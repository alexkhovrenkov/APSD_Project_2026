"""Stage 2 - build and validate manifest.json from saved evidence files.

The manifest is the ONLY data the AI stage reads about the part. Every fact has:
  value, unit, status, provenance, evidence (file#json-pointer), confidence + reason.

Source priority: evaluated B-rep geometry (07_bodydetails.json) when it was retrieved,
otherwise the parametric feature list (04_features.json). When both exist, the
feature-list value is used as a cross-check and the (dis)agreement is stated.

Status vocabulary (UNKNOWN convention):
  known          value supported by the cited evidence
  unknown        value is null: evidence is insufficient to decide (reason given)
  not_retrieved  value is null: the data source was not accessed / access was denied
  absent         affirmative evidence of absence WITHIN the stated scope only
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import jsonschema

from . import __version__
from .config import PIPELINE_DIR
from .feature_parser import parse_features
from .geometry_parser import locate_freeform_faces, parse_bodydetails

SCHEMA_PATH = PIPELINE_DIR / "schemas" / "feature_manifest.schema.json"
HUMAN_INPUTS_PATH = PIPELINE_DIR / "human_inputs" / "drawing_supplement.json"

UNKNOWN_CONVENTION = {
    "representation": "value = null together with status 'unknown' or 'not_retrieved' and a 'reason'",
    "unknown": "evidence insufficient to decide",
    "not_retrieved": "data source not accessed or access denied (e.g. HTTP 401)",
    "absent": "complete evidence within 'scope' shows no such feature; not a statement beyond that scope",
}


def fact(value: Any, unit: str | None, status: str, provenance: str, evidence: list[dict], confidence: str,
         confidence_reason: str, **extra: Any) -> dict:
    out = {"value": value, "unit": unit, "status": status, "provenance": provenance, "evidence": evidence,
           "confidence": confidence, "confidence_reason": confidence_reason}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def unknown(reason: str, status: str = "unknown", unit: str | None = None, evidence: list | None = None) -> dict:
    return fact(None, unit, status, "none", evidence or [], "none", "no supporting evidence", reason=reason)


def absent(scope: str, reason: str, evidence: list[dict], confidence: str = "medium",
           provenance: str = "api_reported") -> dict:
    why = ("evaluated geometry checked" if scope.startswith("evaluated_geometry")
           else "complete feature list checked; scope limited to the CAD model")
    return fact(False, None, "absent", provenance, evidence, confidence, why, scope=scope, reason=reason)


def ev(ref: str, note: str = "", source: str = "onshape_api") -> dict:
    d = {"source": source, "ref": ref}
    if note:
        d["note"] = note
    return d


def _load(evidence_dir: Path, name: str) -> Any | None:
    p = evidence_dir / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _agree(a: float | None, b: float | None, tol: float = 1e-3) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


# ---------------------------------------------------------------------------------------
def build_manifest(evidence_dir: Path, evidence_class: str = "LIVE_ONSHAPE_API") -> dict:
    rec = _load(evidence_dir, "extraction_record") or {}
    doc = _load(evidence_dir, "01_document") or {}
    features = _load(evidence_dir, "04_features")
    parts = _load(evidence_dir, "05_parts")
    config = _load(evidence_dir, "06_configuration")
    body = _load(evidence_dir, "07_bodydetails")
    endpoints = rec.get("endpoints", {})
    if features is None:
        raise SystemExit("04_features.json missing: the feature list is required to build a manifest.")

    fo = parse_features(features)
    target_part = None
    if isinstance(parts, list):
        solids = [p for p in parts if str(p.get("bodyType", "solid")).lower() == "solid" and not p.get("isHidden")]
        target_part = solids[0] if len(solids) == 1 else None
    go = parse_bodydetails(body, (target_part or {}).get("partId")) if body else None
    G = bool(go and "error" not in go and go.get("axial_length_mm"))
    FL = fo["file"] + "#/features"

    facts: dict[str, dict] = {}
    rv = next((r for r in fo["revolves"] if r.get("profile")), None)
    prof = rv["profile"] if rv else None
    add_feats = fo["additive_extrudes"]
    limitations: list[str] = []

    # ---- rotation axis ------------------------------------------------------------------
    if G:
        ra = go["rotation_axis"]
        cross = (f"; feature list: revolve {rv['feature_id']} about sketch line {rv['axis_entity_id']}" if rv else "")
        facts["rotation_axis"] = fact(
            {"origin_mm": ra["origin_mm"], "direction": ra["direction"], "frame": "Part Studio world coordinates",
             "feature_tree_definition": rv and {"revolve": rv["feature_id"], "axis_sketch_entity": rv["axis_entity_id"]}},
            None, "known", "geometrically_derived", [ev(p) for p in ra["face_pointers"][:6]], "high", ra["basis"] + cross)
    elif rv:
        facts["rotation_axis"] = fact(
            {"defined_by": f"revolve {rv['feature_id']} (full_revolve={rv['full_revolve']}) about sketch line "
                           f"{rv['axis_entity_id']} of sketch {rv['profile_sketch_id']}",
             "axis_in_sketch_coordinates": rv["axis_in_sketch"],
             "profile_sketch_plane": (rv.get("profile_sketch_plane") or {}).get("default_plane"),
             "world_direction": None, "world_direction_note": "requires evaluated geometry (not retrieved)"},
            None, "known", "api_reported",
            [ev(rv["axis_pointer"], "revolve axis mate connector placed on this sketch line"),
             ev(rv["axis_in_sketch"]["entity_pointer"], "axis line geometry")],
            "medium", "axis reference decoded from an undocumented qCompressed query string")
    else:
        facts["rotation_axis"] = unknown("no revolve with a resolvable axis and no evaluated geometry")

    # ---- length / diameter / ratio -------------------------------------------------------
    p_len = prof["axial_extent_mm"] if prof else None
    p_dia = prof["max_outer_diameter_mm"] if prof else None
    if G:
        L, D = go["axial_length_mm"], go["max_coaxial_cylinder_diameter_mm"]
        xl = (f"; feature-list profile gives {p_len:g} mm ({'agrees' if _agree(L, p_len) else 'DISAGREES'})" if prof else "")
        facts["overall_axial_length"] = fact(
            L, "mm", "known", "geometrically_derived",
            [ev(f"07_bodydetails.json#/bodies/{go['body_index']}/faces", "face bounding boxes projected on the axis")],
            "high" if go.get("axial_length_exact") and (not prof or _agree(L, p_len)) else "medium",
            go["axial_length_basis"] + "; metres x 1000 (documented unit)" + xl,
            original={"unit": "m", "range_m": [x / 1000 for x in go["axial_range_mm"]], "conversion": "m x 1000 = mm"})
        dface = next((c for c in go["external_cylinders"] if c["diameter_mm"] == D), None)
        xd = (f"; feature-list profile gives {p_dia:g} mm ({'agrees' if _agree(D, p_dia) else 'DISAGREES'})" if prof else "")
        facts["max_relevant_diameter"] = fact(
            D, "mm", "known", "geometrically_derived", [ev(dface["pointer"], "largest external coaxial cylinder")] if dface else [],
            "high" if (not prof or _agree(D, p_dia)) else "medium",
            "largest external cylinder coaxial with the rotation axis (radius x 2)" + xd)
    elif prof:
        L = p_len
        axis_dim = next((c for c in rv["dimension_constraints"] if c["type"] == "LENGTH" and _agree(_expr_mm(c["expression"]), L)), None)
        evid = [ev(s["pointer"], f"profile segment axial {s['axial_mm']} mm") for s in prof["segments"]
                if s["kind"] == "perpendicular_to_axis" and s["axial_mm"][0] in (prof["axial_min_mm"], prof["axial_max_mm"])]
        if axis_dim:
            evid.append(ev(axis_dim["pointer"], f"sketch dimension {axis_dim['expression']}"))
        conf, caveat = ("medium", f"; {len(add_feats)} additive extrude(s) with non-numeric end bound could change the "
                                  "extent - confirm with bodydetails") if add_feats else ("high", "")
        facts["overall_axial_length"] = fact(
            L, "mm", "known", "geometrically_derived", evid, conf,
            "axial extent of the revolve profile; sketch units verified by " + rv["sketch_units"]["agreement"] + caveat,
            original={"unit": "m (sketch geometry)", "conversion": f"{L / 1000:g} m x 1000 = {L:g} mm"})
        dseg = next(s for s in prof["outer_cylinders"] if s["diameter_mm"] == p_dia)
        facts["max_relevant_diameter"] = fact(p_dia, "mm", "known", "geometrically_derived",
                                              [ev(dseg["pointer"], f"radius {dseg['radial_mm'][0]} mm from axis")],
                                              conf, "largest outer radius of the revolve profile x 2")
    else:
        facts["overall_axial_length"] = unknown("no revolve profile or evaluated geometry", unit="mm")
        facts["max_relevant_diameter"] = unknown("no revolve profile or evaluated geometry", unit="mm")

    Lf, Df = facts["overall_axial_length"], facts["max_relevant_diameter"]
    if Lf["status"] == "known" and Df["status"] == "known":
        facts["length_to_diameter_ratio"] = fact(
            round(Lf["value"] / Df["value"], 4), None, "known", "geometrically_derived",
            [ev("manifest.json#/facts/overall_axial_length", source="derived"),
             ev("manifest.json#/facts/max_relevant_diameter", source="derived")],
            "high" if (Lf["confidence"], Df["confidence"]) == ("high", "high") else "medium",
            f"L/D = {Lf['value']:g} / {Df['value']:g}; confidence = lower of the two inputs")
    else:
        facts["length_to_diameter_ratio"] = unknown("L or D unknown")

    # ---- external profile (sequence of outer diameters along the axis) -------------------------
    if G and go["external_cylinders"]:
        outer = [{"axial_from_mm": c["axial_span_mm"][0], "axial_to_mm": c["axial_span_mm"][1],
                  "diameter_mm": c["diameter_mm"], "pointer": c["pointer"]} for c in go["external_cylinders"] if c["axial_span_mm"]]
        src, src_prov, src_conf = "external coaxial cylinders of the evaluated B-rep (spans shortened by fillets)", "geometrically_derived", "high"
    elif prof:
        outer = [{"axial_from_mm": s["axial_mm"][0], "axial_to_mm": s["axial_mm"][1], "diameter_mm": s["diameter_mm"],
                  "pointer": s["pointer"]} for s in prof["outer_cylinders"]]
        src, src_prov, src_conf = "outer-envelope lines of the revolve profile sketch", "geometrically_derived", "high"
    else:
        outer = []
    if outer:
        seq = [{k: o[k] for k in ("axial_from_mm", "axial_to_mm", "diameter_mm")} | {"length_mm": round(o["axial_to_mm"] - o["axial_from_mm"], 4)} for o in outer]
        facts["principal_diameters"] = fact(seq, "mm", "known", src_prov, [ev(o["pointer"]) for o in outer], src_conf,
                                            src + "; axial 0 = end where the axis coordinate is smallest")
        dias = [o["diameter_mm"] for o in outer]
        imax = dias.index(max(dias))
        facts["external_steps"] = fact(
            {"diameter_sequence_from_axial_0_mm": dias,
             "diameter_changes": sum(1 for a, b in zip(dias, dias[1:]) if abs(a - b) > 1e-6),
             "max_diameter_position": "interior" if 0 < imax < len(dias) - 1 else ("axial_0_end" if imax == 0 else "axial_max_end"),
             "diameter_changes_toward_axial_0": sum(1 for a, b in zip(dias[:imax], dias[1:imax + 1]) if abs(a - b) > 1e-6),
             "diameter_changes_toward_axial_max": sum(1 for a, b in zip(dias[imax:], dias[imax + 1:]) if abs(a - b) > 1e-6),
             "monotonic_from_one_end": dias == sorted(dias) or dias == sorted(dias, reverse=True)},
            None, "known", src_prov, [ev(o["pointer"]) for o in outer], src_conf, "counted from " + src)
        fl = outer[imax]
        neighbours = [dias[j] for j in (imax - 1, imax + 1) if 0 <= j < len(dias)]
        facts["flange"] = fact(
            {"diameter_mm": fl["diameter_mm"], "axial_from_mm": fl["axial_from_mm"], "axial_to_mm": fl["axial_to_mm"],
             "length_mm": round(fl["axial_to_mm"] - fl["axial_from_mm"], 4), "adjacent_diameters_mm": neighbours,
             "label_note": "'flange' = descriptive label for the maximum-diameter section; no CAD feature is named so"},
            "mm", "known", src_prov, [ev(fl["pointer"])], src_conf,
            "maximum-diameter section with smaller diameters on both sides" if len(neighbours) == 2
            else "maximum-diameter section at one end")
        recess = [i for i in range(1, len(dias) - 1) if dias[i] < dias[i - 1] and dias[i] < dias[i + 1]]
        if recess:
            facts["grooves"] = fact([seq[i] for i in recess], "mm", "known", src_prov, [ev(outer[i]["pointer"]) for i in recess],
                                    "medium", "local minimum of the outer diameter sequence (groove or recess candidate)")
        else:
            facts["grooves"] = absent("evaluated_geometry" if G else "cad_model_feature_list + revolve profile",
                                      "the outer diameter sequence has no local minimum between larger diameters",
                                      [ev(o["pointer"]) for o in outer][:4], "high" if G else "medium",
                                      "geometrically_derived")
    else:
        for k in ("principal_diameters", "external_steps", "flange", "grooves"):
            facts[k] = unknown("no revolve profile or evaluated geometry")

    # ---- internal geometry ---------------------------------------------------------------
    internal: list[dict] = []
    if G and go["internal_cylinders"]:
        for c in go["internal_cylinders"]:
            internal.append({"kind": "internal coaxial cylinder (bore)", "diameter_mm": c["diameter_mm"],
                             "axial_from_mm": c["axial_span_mm"][0], "axial_to_mm": c["axial_span_mm"][1],
                             "length_mm": round(c["axial_span_mm"][1] - c["axial_span_mm"][0], 4),
                             "opens_at_end": [a for a in c["axial_span_mm"] if a in (0.0, Lf["value"])],
                             "evidence": [c["pointer"]]})
        spans = sorted((i["axial_from_mm"], i["axial_to_mm"]) for i in internal)
        reach = 0.0
        for a, b in spans:
            if a <= reach + 1e-6:
                reach = max(reach, b)
        through = reach >= Lf["value"] - 1e-6
        basis_int = "internal coaxial cylinders of the evaluated B-rep (face orientation: " + go["orientation_note"] + ")"
        int_conf = "high"
    elif prof:
        for s in prof["inner_cylinders"]:
            internal.append({"kind": "bore/recess in revolve profile", "diameter_mm": s["diameter_mm"],
                             "axial_from_mm": s["axial_mm"][0], "axial_to_mm": s["axial_mm"][1],
                             "length_mm": round(s["axial_mm"][1] - s["axial_mm"][0], 4), "evidence": [s["pointer"]]})
        for c in fo["circle_cuts"]:
            if c["circles"]["count"] == 1 and c["circles"]["concentric_with_sketch_origin"]:
                internal.append({"kind": "single-circle cut (coaxiality probable, not proven)",
                                 "diameter_mm": c["circles"]["distinct_diameters_mm"][0], "end_bound": c["end_bound"],
                                 "evidence": [c["pointer"]]})
        through = None
        basis_int = "revolve profile + cut features (no evaluated geometry)"
        int_conf = "medium"
    for h in fo["holes"]:
        tap = h["lookup_table"]["value"] or {}
        tapped = str(tap.get("holeType", "")).lower() == "tapped"
        internal.append({"kind": f"Hole feature ({h['style']}, {tap.get('holeType') or 'custom / no thread standard'})",
                         **({"tap_drill_diameter_mm": (h["tap_drill_diameter"] or {}).get("value_mm")} if tapped else
                            {"hole_diameter_parameter_mm": (h["hole_diameter"] or {}).get("value_mm")}),
                         "hole_depth_mm": (h["hole_depth"] or {}).get("value_mm"), "end_style": h["end_style"],
                         "note": "feature parameters as entered; the evaluated geometry above is authoritative for diameters",
                         "evidence": [h["pointer"]]})
    if internal:
        facts["bores_and_counterbores"] = fact(
            {"items": internal, "continuous_axial_opening_end_to_end": through,
             "opening_basis": "union of internal coaxial cylinder spans" if through is not None else "not determinable without evaluated geometry"},
            "mm", "known", "geometrically_derived", [ev(p) for it in internal for p in it["evidence"]][:12], int_conf, basis_int)
    else:
        facts["bores_and_counterbores"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                                 "no internal cylinder, cut or hole feature", [ev(FL)])

    # ---- threads -------------------------------------------------------------------------
    # Sources: tapped Hole features, thread features (standard externalThread / custom
    # threadCreator), cosmetic thread metadata and non-analytic (helical) faces in the B-rep.
    geo_threads = (go or {}).get("cosmetic_threads", []) if G else []
    freeform = locate_freeform_faces(body, go) if G else []

    def thread_feature_item(t: dict) -> dict:
        if t["feature_type"] == "threadCreator":
            return {"source": "threadCreator feature (custom FeatureScript, modelled thread)",
                    "pitch_mm": (t["pitch"] or {}).get("value_mm"), "pitch_expression": (t["pitch"] or {}).get("original_expression"),
                    "label_as_reported": t["label_as_reported"], "profile_as_reported": t["profile"],
                    "major_diameter_parameter_mm": (t["major_diameter_parameter"] or {}).get("value_mm"),
                    "length_type": t["length_type"], "left_handed": t["left_handed"], "status": t["status"],
                    "evidence": [t["pointer"], t["kind_pointer"], (t["pitch"] or {}).get("pointer")]}
        return {"source": f"{t['feature_type']} feature", "standard_as_reported": t.get("standard_as_reported"),
                "depth_type": t.get("depth_type"), "status": t["status"], "evidence": [t["pointer"], t.get("standard_pointer")]}

    def kind_of(t: dict) -> str:
        if t["feature_type"] == "threadCreator":
            return t.get("kind") or "unknown"
        return "internal" if t["feature_type"] == "internalThread" else "external"

    def helical(role: str) -> list[dict]:
        return [{"source": "evaluated geometry: non-analytic (helical) faces located by loose bounding boxes",
                 "axial_span_mm_approx": f_["axial_span_clipped_mm"], "near_diameters_mm": f_["near_diameters_mm"],
                 "evidence": [f_["pointer"]]} for f_ in freeform if f_["role_heuristic"] == role]

    def consistency(items: list[dict], role: str) -> list[str]:
        notes = []
        size = next((e["standard_as_reported"].get("size") for e in items if isinstance(e.get("standard_as_reported"), dict)), None)
        cyl = next((e.get("cylinder_diameter_mm") for e in items if e.get("cylinder_diameter_mm")), None)
        m = re.match(r"^M(\d+(?:\.\d+)?)", str(size or ""))
        if m and cyl and abs(float(m.group(1)) - cyl) > 1e-6:
            notes.append(f"designation {size} (nominal {m.group(1)} mm) differs from the threaded cylinder diameter {cyl:g} mm "
                         "in the model - human check needed")
        near = sorted({d for e in items for d in e.get("near_diameters_mm", [])})
        for e in items:
            md = e.get("major_diameter_parameter_mm")
            if md is not None and near and all(abs(md - d) > 1e-6 for d in near):
                notes.append(f"threadCreator parameter majorDiameter = {md:g} mm matches none of the diameters at the "
                             f"threaded region {near} mm; this third-party parameter is reported but not interpreted")
        return list(dict.fromkeys(notes))

    int_threads = []
    for h in fo["holes"]:
        tap = h["lookup_table"]["value"] or {}
        if str(tap.get("holeType", "")).lower() == "tapped":
            int_threads.append({"source": "Hole feature (cosmetic thread attribute)", "designation": tap.get("size"),
                                "pitch_as_reported": tap.get("pitch"),
                                "major_diameter_mm": (h["major_diameter"] or {}).get("value_mm"),
                                "tapped_depth_mm": (h["tapped_depth"] or {}).get("value_mm"),
                                "evidence": [h["lookup_table"]["pointer"], (h["major_diameter"] or {}).get("pointer")]})
    int_threads += [thread_feature_item(t) for t in fo["thread_features"] if kind_of(t) == "internal"]
    int_threads += [{"source": "evaluated geometry (cosmetic thread on internal face)", **{k: t[k] for k in (
        "starts_at_axial_mm", "runs_toward", "pitch_mm", "thread_length_mm", "cylinder_diameter_mm")}, "evidence": [t["pointer"]]}
        for t in geo_threads if t.get("role") == "internal"]
    int_threads += helical("internal")
    if int_threads:
        notes = consistency(int_threads, "internal")
        facts["internal_threads"] = fact(
            {"items": int_threads, "consistency_notes": notes}, "mm", "known", "api_reported",
            [ev(p) for t in int_threads for p in t["evidence"] if p][:12], "medium" if notes else "high",
            "thread features and/or evaluated geometry; see each item's source"
            + ("; inconsistencies recorded in consistency_notes" if notes else ""))
    else:
        facts["internal_threads"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                           "no tapped Hole feature, thread feature, cosmetic thread or internal helical face", [ev(FL)])

    ext = [thread_feature_item(t) for t in fo["thread_features"] if kind_of(t) == "external"]
    ext += [{"source": "evaluated geometry (cosmetic thread on external face)", **{k: t[k] for k in (
        "starts_at_axial_mm", "runs_toward", "pitch_mm", "thread_length_mm", "cylinder_diameter_mm")}, "evidence": [t["pointer"]]}
        for t in geo_threads if t.get("role") == "external"]
    ext += helical("external")
    if ext:
        notes = consistency(ext, "external")
        facts["external_thread_in_cad_model"] = fact(
            {"items": ext, "consistency_notes": notes}, "mm", "known", "api_reported",
            [ev(p) for e in ext for p in e["evidence"] if p][:12], "medium" if notes else "high",
            "thread features and/or evaluated geometry; see each item's source"
            + ("; inconsistencies recorded in consistency_notes" if notes else ""))
    else:
        facts["external_thread_in_cad_model"] = absent(
            "cad_model_feature_list", "no thread feature, external cosmetic thread or external helical face", [ev(FL)])
    _verified_thread = [i for i, h in enumerate(_verified_human_facts()) if h["fact"] == "external_thread_on_physical_part"]
    facts["external_thread_on_physical_part"] = unknown(
        "not derivable from the API data: the physical requirement is defined by the drawing and CAD data show design "
        "intent only (see external_thread_in_cad_model). "
        + (f"A human-verified drawing callout is given in human_supplied_facts[{_verified_thread[0]}]."
           if _verified_thread else "No drawing callout has been human-verified."))

    unclassified = [f_ for f_ in freeform if f_["role_heuristic"] is None]
    if unclassified:
        limitations.append(f"{len(unclassified)} non-analytic face(s) could not be assigned to an internal or external thread region.")

    # ---- radii, chamfers ---------------------------------------------------------------------
    if G and go["blends"]:
        bl = [{"radius_mm": c["minor_radius_mm"], "type": c["role"], "axial_span_mm": c["axial_span_mm"],
               "between_diameters_mm": _neighbours(outer, c["axial_span_mm"])} for c in go["blends"]]
        facts["radii_fillets"] = fact(bl, "mm", "known", "geometrically_derived", [ev(c["pointer"]) for c in go["blends"]], "high",
                                      "toroidal faces coaxial with the axis (minor radius = fillet radius)"
                                      + (f"; fillet feature reports {fo['fillets'][0]['size']['value_mm']:g} mm"
                                         if fo["fillets"] and fo["fillets"][0].get("size") else ""))
    elif fo["fillets"]:
        facts["radii_fillets"] = fact([{"radius_mm": (f["size"] or {}).get("value_mm"), "edge_count": f["edge_query_count"]}
                                       for f in fo["fillets"]], "mm", "known", "api_reported", [ev(f["pointer"]) for f in fo["fillets"]],
                                      "high", "fillet feature radius and edge count (edge locations not reported)")
    else:
        facts["radii_fillets"] = absent("cad_model_feature_list", "no fillet feature", [ev(FL)])
    cones = [c for c in (go or {}).get("coaxial_faces", []) if c["type"] == "CONE"] if G else []
    if fo["chamfers"] or cones:
        facts["chamfers"] = fact({"features": [{"width_mm": (c["size"] or {}).get("value_mm")} for c in fo["chamfers"]],
                                  "conical_faces": cones}, "mm", "known", "api_reported",
                                 [ev(c["pointer"]) for c in fo["chamfers"]] + [ev(c["pointer"]) for c in cones], "high",
                                 "chamfer features / conical faces")
    else:
        facts["chamfers"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                   "no chamfer feature and no conical face", [ev(FL)], "high" if G else "medium")

    # ---- off-axis holes ------------------------------------------------------------------------
    cut_info = [{"feature": c["feature_id"], "end_bound": c["end_bound"], "depth_mm": (c.get("depth") or {}).get("value_mm"),
                 "pitch_circle": c["circles"]["pitch_circle"], "pointer": c["pointer"]}
                for c in fo["circle_cuts"] if c["circles"]["pitch_circle"]]
    if G and go["axial_off_axis_cylinders"]:
        pats = [{"hole_diameter_mm": p["hole_diameter_mm"], "count_of_cylinder_faces": p["cylinder_faces"],
                 "pitch_circle_diameter_mm": p["pitch_circle_diameter_mm"], "angular_positions_deg": p["angular_positions_deg"],
                 "equally_spaced": p["equally_spaced"], "direction": "parallel to the rotation axis",
                 "pitch_circle_centred_on_axis": True, "evidence": p["pointers"][:3]} for p in go["axial_off_axis_cylinders"]]
        facts["axial_holes"] = fact({"patterns": pats, "feature_list": cut_info}, "mm", "known", "geometrically_derived",
                                    [ev(p) for x in pats for p in x["evidence"]] + [ev(c["pointer"]) for c in cut_info], "high",
                                    "off-axis cylinders parallel to the axis in evaluated geometry; PCD measured from the axis line; "
                                    "depth from the cut feature")
    elif cut_info:
        facts["axial_holes"] = fact({"feature_list": cut_info}, "mm", "known", "geometrically_derived",
                                    [ev(c["pointer"]) for c in cut_info], "medium",
                                    "sketch circles of a cut normal to the axis; centring on the axis not proven")
    else:
        facts["axial_holes"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                      "no off-axis cylinder parallel to the axis", [ev(FL)], "high" if G else "medium")
    if G and go["radial_cylinders"]:
        facts["radial_holes"] = fact(go["radial_cylinders"], "mm", "known", "geometrically_derived",
                                     [ev(r["pointer"]) for r in go["radial_cylinders"]], "high", "cylinders perpendicular to the axis")
    else:
        facts["radial_holes"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                       "no cylinder perpendicular to the axis" if G else "no cut sketched on a plane containing the axis",
                                       [ev(FL)], "high" if G else "medium", "geometrically_derived" if G else "api_reported")

    # ---- plane surfaces --------------------------------------------------------------------------
    feat_planes = [{"feature": a["feature_id"], "operation": "ADD (extrude)", "end_bound": a["end_bound"],
                    "dimensions_as_entered": [d["expression"] for d in a["dimension_constraints"]], "pointer": a["pointer"]}
                   for a in add_feats]
    if G and go["planes_parallel_to_axis"]:
        pl = [{"face_id": p["face_id"], "distance_from_axis_mm": p["distance_from_axis_mm"], "axial_span_mm": p.get("axial_span_mm"),
               "area_mm2": p["area_mm2"], "on_diameter_mm": _diameter_at(outer, p.get("axial_span_mm"))} for p in go["planes_parallel_to_axis"]]
        facts["plane_surface_features"] = fact(
            {"planar_faces_parallel_to_axis": pl, "creating_features": feat_planes,
             "description": "planar faces parallel to the rotation axis (not end faces or shoulders)"},
            "mm", "known", "geometrically_derived", [ev(p["pointer"]) for p in go["planes_parallel_to_axis"]] + [ev(f["pointer"]) for f in feat_planes],
            "high", "plane normals perpendicular to the axis in evaluated geometry")
    elif feat_planes:
        facts["plane_surface_features"] = fact({"creating_features": feat_planes}, "mm", "known", "geometrically_derived",
                                               [ev(f["pointer"]) for f in feat_planes], "medium",
                                               "straight-line extrude profile -> planar faces; extent needs evaluated geometry")
    else:
        facts["plane_surface_features"] = absent("evaluated_geometry" if G else "cad_model_feature_list",
                                                 "no planar face parallel to the axis", [ev(FL)], "high" if G else "medium")
    facts["gear_teeth"] = absent("cad_model_feature_list", "no gear, spline or tooth-pattern feature", [ev(FL)])

    # ---- manufacturing data (never inferred from geometry) ----------------------------------------
    mat = (go or {}).get("material") if G else None
    if not mat and target_part:
        mat = (target_part.get("material") or {}).get("displayName") if isinstance(target_part.get("material"), dict) else None
    if mat:
        facts["material"] = fact(mat, None, "known", "api_reported", [ev("05_parts.json")], "medium",
                                 "material property assigned in Onshape; not a verified manufacturing specification")
    elif parts is not None:
        facts["material"] = unknown("no material is assigned to the part in Onshape (05_parts.json material = null)",
                                    evidence=[ev("05_parts.json#/0/material")])
    else:
        facts["material"] = unknown(f"parts endpoint not retrieved (HTTP {endpoints.get('05_parts', {}).get('status')})",
                                    status="not_retrieved")
    facts["stock_form"] = unknown("initial stock form is a manufacturing decision; not encoded in CAD data")
    facts["tolerances"] = unknown("no tolerance data in the retrieved CAD data (feature tolerance types are NONE)")
    facts["surface_finish"] = unknown("not encoded in retrieved CAD data")

    flagged = [x for x in fo["feature_inventory"] if x["status"] not in (None, "OK")]
    facts["suppressed_or_failed_features"] = fact(
        {"excluded_from_geometry": fo["excluded_features"], "non_ok_status": flagged,
         "feature_list_complete": fo["is_complete"], "microversion_skew": fo["microversion_skew"]},
        None, "known", "api_reported", [ev(fo["file"] + "#/featureStates")], "high",
        "featureStates and suppressed flags reported for every feature")

    # ---- part / configuration ---------------------------------------------------------------
    if isinstance(parts, list):
        part_fact = fact({"part_count": len(parts), "part_id": (target_part or {}).get("partId"),
                          "part_name": (target_part or {}).get("name")}, None,
                         "known" if target_part else "unknown", "api_reported" if target_part else "none",
                         [ev("05_parts.json")], "high" if target_part else "none",
                         "single visible solid part" if target_part else "several parts; target not chosen automatically",
                         **({} if target_part else {"reason": "ambiguous target part"}))
    else:
        part_fact = unknown(f"parts endpoint not retrieved (HTTP {endpoints.get('05_parts', {}).get('status')})",
                            status="not_retrieved")
    if isinstance(config, dict):
        cfg_fact = fact({"configuration_parameters": len(config.get("configurationParameters") or []),
                         "evaluated": "default (no configuration query sent)"}, None, "known", "api_reported",
                        [ev("06_configuration.json#/configurationParameters")], "high", "configuration definition retrieved")
    else:
        cfg_fact = fact({"evaluated": "default configuration (no configuration query parameter was sent)",
                         "configuration_parameters": None}, None, "known", "api_reported",
                        [ev("extraction_record.json#/configuration_note")], "medium",
                        f"configuration definition not retrieved (HTTP {endpoints.get('06_configuration', {}).get('status')})")

    # ---- human supplements (only verified entries) -------------------------------------------
    human = _verified_human_facts()

    # ---- limitations ---------------------------------------------------------------------
    if G:
        limitations.append("Evaluated-geometry facts use face 'orientation' (undocumented) to separate internal from external "
                           "faces: " + go["orientation_note"] + ".")
    else:
        limitations.append("Evaluated geometry (bodydetails) was not retrieved: dimensions come from the parametric feature list.")
    limitations += [
        "Feature names are reported as entered but never used as geometric evidence.",
        "Thread data come from feature parameters (Hole, thread features, custom threadCreator) and, where modelled, helical "
        "faces located only approximately (their bounding boxes are not tight); thread form, class and tolerance are not measured.",
        "Some feature-list references (revolve axis line, fillet edges, profile sketches, sketch planes) are decoded from "
        "Onshape's undocumented qCompressed query text.",
        "Sketch-geometry units are undocumented; inferred by cross-checking dimension constraints: "
        + fo["sketch_units_document"]["agreement"] + ".",
        "Axial coordinates start at the end with the smallest axis coordinate; mapping to drawing left/right is a human check.",
        "Material, stock, tolerances and surface finish are not derivable from geometry.",
    ]
    if rec.get("auth_mode") == "anonymous":
        limitations.append("Extraction used anonymous link-share access: endpoints requiring authentication returned 401.")

    unknowns = [{"fact": k, "status": v["status"], "reason": v.get("reason", "")}
                for k, v in {**facts, "part": part_fact}.items() if v["status"] in ("unknown", "not_retrieved")]

    snap = rec.get("snapshot", {})
    return {
        "schema": "apsd-lathe-feature-manifest", "schema_version": "1.0",
        "course_template_status": "Course template templates/feature_manifest.json (brief section 1.6.2) was not "
                                  "available; project schema schemas/feature_manifest.schema.json is used instead.",
        "unknown_convention": UNKNOWN_CONVENTION,
        "evidence_class": evidence_class,
        "part": "APSD-LATHE",
        "units": {"length": "mm", "angle": "deg", "source_units": [
            {"source": "feature parameter expressions", "unit": "as written in each expression",
             "basis": "observed: quantity 'value' fields are 0.0 with empty 'units'; expression text carries the unit"},
            {"source": "sketch geometry (pntX, radius, ...)", "unit": fo["sketch_units_document"]["unit"],
             "basis": "inferred: " + fo["sketch_units_document"]["agreement"]},
            {"source": "bodydetails coordinates", "unit": "m",
             "basis": "documented: 'All coordinates are in meters (m).' (getBodyDetails, Onshape OpenAPI)"}]},
        "source": {
            "geometry_source": "Onshape REST API (Path B): " + ("evaluated B-rep (bodydetails) + feature list cross-check" if G
                                                               else "parametric feature list only"),
            "host": rec.get("target", {}).get("host", ""), "api_version": rec.get("api_version", ""),
            "auth_mode": rec.get("auth_mode", "synthetic"),
            "document_id": rec.get("target", {}).get("document_id", ""), "document_name": doc.get("name"),
            "url_context": {"wvm": rec.get("target", {}).get("url_wvm"), "id": rec.get("target", {}).get("url_wvm_id")},
            "element_id": rec.get("target", {}).get("element_id", ""), "element_type": rec.get("element_type"),
            "element_name": rec.get("element_name"), "part": part_fact, "configuration": cfg_fact,
            "drawing": _drawing_summary(rec, evidence_dir)},
        "model_revision": {
            "snapshot_microversion": snap.get("wvm_id") if snap.get("wvm") == "m" else None,
            "snapshot_source": snap.get("source", "unknown") + ("; " + snap["note"] if snap.get("note") else ""),
            "element_microversion": rec.get("element_microversion"),
            "feature_list_source_microversion": fo["source_microversion"],
            "default_workspace_microversion_start": rec.get("default_workspace_microversion_start"),
            "default_workspace_microversion_end": rec.get("default_workspace_microversion_end"),
            "revision_changed_during_extraction": rec.get("revision_changed_during_extraction"),
            "revision_check_basis": rec.get("revision_check_basis", "getDocument defaultWorkspace.microversion")},
        "extraction": {"run_id": rec.get("run_id", evidence_dir.name), "timestamp_utc": rec.get("started_utc", ""),
                       "evidence_dir": str(evidence_dir.relative_to(PIPELINE_DIR)) if evidence_dir.is_relative_to(PIPELINE_DIR) else str(evidence_dir),
                       "endpoints": endpoints, "pipeline_version": __version__,
                       "manifest_built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        "facts": facts,
        "human_supplied_facts": human,
        "feature_inventory": fo["feature_inventory"],
        "unknowns": unknowns,
        "limitations": limitations,
    }


def _verified_human_facts() -> list[dict]:
    human = []
    if HUMAN_INPUTS_PATH.exists():
        for h in json.loads(HUMAN_INPUTS_PATH.read_text(encoding="utf-8")).get("entries", []):
            if h.get("verified") is True and h.get("verified_by") and h.get("verified_on"):
                human.append({"fact": h["fact"], "value": h["value"], "unit": h.get("unit"), "provenance": "human_supplied",
                              "source": h["source"], "verified_by": h["verified_by"], "verified_on": h["verified_on"],
                              "note": h.get("note", "")})
    return human


def _drawing_summary(rec: dict, evidence_dir: Path) -> dict | None:
    info = rec.get("drawing")
    if not info:
        return None
    views = _load(evidence_dir, "11_drawing_views")
    items = (views or {}).get("items", []) if isinstance(views, dict) else []
    return {**info,
            "views_retrieved": views is not None,
            "view_count": len(items) if views is not None else None,
            "section_views": [v.get("viewId") for v in items if v.get("sectionId")],
            "projection_angles": sorted({v.get("projectionAngle") for v in items if v.get("projectionAngle")}),
            "evidence": "11_drawing_views.json#/items" if views is not None else None,
            "limitation": "The read-only drawing API returns view definitions, not dimension or annotation text. Drawing "
                          "callouts enter this manifest only as human_supplied_facts that cite this drawing tab. The API "
                          "data do not show which Part Studio the drawing views reference."}


def _expr_mm(expr: str) -> float:
    from .units import length_mm_from_expression
    v, _ = length_mm_from_expression(expr)
    return v if v is not None else float("nan")


def _neighbours(outer: list[dict], span: list | None) -> list:
    if not span:
        return []
    return [o["diameter_mm"] for o in outer if abs(o["axial_to_mm"] - span[0]) < 1e-3 or abs(o["axial_from_mm"] - span[1]) < 1e-3]


def _diameter_at(outer: list[dict], span: list | None) -> float | None:
    if not span:
        return None
    mid = sum(span) / 2
    return next((o["diameter_mm"] for o in outer if o["axial_from_mm"] - 1e-6 <= mid <= o["axial_to_mm"] + 1e-6), None)


def validate_manifest(manifest: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    for k, f in manifest["facts"].items():
        if f["status"] == "known" and f["provenance"] == "human_supplied":
            raise jsonschema.ValidationError(f"fact {k}: human input must go to human_supplied_facts")
    json.dumps(manifest, allow_nan=False)


def write_manifest(manifest: dict, path: Path) -> None:
    validate_manifest(manifest)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
