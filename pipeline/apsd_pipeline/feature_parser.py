"""Parse an Onshape Part Studio feature list (getPartStudioFeatures) into observations.

Every observation carries a JSON pointer into the saved evidence file so a reader
can find the exact field it came from.

Field mappings were written after inspecting the live response for the APSD-LATHE
document on 2026-11-09 (API v17, serializationVersion 1.2.23). Observed facts that
shaped this parser:
  * features are flat objects: {"btType", "featureType", "featureId", "name",
    "suppressed", "parameters", ("entities", "constraints" for sketches), "subFeatures"}
  * quantity parameters hold their value ONLY in "expression" ("value": 0.0, "units": "")
  * feature health is in top-level "featureStates"[featureId].featureStatus / .inactive
  * sketch geometry numbers carry no unit field; the unit is inferred below by
    cross-checking dimension constraints (which do carry units) against geometry.
  * queries such as the revolve axis are serialized with qCompressed(...). The
    "%B5$..." form contains readable sketchEntityId tokens; that format is NOT
    documented by Onshape, so references decoded from it are marked low/medium
    confidence and should be confirmed with bodydetails geometry.

What this parser deliberately does NOT do:
  * infer geometry from feature NAMES (names are reported, never interpreted)
  * treat a thread label as proof of modelled helical geometry
  * treat "no feature found" as proof that the physical part lacks a feature
"""

from __future__ import annotations

import base64
import math
import re
import zlib
from typing import Any

from .units import length_mm_from_expression, parse_expression

FILE = "04_features.json"
TOL = 1e-9

# 12-character string tokens ("Sc$" + 12 chars) in the readable qCompressed form; only
# tokens that match a real sketch entity id of this Part Studio are kept.
_SKETCH_ENTITY_RE = re.compile(r"Sc\$([A-Za-z0-9]{12})")
_KNOWN_ENTITY_IDS: set[str] = set()
_DOC_UNITS: dict = {}
_KNOWN_ENTITY_OWNER: dict[str, str] = {}


def _ptr(*parts: Any) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _param_map(feature: dict) -> dict[str, tuple[int, dict]]:
    return {p.get("parameterId"): (i, p) for i, p in enumerate(feature.get("parameters", []))}


_ZLIB_QUERY_RE = re.compile(r'qCompressed\(1\.0,"&[0-9a-fA-F]+\$([^"]+)"')


def query_text(q: dict) -> str:
    """Readable text of a query. 'qCompressed(1.0,"&<hexlen>$<base64>")' is base64+zlib of
    the readable '%B5$...' form (observed, undocumented); other forms are returned as-is."""
    qs = q.get("queryString", "") or ""
    m = _ZLIB_QUERY_RE.search(qs)
    if m:
        try:
            return zlib.decompress(base64.b64decode(m.group(1))).decode("utf-8", "replace")
        except (ValueError, zlib.error):
            return qs
    return qs


def _decoded_entity_refs(param: dict) -> list[str]:
    refs: list[str] = []
    for q in param.get("queries", []) or []:
        refs += [t for t in _SKETCH_ENTITY_RE.findall(query_text(q)) if t in _KNOWN_ENTITY_IDS]
    return list(dict.fromkeys(refs))


# ----------------------------------------------------------------------------------------
# sketch geometry helpers
# ----------------------------------------------------------------------------------------
def _segment_endpoints(ent: dict) -> tuple[tuple[float, float], tuple[float, float]] | None:
    g = ent.get("geometry", {})
    if not str(g.get("btType", "")).startswith("BTCurveGeometryLine") or "startParam" not in ent:
        return None
    px, py, dx, dy = g["pntX"], g["pntY"], g["dirX"], g["dirY"]
    s, e = ent["startParam"], ent["endParam"]
    return (px + dx * s, py + dy * s), (px + dx * e, py + dy * e)


def _circle(ent: dict) -> dict | None:
    g = ent.get("geometry", {})
    if str(g.get("btType", "")).startswith("BTCurveGeometryCircle") and "startParam" not in ent:
        return {"x": g["xCenter"], "y": g["yCenter"], "r": g["radius"]}
    return None


def sketch_units(sketch: dict) -> dict:
    """Per-sketch unit inference; falls back to the document-wide check when a sketch has
    no checkable LENGTH/DIAMETER/RADIUS constraint (the API uses one serialization unit)."""
    own = infer_sketch_length_unit(sketch)
    if own["factor_to_mm"] or own["checks"] or not _DOC_UNITS.get("factor_to_mm"):
        return {**own, "basis": "this sketch's own dimension constraints"}
    return {**own, "unit": _DOC_UNITS["unit"], "factor_to_mm": _DOC_UNITS["factor_to_mm"],
            "basis": "document-wide check (" + _DOC_UNITS["agreement"] + "); no checkable constraint in this sketch"}


def infer_sketch_length_unit(sketch: dict) -> dict:
    """Infer the unit of raw sketch geometry by comparing dimension constraints
    (expression with explicit unit) with the geometry they constrain."""
    ents = {e.get("entityId"): e for e in sketch.get("entities", [])}
    checks = []
    for ci, c in enumerate(sketch.get("constraints", [])):
        ctype = c.get("constraintType")
        if ctype not in ("LENGTH", "DIAMETER", "RADIUS"):
            continue
        pm = _param_map(c)
        first = pm.get("localFirst", (None, {}))[1].get("value")
        li, lp = pm.get("length", (None, {}))
        mm, _ = length_mm_from_expression(lp.get("expression"))
        ent = ents.get(first)
        if mm is None or ent is None:
            continue
        if ctype == "LENGTH":
            ends = _segment_endpoints(ent)
            if not ends:
                continue
            geom = math.dist(*ends)
        else:
            circ = _circle(ent)
            if not circ:
                continue
            geom = circ["r"] * (2 if ctype == "DIAMETER" else 1)
        if geom > TOL:
            checks.append({"constraint_index": ci, "param_index": li, "type": ctype, "expression": lp.get("expression"),
                           "geometry_value": geom, "ratio_mm_per_unit": mm / geom})
    if not checks:
        return {"unit": None, "factor_to_mm": None, "checks": [], "agreement": "no checkable constraints"}
    ok = [c for c in checks if abs(c["ratio_mm_per_unit"] - 1000.0) / 1000.0 < 1e-3]
    if len(ok) == len(checks):
        return {"unit": "m", "factor_to_mm": 1000.0, "checks": checks,
                "agreement": f"{len(ok)}/{len(checks)} dimension constraints match geometry x 1000"}
    return {"unit": None, "factor_to_mm": None, "checks": checks,
            "agreement": f"only {len(ok)}/{len(checks)} constraints consistent with metres; unit not inferred"}


# ----------------------------------------------------------------------------------------
# main parser
# ----------------------------------------------------------------------------------------
def parse_features(data: dict) -> dict:
    states = data.get("featureStates", {}) or {}
    features = data.get("features", []) or []
    inventory = []
    sketches: dict[str, tuple[int, dict]] = {}
    entity_owner: dict[str, str] = {}

    for i, f in enumerate(features):
        st = states.get(f.get("featureId"), {})
        active = (not f.get("suppressed")) and st.get("featureStatus") != "ERROR" and not st.get("inactive")
        inventory.append({
            "index": i, "feature_id": f.get("featureId"), "feature_type": f.get("featureType"),
            "name_as_entered": f.get("name"), "suppressed": bool(f.get("suppressed")),
            "status": st.get("featureStatus"), "inactive": st.get("inactive"), "used_for_geometry": active,
            "pointer": f"{FILE}#{_ptr('features', i)}",
        })
        if f.get("featureType") == "newSketch":
            sketches[f["featureId"]] = (i, f)
            for e in f.get("entities", []):
                entity_owner[e.get("entityId")] = f["featureId"]
    _KNOWN_ENTITY_IDS.clear()
    _KNOWN_ENTITY_IDS.update(k for k in entity_owner if k)
    _KNOWN_ENTITY_OWNER.clear()
    _KNOWN_ENTITY_OWNER.update(entity_owner)
    all_checks = [c for _, (_, s) in sketches.items() for c in infer_sketch_length_unit(s)["checks"]]
    consistent = bool(all_checks) and all(abs(c["ratio_mm_per_unit"] - 1000.0) / 1000.0 < 1e-3 for c in all_checks)
    _DOC_UNITS.clear()
    _DOC_UNITS.update({"unit": "m" if consistent else None, "factor_to_mm": 1000.0 if consistent else None,
                       "agreement": f"{sum(abs(c['ratio_mm_per_unit'] - 1000.0) / 1000.0 < 1e-3 for c in all_checks)}"
                                    f"/{len(all_checks)} dimension constraints in all sketches match geometry x 1000"})

    active_ids = {x["feature_id"] for x in inventory if x["used_for_geometry"]}
    out: dict[str, Any] = {
        "file": FILE,
        "is_complete": data.get("isComplete"),
        "source_microversion": data.get("sourceMicroversion"),
        "microversion_skew": data.get("microversionSkew"),
        "rollback_index": data.get("rollbackIndex"),
        "serialization_version": data.get("serializationVersion"),
        "feature_inventory": inventory,
        "excluded_features": [x for x in inventory if not x["used_for_geometry"]],
        "revolves": [], "holes": [], "circle_cuts": [], "additive_extrudes": [], "other_extrudes": [],
        "fillets": [], "chamfers": [], "thread_features": [], "unhandled_features": [], "sketch_plane_refs": {},
        "sketch_units_document": dict(_DOC_UNITS),
    }

    # Which profile-sketch entities are used as sketch planes by later sketches
    # (=> the face swept from that entity exists in the model).
    for sid, (si, s) in sketches.items():
        pm = _param_map(s)
        if "sketchPlane" in pm:
            refs = _decoded_entity_refs(pm["sketchPlane"][1])
            raw = " ".join(query_text(q) for q in pm["sketchPlane"][1].get("queries", []))
            out["sketch_plane_refs"][sid] = {
                "entity_refs": refs,
                "default_plane": next((n for n in ("Front", "Top", "Right") if f"{n}planeOp" in raw), None),
                "swept_face": "SWEPT_FACE" in raw,
                "pointer": f"{FILE}#{_ptr('features', si, 'parameters', pm['sketchPlane'][0], 'queries')}",
            }

    for i, f in enumerate(features):
        if f.get("featureId") not in active_ids:
            continue
        ftype = f.get("featureType")
        if ftype == "revolve":
            out["revolves"].append(_parse_revolve(i, f, sketches, entity_owner, out["sketch_plane_refs"]))
        elif ftype == "hole":
            out["holes"].append(_parse_hole(i, f, entity_owner))
        elif ftype == "extrude":
            _parse_extrude(i, f, sketches, out)
        elif ftype == "fillet":
            out["fillets"].append(_parse_edge_blend(i, f, "radius"))
        elif ftype == "chamfer":
            out["chamfers"].append(_parse_edge_blend(i, f, "width"))
        elif ftype in ("externalThread", "internalThread", "cosmeticThread", "thread"):
            out["thread_features"].append(_parse_thread_feature(i, f, states))
        elif ftype == "threadCreator":
            out["thread_features"].append(_parse_thread_creator(i, f, states))
        elif ftype != "newSketch":
            out["unhandled_features"].append({"index": i, "feature_type": ftype, "feature_id": f.get("featureId"),
                                              "pointer": f"{FILE}#{_ptr('features', i)}"})
    return out


def _parse_revolve(i: int, f: dict, sketches: dict, entity_owner: dict, plane_refs: dict) -> dict:
    pm = _param_map(f)
    rv: dict[str, Any] = {
        "index": i, "feature_id": f["featureId"], "pointer": f"{FILE}#{_ptr('features', i)}",
        "operation": pm.get("operationType", (0, {}))[1].get("value"),
        "body_type": pm.get("bodyType", (0, {}))[1].get("value"),
        "full_revolve": pm.get("fullRevolve", (0, {}))[1].get("value"),
    }
    # The axis is a mate-connector subfeature placed ON a sketch entity.
    axis_ref, axis_ptr = None, None
    for si, sf in enumerate(f.get("subFeatures", [])):
        if sf.get("featureType") != "mateConnector":
            continue
        spm = _param_map(sf)
        if "originQuery" in spm:
            refs = _decoded_entity_refs(spm["originQuery"][1])
            if refs:
                axis_ref = refs[0]
                axis_ptr = f"{FILE}#{_ptr('features', i, 'subFeatures', si, 'parameters', spm['originQuery'][0], 'queries', 0, 'queryString')}"
    rv["axis_entity_id"] = axis_ref
    rv["axis_pointer"] = axis_ptr
    sketch_id = entity_owner.get(axis_ref) if axis_ref else None
    rv["profile_sketch_id"] = sketch_id
    if not sketch_id:
        rv["profile"] = None
        rv["profile_note"] = "Axis entity could not be resolved to a sketch line; profile not analysed."
        return rv
    si, sketch = sketches[sketch_id]
    rv["profile_sketch_pointer"] = f"{FILE}#{_ptr('features', si)}"
    rv["profile_sketch_plane"] = plane_refs.get(sketch_id)
    units = sketch_units(sketch)
    rv["sketch_units"] = units
    ents = sketch.get("entities", [])
    axis_ent = next((e for e in ents if e.get("entityId") == axis_ref), None)
    ends = _segment_endpoints(axis_ent) if axis_ent else None
    if not ends or not units["factor_to_mm"]:
        rv["profile"] = None
        rv["profile_note"] = "Axis is not a sketch line, or sketch units could not be verified."
        return rv
    k = units["factor_to_mm"]
    (x0, y0), (x1, y1) = ends
    # Orient the axial coordinate from the axis-line end with the smaller sketch coordinate
    # along the dominant direction (a presentation convention, not a drawing orientation).
    if (abs(x1 - x0) >= abs(y1 - y0) and x1 < x0) or (abs(y1 - y0) > abs(x1 - x0) and y1 < y0):
        (x0, y0), (x1, y1) = (x1, y1), (x0, y0)
    L = math.dist((x0, y0), (x1, y1))
    dx, dy = (x1 - x0) / L, (y1 - y0) / L
    rv["axis_in_sketch"] = {"point": [x0, y0], "direction": [dx, dy],
                            "entity_pointer": f"{FILE}#{_ptr('features', si, 'entities', ents.index(axis_ent))}"}

    def to_ar(p):  # sketch point -> (axial mm, radial mm)
        vx, vy = p[0] - x0, p[1] - y0
        return round((vx * dx + vy * dy) * k, 4), round(abs(vx * dy - vy * dx) * k, 4)

    segs, arcs = [], []
    for ei, e in enumerate(ents):
        if e.get("isConstruction"):
            continue
        ep = _segment_endpoints(e)
        ptr = f"{FILE}#{_ptr('features', si, 'entities', ei)}"
        if ep is None:
            if e.get("geometry"):
                arcs.append({"entity_id": e.get("entityId"), "geometry_type": e["geometry"].get("btType"), "pointer": ptr})
            continue
        (a0, r0), (a1, r1) = to_ar(ep[0]), to_ar(ep[1])
        if abs(r0 - r1) < 1e-3:
            kind = "parallel_to_axis"  # revolves into a cylinder (or the axis itself if r == 0)
        elif abs(a0 - a1) < 1e-3:
            kind = "perpendicular_to_axis"  # revolves into a planar annulus / end face / shoulder
        else:
            kind = "inclined"  # revolves into a cone (chamfer / taper)
        segs.append({"entity_id": e.get("entityId"), "kind": kind, "axial_mm": sorted([a0, a1]),
                     "radial_mm": sorted([r0, r1]), "pointer": ptr})

    axial_all = [a for s in segs for a in s["axial_mm"]]
    a_min, a_max = min(axial_all), max(axial_all)
    cyl = [s for s in segs if s["kind"] == "parallel_to_axis" and s["radial_mm"][0] > 1e-3]
    for s in cyl:
        mid = sum(s["axial_mm"]) / 2
        covering = [c["radial_mm"][0] for c in cyl if c["axial_mm"][0] - 1e-6 <= mid <= c["axial_mm"][1] + 1e-6]
        s["role"] = "outer_envelope" if s["radial_mm"][0] >= max(covering) - 1e-6 else "inner_boundary"
        s["diameter_mm"] = round(2 * s["radial_mm"][0], 4)
        s["face_used_as_sketch_plane_by"] = [sid for sid, r in plane_refs.items() if s["entity_id"] in r["entity_refs"]]
    for s in segs:
        if s["kind"] == "perpendicular_to_axis":
            s["face_used_as_sketch_plane_by"] = [sid for sid, r in plane_refs.items() if s["entity_id"] in r["entity_refs"]]

    outer = sorted([s for s in cyl if s["role"] == "outer_envelope"], key=lambda s: s["axial_mm"][0])
    inner = sorted([s for s in cyl if s["role"] == "inner_boundary"], key=lambda s: s["axial_mm"][0])
    rv["profile"] = {
        "axial_min_mm": a_min, "axial_max_mm": a_max, "axial_extent_mm": round(a_max - a_min, 4),
        "segments": segs, "arc_entities": arcs,
        "outer_cylinders": outer, "inner_cylinders": inner,
        "max_outer_diameter_mm": max((s["diameter_mm"] for s in outer), default=None),
        "axis_on_profile_boundary": any(s["kind"] == "parallel_to_axis" and s["radial_mm"][1] < 1e-3 for s in segs),
        "region_selection_note": ("The revolve's sketch-region selection is a compressed query that cannot be "
                                  "decoded; inner segments are only treated as voids where a later sketch was "
                                  "successfully placed on the face they create."),
    }
    rv["dimension_constraints"] = [
        {"type": c["type"], "expression": c["expression"],
         "pointer": f"{FILE}#{_ptr('features', si, 'constraints', c['constraint_index'], 'parameters', c['param_index'], 'expression')}"}
        for c in units["checks"]
    ]
    return rv


def _q_mm(pm: dict, key: str, i: int) -> dict | None:
    if key not in pm:
        return None
    pi, p = pm[key]
    mm, ev = length_mm_from_expression(p.get("expression"))
    return {"value_mm": mm, **ev, "pointer": f"{FILE}#{_ptr('features', i, 'parameters', pi, 'expression')}"}


def _parse_hole(i: int, f: dict, entity_owner: dict) -> dict:
    pm = _param_map(f)
    val = lambda k: pm.get(k, (0, {}))[1].get("value")  # noqa: E731
    std = val("threadStandard")
    # Observed 2026-11-11: with threadStandard 'UNSET' (custom hole) both lookup tables hold
    # inactive defaults, so a table is only reported when ISO/ANSI is actually selected.
    table_key = {"ISO": "isoHoleTableEx", "ANSI": "ansiHoleTableEx"}.get(std)
    table = (val(table_key) or {}) if table_key else {}
    loc_refs, loc_inference = [], None
    for sf in f.get("subFeatures", []):
        spm = _param_map(sf)
        if "originQuery" in spm:
            loc_refs += _decoded_entity_refs(spm["originQuery"][1])
            loc_inference = spm.get("entityInferenceType", (0, {}))[1].get("value")
    loc_sig = pm.get("locationSignatures", (0, {}))[1].get("expression")
    return {
        "index": i, "feature_id": f["featureId"], "pointer": f"{FILE}#{_ptr('features', i)}",
        "style": val("styleV2") or val("style"),
        "end_style": val("endStyleV2") or val("endStyle"),
        "thread_standard": std, "units_system": val("unitsSystem"),
        "label_as_reported": val("featureName"),
        "lookup_table": {"parameter": table_key, "value": table,
                         "pointer": f"{FILE}#{_ptr('features', i, 'parameters', pm[table_key][0], 'value')}" if table_key in pm else None,
                         "note": None if table_key else f"threadStandard {std!r}: lookup tables inactive, not reported"},
        "major_diameter": _q_mm(pm, "majorDiameter", i),
        "tap_drill_diameter": _q_mm(pm, "tapDrillDiameterV3", i),
        "hole_diameter": _q_mm(pm, "holeDiameterV3", i),
        "hole_depth": _q_mm(pm, "holeDepthV3", i),
        "tapped_depth": _q_mm(pm, "tappedDepthV3", i),
        "is_tapped_through": val("isTappedThrough"),
        "counterbore": {"diameter": _q_mm(pm, "cBoreDiameterV3", i), "depth": _q_mm(pm, "cBoreDepthV3", i)}
        if (val("styleV2") or val("style")) == "C_BORE" else None,
        "location": {"decoded_entity_refs": loc_refs, "inference": loc_inference,
                     "location_signatures_expression": loc_sig,
                     "owner_sketch": entity_owner.get(loc_refs[0]) if loc_refs else None},
        "geometry_note": "Onshape Hole threads are cosmetic: thread data are feature attributes, not modelled helices.",
    }


def _parse_extrude(i: int, f: dict, sketches: dict, out: dict) -> None:
    pm = _param_map(f)
    val = lambda k: pm.get(k, (0, {}))[1].get("value")  # noqa: E731
    op = val("operationType")
    # The profile sketch is referenced by a qSketchRegion(id + "<sketchFeatureId>") query.
    sk_id = None
    sketch_ref_basis = None
    for q in (pm.get("entities", (0, {}))[1].get("queries") or []):
        m = re.search(r'qSketchRegion\(id \+ "([^"]+)"', q.get("queryString", "") or "")
        if m and m.group(1) in sketches:
            sk_id, sketch_ref_basis = m.group(1), "documented qSketchRegion(id + \"<sketch>\") query"
            continue
        txt = query_text(q)
        hits = [sid for sid in sketches if f"{sid}wireOp" in txt]  # wires created by that sketch
        if len(hits) == 1:
            sk_id, sketch_ref_basis = hits[0], "sketch id found in decoded query text (undocumented format)"
    if sk_id is None:
        sketch_ref_basis = "profile sketch not identifiable"
    rec = {"index": i, "feature_id": f["featureId"], "pointer": f"{FILE}#{_ptr('features', i)}",
           "operation": op, "end_bound": val("endBound"), "depth": _q_mm(pm, "depth", i)
           if val("endBound") == "BLIND" else None,
           "profile_sketch_id": sk_id, "profile_sketch_basis": sketch_ref_basis,
           "sketch_plane": out["sketch_plane_refs"].get(sk_id)}
    if sk_id is None:
        out["other_extrudes"].append(rec)
        return
    si, sketch = sketches[sk_id]
    units = sketch_units(sketch)
    rec["sketch_units"] = units
    k = units["factor_to_mm"]
    circles, lines = [], []
    for ei, e in enumerate(sketch.get("entities", [])):
        if e.get("isConstruction"):
            continue
        c = _circle(e)
        if c:
            circles.append({**c, "entity_id": e.get("entityId"), "pointer": f"{FILE}#{_ptr('features', si, 'entities', ei)}"})
        elif _segment_endpoints(e):
            lines.append({"entity_id": e.get("entityId"), "pointer": f"{FILE}#{_ptr('features', si, 'entities', ei)}",
                          "endpoints": _segment_endpoints(e)})
    rec["dimension_constraints"] = []
    for ci, c in enumerate(sketch.get("constraints", [])):
        cpm = _param_map(c)
        if "length" in cpm:
            rec["dimension_constraints"].append({
                "type": c.get("constraintType"), "expression": cpm["length"][1].get("expression"),
                "parsed": (lambda q: {"value": q.value, "unit": q.unit} if q else None)(parse_expression(cpm["length"][1].get("expression"))),
                "pointer": f"{FILE}#{_ptr('features', si, 'constraints', ci, 'parameters', cpm['length'][0], 'expression')}"})
    if circles and not lines and k:
        rec["circles"] = _circle_pattern(circles, k)
        (out["circle_cuts"] if op == "REMOVE" else out["other_extrudes"]).append(rec)
    elif lines and k:
        txt = " ".join(query_text(q) for q in (pm.get("entities", (0, {}))[1].get("queries") or []))
        rec["profile_bounded_by_edges_of"] = sorted({_KNOWN_ENTITY_OWNER.get(t) for t in _SKETCH_ENTITY_RE.findall(txt)
                                                     if t in _KNOWN_ENTITY_OWNER and _KNOWN_ENTITY_OWNER[t] != sk_id} - {None})
        rec["profile_bounding_entity_refs"] = [t for t in _SKETCH_ENTITY_RE.findall(txt) if t in _KNOWN_ENTITY_IDS]
        rec["line_profile"] = {"line_count": len(lines),
                               "all_lines_axis_aligned_in_sketch": all(
                                   abs(l["endpoints"][0][0] - l["endpoints"][1][0]) < 1e-9 or
                                   abs(l["endpoints"][0][1] - l["endpoints"][1][1]) < 1e-9 for l in lines),
                               "lines": [{"entity_id": l["entity_id"], "pointer": l["pointer"],
                                          "start_mm": [round(v * k, 4) for v in l["endpoints"][0]],
                                          "end_mm": [round(v * k, 4) for v in l["endpoints"][1]]} for l in lines]}
        (out["additive_extrudes"] if op == "ADD" else out["other_extrudes"]).append(rec)
    else:
        out["other_extrudes"].append(rec)


def _circle_pattern(circles: list[dict], k: float) -> dict:
    """Group circles; detect equal-diameter circles on a common pitch circle about the sketch origin."""
    diam = sorted({round(2 * c["r"] * k, 4) for c in circles})
    dists = [round(math.hypot(c["x"], c["y"]) * k, 4) for c in circles]
    on_origin = [d < 1e-3 for d in dists]
    pcd = None
    if len(circles) > 1 and not any(on_origin) and max(dists) - min(dists) < 1e-3:
        angles = sorted(math.degrees(math.atan2(c["y"], c["x"])) % 360 for c in circles)
        gaps = [round((angles[(j + 1) % len(angles)] - angles[j]) % 360, 3) for j in range(len(angles))]
        pcd = {"pitch_circle_diameter_mm": round(2 * dists[0], 4), "count": len(circles),
               "angular_gaps_deg": gaps, "equally_spaced": max(gaps) - min(gaps) < 0.01,
               "centre_reference": "sketch origin of the hole sketch (relation to the part axis: see manifest)"}
    return {"count": len(circles), "distinct_diameters_mm": diam, "centre_distances_from_sketch_origin_mm": dists,
            "concentric_with_sketch_origin": all(on_origin), "pitch_circle": pcd,
            "entities": [{"entity_id": c["entity_id"], "pointer": c["pointer"], "diameter_mm": round(2 * c["r"] * k, 4)} for c in circles]}


def _parse_edge_blend(i: int, f: dict, size_key: str) -> dict:
    pm = _param_map(f)
    ent = pm.get("entities", (0, {}))[1]
    queries = ent.get("queries", []) or []
    return {"index": i, "feature_id": f["featureId"], "pointer": f"{FILE}#{_ptr('features', i)}",
            "size": _q_mm(pm, size_key, i), "edge_query_count": len(queries),
            "decoded_edge_sources": [_decoded_entity_refs({"queries": [q]}) for q in queries],
            "decode_note": "edge sources decoded from undocumented qCompressed text: low confidence"}


def _parse_thread_feature(i: int, f: dict, states: dict) -> dict:
    """Thread feature (observed 2026-11-11: featureType 'externalThread' with
    branchOfStandard {standard, size, pitch}, depthType, threadDepth, addChamfer, addUndercut)."""
    pm = _param_map(f)
    val = lambda k: pm.get(k, (0, {}))[1].get("value")  # noqa: E731
    std_i = pm.get("branchOfStandard", (None, {}))[0]
    depth_type = val("depthType")
    return {"index": i, "feature_id": f["featureId"], "feature_type": f.get("featureType"),
            "pointer": f"{FILE}#{_ptr('features', i)}",
            "status": states.get(f["featureId"], {}).get("featureStatus"),
            "standard_as_reported": val("branchOfStandard"),
            "standard_pointer": f"{FILE}#{_ptr('features', i, 'parameters', std_i, 'value')}" if std_i is not None else None,
            "depth_type": depth_type,
            "thread_depth": _q_mm(pm, "threadDepth", i) if str(depth_type).lower() == "blind" else None,
            "add_chamfer": val("addChamfer"), "add_undercut": val("addUndercut"),
            "geometry_note": "cosmetic thread feature: thread data are attributes, no modelled helix"}


def _parse_thread_creator(i: int, f: dict, states: dict) -> dict:
    """Custom FeatureScript 'threadCreator' (observed 2026-11-11 on APSD-LATHE-Final).
    It is NOT an Onshape standard feature: parameter semantics are not documented by
    Onshape, so values are reported as entered and only 'internalThreads' and the pitch
    'p' are used directly; the thread location comes from evaluated geometry."""
    pm = _param_map(f)
    val = lambda k: pm.get(k, (0, {}))[1].get("value")  # noqa: E731
    internal = val("internalThreads")
    return {"index": i, "feature_id": f["featureId"], "feature_type": "threadCreator",
            "pointer": f"{FILE}#{_ptr('features', i)}",
            "source_kind": "custom FeatureScript (namespace " + (f.get("namespace") or "?")[:24] + "...)",
            "status": states.get(f["featureId"], {}).get("featureStatus"),
            "kind": "internal" if internal is True else "external" if internal is False else None,
            "kind_pointer": f"{FILE}#{_ptr('features', i, 'parameters', pm['internalThreads'][0], 'value')}" if "internalThreads" in pm else None,
            "pitch": _q_mm(pm, "p", i),
            "major_diameter_parameter": _q_mm(pm, "majorDiameter", i),
            "diameter_type": val("diameterType"), "length_type": val("lengthType"),
            "length_parameter": _q_mm(pm, "length", i), "profile": val("threadProfile"),
            "left_handed": val("leftHanded"), "label_as_reported": val("screwfriendlyname"),
            "geometry_note": "modelled thread geometry (helical faces in the evaluated B-rep), not a cosmetic attribute"}
