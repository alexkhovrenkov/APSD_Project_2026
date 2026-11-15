"""Stage 1 - read-only extraction from the Onshape REST API into sanitized evidence files.

Endpoints (paths verified against https://cad.onshape.com/api/openapi, server "v17"):
  getDocument                 GET /documents/{did}
  getDocumentWorkspaceMicroversion (auth)  GET /documents/d/{did}/w/{wid}/currentmicroversion
  getElementsInDocument       GET /documents/d/{did}/{wvm}/{wvmid}/elements
  getPartStudioFeatures       GET /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features
  getPartsWMVE         (auth) GET /parts/d/{did}/{wvm}/{wvmid}/e/{eid}
  getConfiguration     (auth) GET /elements/d/{did}/{wvm}/{wvmid}/e/{eid}/configuration
  getPartStudioBodyDetails (auth) GET /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/bodydetails
  getPartStudioBoundingBoxes (auth) GET /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/boundingboxes
  getDrawingViews_1    (auth) GET /drawings/d/{did}/{wvm}/{wvmid}/e/{drawing eid}/views
    -> view definitions only (orientation, section views, projection angle). Observed
       2026-11-11: dimension/annotation TEXT is not part of this response, so drawing
       callouts can only enter the manifest as human-verified facts.

"(auth)" = observed to return HTTP 401 without credentials for this document on
2026-11-09, i.e. NOT available through anonymous link sharing.

All geometry calls use the microversion path ``/m/<microversion>`` captured at the
start of the run, so every response describes the same model state. The document
microversion is read again at the end and any change is reported.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import OnshapeCredentials, OnshapeTarget
from .onshape_client import API_VERSION, OnshapeAPIError, OnshapeClient
from .redact import redact_json


def _save(evidence_dir: Path, name: str, data: Any) -> tuple[str, str]:
    path = evidence_dir / f"{name}.json"
    text = json.dumps(redact_json(data), indent=1, ensure_ascii=False, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path.name, hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_extraction(target: OnshapeTarget, creds: OnshapeCredentials, output_dir: Path,
                   client: OnshapeClient | None = None, drawing_element_id: str | None = None) -> Path:
    """Fetch everything available, save sanitized evidence, return the evidence directory."""
    started = dt.datetime.now(dt.timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + f"_{creds.mode}"
    evidence_dir = output_dir / "evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    client = client or OnshapeClient(target, creds)
    did, eid = target.document_id, target.element_id
    results: dict[str, dict[str, Any]] = {}

    def fetch(name: str, path: str, params: dict | None = None) -> Any:
        try:
            data, rec = client.get_json(name, path, params)
        except OnshapeAPIError as exc:
            results[name] = {"status": exc.status, "retrieved": False, "error": str(exc)}
            print(f"  [--] {name}: HTTP {exc.status} (not retrieved)")
            return None
        rec.saved_as, rec.sha256 = _save(evidence_dir, name, data)
        results[name] = {"status": 200, "retrieved": True, "file": rec.saved_as}
        print(f"  [ok] {name}: HTTP 200 -> {rec.saved_as}")
        return data

    print(f"Extracting from {target.host} (auth mode: {creds.mode}, API {API_VERSION})")
    doc = fetch("01_document", f"/documents/{did}")
    if doc is None:
        raise SystemExit("Cannot read the document itself; check the URL, stack host and access. Evidence saved.")

    # ---- pin a microversion snapshot -------------------------------------------------
    snapshot = {"wvm": target.wvm, "wvm_id": target.wvm_id, "source": "URL"}
    if target.wvm == "w":
        mv = fetch("02_workspace_microversion", f"/documents/d/{did}/w/{target.wvm_id}/currentmicroversion")
        if mv and mv.get("microversion"):
            snapshot = {"wvm": "m", "wvm_id": mv["microversion"],
                        "source": "02_workspace_microversion.json#/microversion"}
        elif (doc.get("defaultWorkspace") or {}).get("id") == target.wvm_id and doc["defaultWorkspace"].get("microversion"):
            snapshot = {"wvm": "m", "wvm_id": doc["defaultWorkspace"]["microversion"],
                        "source": "01_document.json#/defaultWorkspace/microversion (URL workspace is the default workspace)"}
        else:
            snapshot["note"] = "No microversion available; workspace path used, revision changes detected by end check only."
    wvm, wvm_id = snapshot["wvm"], snapshot["wvm_id"]
    base = f"/d/{did}/{wvm}/{wvm_id}"

    elements = fetch("03_elements", f"/documents{base}/elements")
    element = next((e for e in (elements or []) if e.get("id") == eid), None)
    element_type = (element or {}).get("elementType")
    if elements is not None and element is None:
        raise SystemExit(f"Element {eid} not found in this document/workspace.")
    if element_type and element_type != "PARTSTUDIO":
        raise SystemExit(f"Element {eid} is a {element_type}, not a PARTSTUDIO. Use the Part Studio tab URL.")

    fetch("04_features", f"/partstudios{base}/e/{eid}/features")
    fetch("05_parts", f"/parts{base}/e/{eid}")
    fetch("06_configuration", f"/elements{base}/e/{eid}/configuration")
    fetch("07_bodydetails", f"/partstudios{base}/e/{eid}/bodydetails")
    fetch("08_boundingboxes", f"/partstudios{base}/e/{eid}/boundingboxes")

    drawing_info = None
    if drawing_element_id:
        drw = next((e for e in (elements or []) if e.get("id") == drawing_element_id), None)
        drawing_info = {"element_id": drawing_element_id, "name": (drw or {}).get("name"),
                        "data_type": (drw or {}).get("dataType"), "element_microversion": (drw or {}).get("microversionId")}
        if drw is None or (drw.get("dataType") or "") != "onshape-app/drawing":
            drawing_info["error"] = "element not found in this document or not a drawing; drawing skipped"
            print(f"  [--] drawing {drawing_element_id}: {drawing_info['error']}")
        else:
            fetch("11_drawing_views", f"/drawings{base}/e/{drawing_element_id}/views")

    # ---- revision check at the end ---------------------------------------------------
    doc_end = fetch("09_document_end", f"/documents/{did}")
    # Observed 2026-11-11: getDocument's defaultWorkspace.microversion can lag behind the
    # workspace's real current microversion, so the authoritative endpoint is preferred.
    mv_end_rec = fetch("10_workspace_microversion_end", f"/documents/d/{did}/w/{target.wvm_id}/currentmicroversion") \
        if target.wvm == "w" and snapshot["source"].startswith("02_") else None
    if mv_end_rec and mv_end_rec.get("microversion"):
        mv_start, mv_end = snapshot["wvm_id"], mv_end_rec["microversion"]
        mv_basis = "getDocumentWorkspaceMicroversion (currentmicroversion) at start and end"
    else:
        mv_start = (doc.get("defaultWorkspace") or {}).get("microversion")
        mv_end = ((doc_end or {}).get("defaultWorkspace") or {}).get("microversion")
        mv_basis = "getDocument defaultWorkspace.microversion at start and end (may lag; observed 2026-11-11)"

    record = {
        "record_type": "extraction_record",
        "run_id": run_id,
        "evidence_class": "LIVE Onshape API responses (sanitized)",
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "auth_mode": creds.mode,
        "api_version": API_VERSION,
        "target": {"host": target.host, "document_id": did, "url_wvm": target.wvm,
                   "url_wvm_id": target.wvm_id, "element_id": eid},
        "element_type": element_type,
        "element_name": (element or {}).get("name"),
        "element_microversion": (element or {}).get("microversionId"),
        "drawing": drawing_info,
        "snapshot": snapshot,
        "configuration_query_parameter": None,
        "configuration_note": "No configuration parameter sent: Onshape evaluates the default configuration.",
        "default_workspace_microversion_start": mv_start,
        "default_workspace_microversion_end": mv_end,
        "revision_check_basis": mv_basis,
        "revision_changed_during_extraction": (mv_start != mv_end) if (mv_start and mv_end) else None,
        "endpoints": results,
        "request_log": [r.to_dict() for r in client.log],
        "redaction": "Authorization/On-Nonce/cookies never stored; user names, ids, e-mails and avatars "
                     "in Onshape user objects replaced by <REDACTED>. Geometry and feature data unchanged.",
    }
    (evidence_dir / "extraction_record.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    if record["revision_changed_during_extraction"]:
        print("  WARNING: the document changed during extraction; geometry calls used the start snapshot.")
    print(f"Evidence saved to {evidence_dir}")
    return evidence_dir
