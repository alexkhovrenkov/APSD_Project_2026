"""Part D sensitivity test (brief section 1.7.1 b).

Workflow:
  1. The team changes a COPY of the CAD model (here: a separate Part Studio tab) and extracts it.
  2. ``setup`` builds a manifest of that copy, computes which manifest facts differ from the
     baseline manifest (the one used for the initial AI run) and writes an empty prediction form.
  3. The team fills prediction.json - expected d1-d5, reasoning, names, date - BEFORE asking the AI.
  4. ``make_prompt`` refuses to run while the prediction is incomplete; when it runs it stores the
     prediction's SHA-256 and the time, so the record shows the prediction existed first.
  5. The prompt is sent to a NEW chat; the reply is imported with ``import-response`` like any run.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from .ai_handoff import build_chat_prompt, new_run_dir, preflight_problems
from .config import PIPELINE_DIR
from .manifest import build_manifest, write_manifest

COMPARED_FACT_FIELDS = ("value", "status")


def _rel(p: Path) -> str:
    return str(p.relative_to(PIPELINE_DIR)) if p.is_relative_to(PIPELINE_DIR) else str(p)


def _strip_pointers(v):
    """Drop evidence pointers / face ids so only engineering content is compared."""
    if isinstance(v, dict):
        return {k: _strip_pointers(x) for k, x in v.items() if k not in ("evidence", "pointer", "pointers", "face_id", "index")}
    if isinstance(v, list):
        return [_strip_pointers(x) for x in v]
    return v


def diff_manifests(base: dict, test: dict) -> dict:
    changed = {}
    for key in sorted(set(base["facts"]) | set(test["facts"])):
        b, t = base["facts"].get(key), test["facts"].get(key)
        if b is None or t is None:
            changed[key] = {"baseline": b, "test": t}
            continue
        bv = {f: _strip_pointers(b.get(f)) for f in COMPARED_FACT_FIELDS}
        tv = {f: _strip_pointers(t.get(f)) for f in COMPARED_FACT_FIELDS}
        if bv != tv:
            changed[key] = {"baseline": bv, "test": tv}
    bf = [(f["feature_type"], f["feature_id"]) for f in base["feature_inventory"]]
    tf = [(f["feature_type"], f["feature_id"]) for f in test["feature_inventory"]]
    return {
        "baseline": {"element": base["source"]["element_name"], "run": base["extraction"]["run_id"],
                     "microversion": base["model_revision"]["snapshot_microversion"]},
        "test": {"element": test["source"]["element_name"], "run": test["extraction"]["run_id"],
                 "microversion": test["model_revision"]["snapshot_microversion"]},
        "facts_changed": changed,
        "facts_unchanged": sorted(set(base["facts"]) - set(changed)),
        "features_removed": [{"feature_type": a, "feature_id": b} for a, b in bf if (a, b) not in tf],
        "features_added": [{"feature_type": a, "feature_id": b} for a, b in tf if (a, b) not in bf],
        "note": "Computed automatically by comparing fact values/status (evidence pointers ignored). Feature names "
                "are not compared because they are not evidence.",
    }


PREDICTION_TEMPLATE = {
    "record_type": "sensitivity_prediction",
    "instructions": "Fill in BEFORE asking the AI. State the controlled change in your own words, the digits you "
                    "expect the AI to return for the CHANGED part, and why. A small change may legitimately leave "
                    "the code unchanged - then say why.",
    "controlled_change": None,
    "baseline_final_code": None,
    "expected_digits": {"d1": None, "d2": None, "d3": None, "d4": None, "d5": None},
    "expected_effect_reasoning": None,
    "predicted_by": None,
    "predicted_on": None,
}


def setup(test_evidence: Path, baseline_manifest: Path, output_dir: Path, name: str) -> Path:
    base = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    test = build_manifest(test_evidence)
    if test["source"]["element_id"] == base["source"]["element_id"] and \
            test["model_revision"]["snapshot_microversion"] == base["model_revision"]["snapshot_microversion"]:
        raise SystemExit("The test evidence is the same model state as the baseline; nothing was changed.")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = output_dir / "sensitivity" / f"{stamp}_{name}"
    d.mkdir(parents=True, exist_ok=False)
    write_manifest(test, d / "manifest_test.json")
    summary = diff_manifests(base, test)
    summary["baseline_manifest"] = _rel(baseline_manifest)
    summary["test_evidence"] = _rel(test_evidence)
    (d / "change_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "prediction.json").write_text(json.dumps(PREDICTION_TEMPLATE, indent=1) + "\n", encoding="utf-8")
    return d


def make_prompt(sens_dir: Path, output_dir: Path) -> Path:
    pred_path = sens_dir / "prediction.json"
    pred_text = pred_path.read_text(encoding="utf-8")
    pred = json.loads(pred_text)
    missing = [k for k in ("controlled_change", "expected_effect_reasoning", "predicted_by", "predicted_on")
               if not pred.get(k)]
    missing += [f"expected_digits.{p}" for p, v in (pred.get("expected_digits") or {}).items() if not v]
    if missing:
        raise SystemExit("prediction.json is not complete - fill these BEFORE asking the AI: " + ", ".join(missing))
    text, context = build_chat_prompt(sens_dir / "manifest_test.json")
    problems = [p for p in preflight_problems(context)]
    if problems:
        raise SystemExit("Preflight problems: " + "; ".join(problems))
    run_dir = new_run_dir(output_dir, "claude_chat_sensitivity")
    (run_dir / "chat_prompt.txt").write_text(text, encoding="utf-8")
    context.update({
        "sensitivity_dir": _rel(sens_dir),
        "prediction_sha256": hashlib.sha256(pred_text.encode("utf-8")).hexdigest(),
        "prediction_locked_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "prediction_expected_digits": pred["expected_digits"],
        "preflight_deviations_accepted": [],
    })
    (run_dir / "payload_context.json").write_text(json.dumps(context, indent=1) + "\n", encoding="utf-8")
    return run_dir
