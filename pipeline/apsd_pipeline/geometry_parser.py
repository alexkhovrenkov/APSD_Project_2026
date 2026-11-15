"""Parse evaluated B-rep geometry (getPartStudioBodyDetails) into axis-aware observations.

STATUS: written from the OpenAPI schema ``BTExportModelBodiesResponse-734`` and then
CHECKED against the live response of 2026-11-11 (run 20261111T104951Z_hmac). That
check showed:
  * field names match the schema (surface.type/axis/origin/radius/normal/minorRadius,
    cosmeticThreadMetadata.pitch/threadLength/cylinderRadius/cylinderSystem);
  * a body of revolution has almost no vertices (full circular edges have none), so the
    axial length is taken from FACE BOUNDING BOXES projected on the axis, not vertices;
  * face ``orientation`` (undocumented) was false on every face bounding a known hole
    and true on every known outer cylinder (12/12 cylinders checked against the
    revolve profile); it is used, labelled as an observed convention, to separate
    internal from external faces.
Checked again on the APSD-LATHE-Final tab (run 20261111T113317Z_hmac), which has
modelled helical threads: the bounding boxes of non-analytic (surface type OTHER)
faces are NOT tight - they reached 0.88 mm beyond the planar end faces. The axial
length therefore uses analytic faces (plane/cylinder/cone/torus/sphere) and vertices
only; non-analytic faces are reported separately and only used to locate threads.

Documented unit: "All coordinates are in meters (m)." (getBodyDetails description).

Method (no bounding-box shortcuts; the Part Studio bounding box is documented as
approximate):
  1. Choose the rotation axis as the line shared by the largest total area of
     coaxial cylinder/cone/torus faces.
  2. Axial length = spread of all face bounding boxes (and vertices) projected onto
     the axis. Exact when the axis is parallel to a world axis (boxes are axis-aligned);
     otherwise flagged as approximate.
  3. Max diameter = largest external coaxial cylinder.
  4. Off-axis cylinders parallel to the axis -> axial holes (grouped by radius and
     distance from axis -> pitch circle); cylinders perpendicular -> radial holes.
  5. Planes whose normal is perpendicular to the axis -> plane faces parallel to the
     axis (flats, key or slot sides); planes with normal along the axis -> end
     faces / shoulders.
  6. Faces carrying cosmeticThreadMetadata -> thread attributes (pitch, length).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

FILE = "07_bodydetails.json"
ANALYTIC = {"PLANE", "CYLINDER", "CONE", "TORUS", "SPHERE"}
ORIENTATION_NOTE = ("face 'orientation' is undocumented; observed: false = material outside the cylinder "
                    "(hole/internal), true = external surface (checked on 12/12 cylinders of the live part)")
ANG_TOL = 1e-6
DIST_TOL_M = 1e-6


def _v(d: dict | None) -> tuple[float, float, float] | None:
    if not d:
        return None
    return (float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("z", 0.0)))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else a


def _parallel(a, b) -> bool:
    return abs(abs(_dot(_unit(a), _unit(b))) - 1.0) < 1e-6


def _perpendicular(a, b) -> bool:
    return abs(_dot(_unit(a), _unit(b))) < 1e-6


def _dist_point_line(p, origin, direction) -> float:
    return _norm(_cross(_sub(p, origin), _unit(direction)))


def _mm(x_m: float) -> float:
    return round(x_m * 1000.0, 4)


def parse_bodydetails(data: dict, part_id: str | None = None) -> dict:
    bodies = data.get("bodies", []) or []
    solids = [(i, b) for i, b in enumerate(bodies) if str(b.get("type", "")).upper() in ("SOLID", "")]
    out: dict[str, Any] = {"file": FILE, "status": "schema-derived mapping (not yet verified on a live response)",
                           "solid_body_count": len(solids), "body_ids": [b.get("id") for _, b in solids]}
    chosen = next(((i, b) for i, b in solids if part_id and b.get("id") == part_id), None)
    if chosen is None and len(solids) == 1:
        chosen = solids[0]
    if chosen is None:
        out["error"] = "target body ambiguous (several solid bodies and no matching part id)"
        return out
    bi, body = chosen
    out["body_index"], out["body_id"] = bi, body.get("id")
    faces = body.get("faces", []) or []
    verts = [_v(v.get("point")) for v in body.get("vertices", []) or [] if v.get("point")]

    # -- 1. axis candidates from rotational surfaces ---------------------------------------
    rot = []
    for fi, f in enumerate(faces):
        s = f.get("surface") or {}
        st = str(s.get("type", "")).upper()
        if st in ("CYLINDER", "CONE", "TORUS") and s.get("axis") and s.get("origin"):
            rot.append((fi, f, st, _v(s["origin"]), _unit(_v(s["axis"]))))
    groups: list[dict] = []
    for fi, f, st, o, a in rot:
        for g in groups:
            if _parallel(a, g["dir"]) and _dist_point_line(o, g["origin"], g["dir"]) < DIST_TOL_M:
                g["faces"].append(fi)
                g["area"] += float(f.get("area") or 0.0)
                break
        else:
            groups.append({"origin": o, "dir": a, "faces": [fi], "area": float(f.get("area") or 0.0)})
    if not groups:
        out["error"] = "no cylindrical/conical/toroidal faces: rotation axis not derivable"
        return out
    groups.sort(key=lambda g: g["area"], reverse=True)
    ax = groups[0]
    o, d = ax["origin"], ax["dir"]
    out["rotation_axis"] = {
        "origin_mm": [_mm(c) for c in o], "direction": [round(c, 9) for c in d],
        "basis": f"{len(ax['faces'])} coaxial rotational faces, largest total area of {len(groups)} axis groups",
        "face_pointers": [f"{FILE}#/bodies/{bi}/faces/{fi}/surface" for fi in ax["faces"]],
    }

    # -- 2. axial extent from face boxes (+ vertices) -------------------------------------
    world_aligned = any(abs(c) > 1 - 1e-9 for c in d)
    pts = list(verts)
    for f in faces:
        box = f.get("box") or {}
        if str((f.get("surface") or {}).get("type", "")).upper() in ANALYTIC and box.get("minCorner") and box.get("maxCorner"):
            pts += [_v(box["minCorner"]), _v(box["maxCorner"])]
    if pts:
        proj = [_dot(_sub(p, o), d) for p in pts]
        out["axial_length_mm"] = _mm(max(proj) - min(proj))
        out["axial_range_mm"] = [_mm(min(proj)), _mm(max(proj))]
        n_an = sum(1 for f in faces if str((f.get("surface") or {}).get("type", "")).upper() in ANALYTIC)
        out["axial_length_basis"] = (f"{n_an} analytic-face bounding boxes + {len(verts)} vertices projected on the axis "
                                     f"({len(faces) - n_an} non-analytic faces excluded: their boxes are not tight); "
                                     + ("exact: axis parallel to a world axis" if world_aligned
                                        else "APPROXIMATE: axis not parallel to a world axis"))
        out["axial_length_exact"] = world_aligned
        axial0 = min(proj)
    else:
        axial0 = 0.0
    if verts:
        out["max_vertex_radius_mm"] = _mm(max(_dist_point_line(p, o, d) for p in verts))
    out["vertex_count"] = len(verts)
    coax = []
    for fi in ax["faces"]:
        s = faces[fi]["surface"]
        st = str(s.get("type")).upper()
        r = s.get("radius") if st in ("CYLINDER", "CONE") else s.get("majorRadius")
        box = faces[fi].get("box") or {}
        span = None
        if box.get("minCorner") and box.get("maxCorner"):
            corners = [_v(box["minCorner"]), _v(box["maxCorner"])]
            pa = [_dot(_sub(c, o), d) - axial0 for c in corners]
            span = sorted(_mm(x) for x in pa) if any(abs(c) > 0.999 for c in d) else None
        ori = faces[fi].get("orientation")
        coax.append({"face_id": faces[fi].get("id"), "type": st, "radius_mm": _mm(r) if r is not None else None,
                     "role": ("external" if ori else "internal") if st == "CYLINDER" and ori is not None else
                             ("concave_blend" if st == "TORUS" and ori is False else
                              "convex_blend" if st == "TORUS" and ori is True else None),
                     "diameter_mm": _mm(2 * r) if (r is not None and st != "TORUS") else None,
                     "minor_radius_mm": _mm(s["minorRadius"]) if st == "TORUS" and s.get("minorRadius") is not None else None,
                     "half_angle_rad": s.get("halfAngle") if st == "CONE" else None,
                     "axial_span_mm": span, "area_mm2": round(float(faces[fi].get("area") or 0) * 1e6, 3),
                     "pointer": f"{FILE}#/bodies/{bi}/faces/{fi}"})
    out["coaxial_faces"] = coax
    cyl_r = [c["radius_mm"] for c in coax if c["type"] == "CYLINDER" and c["radius_mm"] and c["role"] != "internal"]
    out["max_coaxial_cylinder_diameter_mm"] = round(2 * max(cyl_r), 4) if cyl_r else None
    out["coaxial_cylinder_diameters_mm"] = sorted({c["diameter_mm"] for c in coax if c["type"] == "CYLINDER"})
    out["external_cylinders"] = sorted([c for c in coax if c["type"] == "CYLINDER" and c["role"] == "external"],
                                       key=lambda c: (c["axial_span_mm"] or [0])[0])
    out["internal_cylinders"] = sorted([c for c in coax if c["type"] == "CYLINDER" and c["role"] == "internal"],
                                       key=lambda c: (c["axial_span_mm"] or [0])[0])
    out["blends"] = [c for c in coax if c["type"] == "TORUS"]
    out["orientation_note"] = ORIENTATION_NOTE

    # -- 4. off-axis holes ---------------------------------------------------------------
    axial_holes: dict[tuple, list] = defaultdict(list)
    radial_holes = []
    for fi, f in enumerate(faces):
        s = f.get("surface") or {}
        if str(s.get("type", "")).upper() != "CYLINDER" or fi in ax["faces"] or not s.get("axis"):
            continue
        a, so = _unit(_v(s["axis"])), _v(s["origin"])
        rec = {"face_id": f.get("id"), "diameter_mm": _mm(2 * s["radius"]), "pointer": f"{FILE}#/bodies/{bi}/faces/{fi}"}
        if _parallel(a, d):
            off = _dist_point_line(so, o, d)
            rel = _sub(so, o)
            rel = _sub(rel, tuple(_dot(rel, d) * c for c in d))
            axial_holes[(rec["diameter_mm"], _mm(off))].append({**rec, "_rel": rel})
        elif _perpendicular(a, d):
            radial_holes.append(rec)
    patterns = []
    for (dia, off), items in axial_holes.items():
        ref = _unit(items[0]["_rel"]) if _norm(items[0]["_rel"]) > 0 else None
        angles = []
        if ref:
            ref2 = _cross(d, ref)
            angles = sorted(round(math.degrees(math.atan2(_dot(it["_rel"], ref2), _dot(it["_rel"], ref))) % 360, 3) for it in items)
        gaps = [round((angles[(j + 1) % len(angles)] - angles[j]) % 360, 3) for j in range(len(angles))] if len(angles) > 1 else []
        patterns.append({"hole_diameter_mm": dia, "cylinder_faces": len(items), "pitch_circle_diameter_mm": round(2 * off, 4),
                         "angular_positions_deg": angles, "equally_spaced": bool(gaps) and max(gaps) - min(gaps) < 0.01,
                         "pointers": [it["pointer"] for it in items],
                         "note": "one hole may consist of several cylinder faces; count faces, not holes"})
    out["axial_off_axis_cylinders"] = patterns
    out["radial_cylinders"] = radial_holes

    # -- 5. planes -----------------------------------------------------------------------
    planes_parallel, planes_normal = [], []
    for fi, f in enumerate(faces):
        s = f.get("surface") or {}
        if str(s.get("type", "")).upper() != "PLANE" or not s.get("normal"):
            continue
        n, po = _unit(_v(s["normal"])), _v(s["origin"])
        rec = {"face_id": f.get("id"), "area_mm2": round(float(f.get("area") or 0) * 1e6, 3), "pointer": f"{FILE}#/bodies/{bi}/faces/{fi}"}
        box = f.get("box") or {}
        if world_aligned and box.get("minCorner") and box.get("maxCorner"):
            rec["axial_span_mm"] = sorted(_mm(_dot(_sub(_v(box[k]), o), d) - axial0) for k in ("minCorner", "maxCorner"))
        if _parallel(n, d):
            planes_normal.append({**rec, "axial_position_mm": _mm(_dot(_sub(po, o), d) - axial0)})
        elif _perpendicular(n, d):
            planes_parallel.append({**rec, "distance_from_axis_mm": _mm(abs(_dot(_sub(o, po), n)))})
    out["planes_normal_to_axis"] = planes_normal
    out["planes_parallel_to_axis"] = planes_parallel

    # -- 6. cosmetic threads ---------------------------------------------------------------
    threads = []
    for fi, f in enumerate(faces):
        t = f.get("cosmeticThreadMetadata")
        if t:
            cs = t.get("cylinderSystem") or {}
            start = _v(cs.get("origin"))
            zax = _v(cs.get("zAxis"))
            threads.append({"face_id": f.get("id"),
                            "role": ("external" if f.get("orientation") else "internal") if f.get("orientation") is not None else None,
                            "starts_at_axial_mm": _mm(_dot(_sub(start, o), d) - axial0) if start else None,
                            "runs_toward": ("+axial" if _dot(zax, d) > 0 else "-axial") if zax else None,
                            "pitch_mm": _mm(t["pitch"]) if t.get("pitch") is not None else None,
                            "thread_length_mm": _mm(t["threadLength"]) if t.get("threadLength") is not None else None,
                            "cylinder_diameter_mm": _mm(2 * t["cylinderRadius"]) if t.get("cylinderRadius") is not None else None,
                            "pointer": f"{FILE}#/bodies/{bi}/faces/{fi}/cosmeticThreadMetadata",
                            "note": "cosmetic thread metadata; not modelled helical geometry"})
    out["cosmetic_threads"] = threads
    out["material"] = ((body.get("properties") or {}).get("material") or {}).get("name")
    return out


def _box_axial_radial(face: dict, o, d, axial0: float) -> tuple[list[float], float] | None:
    box = face.get("box") or {}
    if not (box.get("minCorner") and box.get("maxCorner")):
        return None
    a, b = _v(box["minCorner"]), _v(box["maxCorner"])
    span = sorted(_mm(_dot(_sub(c, o), d) - axial0) for c in (a, b))
    corners = [(x, y, z) for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]
    return span, _mm(max(_dist_point_line(c, o, d) for c in corners))


def locate_freeform_faces(data: dict, go: dict) -> list[dict]:
    """Non-analytic faces (e.g. modelled helical thread flanks), located with their LOOSE
    bounding boxes: axial span clipped to the part length, radial value is an upper bound.
    role: 'external' if the box reaches beyond the external cylinder radius over that span,
    'internal' if it lies inside the external envelope and overlaps an internal cylinder."""
    if not go or "rotation_axis" not in go:
        return []
    bi = go["body_index"]
    body = data["bodies"][bi]
    o = tuple(x / 1000.0 for x in go["rotation_axis"]["origin_mm"])
    d = tuple(go["rotation_axis"]["direction"])
    axial0 = go["axial_range_mm"][0] / 1000.0
    L = go["axial_length_mm"]
    out = []
    for fi, f in enumerate(body.get("faces", [])):
        st = str((f.get("surface") or {}).get("type", "")).upper()
        if st in ANALYTIC:
            continue
        loc = _box_axial_radial(f, o, d, axial0)
        if not loc:
            continue
        (a0, a1), rmax = loc
        a0c, a1c = max(a0, 0.0), min(a1, L)
        mid = (a0c + a1c) / 2
        ext_r = next((c["radius_mm"] for c in go["external_cylinders"]
                      if c["axial_span_mm"] and c["axial_span_mm"][0] - 1 <= mid <= c["axial_span_mm"][1] + 1), None)
        overlaps_int = [c for c in go["internal_cylinders"] if c["axial_span_mm"] and
                        c["axial_span_mm"][0] < a1c and a0c < c["axial_span_mm"][1]]
        role = None
        if ext_r is not None and rmax > ext_r + 1e-3:
            role = "external"
        elif overlaps_int and (ext_r is None or rmax < ext_r - 1e-3):
            role = "internal"
        out.append({"face_id": f.get("id"), "surface_type": st, "role_heuristic": role,
                    "axial_span_clipped_mm": [round(a0c, 3), round(a1c, 3)], "raw_box_axial_span_mm": [a0, a1],
                    "box_radial_upper_bound_mm": rmax, "area_mm2": round(float(f.get("area") or 0) * 1e6, 1),
                    "near_diameters_mm": (sorted({c["diameter_mm"] for c in overlaps_int}) if role == "internal" else
                                          [round(2 * ext_r, 4)] if role == "external" and ext_r else []),
                    "pointer": f"{FILE}#/bodies/{bi}/faces/{fi}"})
    return out
