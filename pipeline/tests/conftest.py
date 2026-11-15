"""Shared test fixtures.

Everything built here is SYNTHETIC: invented, minimal Onshape-like JSON used only to
test the code offline. It is NOT evidence about the APSD-LATHE part and must never be
reported as a working live connection.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SYNTHETIC = "SYNTHETIC TEST FIXTURE - not live data"


def _line(eid, x0, y0, x1, y1, construction=False):
    L = math.dist((x0, y0), (x1, y1))
    return {"btType": "BTMSketchCurveSegment-155", "entityId": eid, "isConstruction": construction,
            "geometry": {"btType": "BTCurveGeometryLine-117", "pntX": x0, "pntY": y0,
                         "dirX": (x1 - x0) / L, "dirY": (y1 - y0) / L},
            "startParam": 0.0, "endParam": L}


def _length(eid, expr):
    return {"btType": "BTMSketchConstraint-2", "constraintType": "LENGTH", "parameters": [
        {"btType": "BTMParameterString-149", "parameterId": "localFirst", "value": eid},
        {"btType": "BTMParameterQuantity-147", "parameterId": "length", "expression": expr, "value": 0.0, "units": ""}]}


def _q(text):
    return {"btType": "BTMIndividualQuery-138", "queryString": f'query=qCompressed(1.0,"{text}",id);'}


@pytest.fixture
def synthetic_features() -> dict:
    """A 100 mm stepped shaft: D40 x 30 then D20 x 70, axis line AXIS000000AA (metres)."""
    ents = [
        _line("AXIS000000AA", 0.0, 0.0, 0.1, 0.0),
        _line("ENDL000000AA", 0.0, 0.0, 0.0, 0.02),
        _line("CYL4000000AA", 0.0, 0.02, 0.03, 0.02),
        _line("SHLD000000AA", 0.03, 0.02, 0.03, 0.01),
        _line("CYL2000000AA", 0.03, 0.01, 0.1, 0.01),
        _line("ENDR000000AA", 0.1, 0.01, 0.1, 0.0),
    ]
    sketch = {"btType": "BTMSketch-151", "featureType": "newSketch", "featureId": "Fsketch1", "name": "S1",
              "suppressed": False, "subFeatures": [],
              "parameters": [{"btType": "BTMParameterQueryList-148", "parameterId": "sketchPlane",
                              "queries": [_q("%B5$...FrontplaneOp...")]}],
              "entities": ents,
              "constraints": [_length("AXIS000000AA", "100 mm"), _length("CYL2000000AA", "70 mm")]}
    revolve = {"btType": "BTMFeature-134", "featureType": "revolve", "featureId": "Frev", "name": "R",
               "suppressed": False,
               "parameters": [{"btType": "BTMParameterEnum-145", "parameterId": "operationType", "value": "NEW"},
                              {"btType": "BTMParameterBoolean-144", "parameterId": "fullRevolve", "value": True}],
               "subFeatures": [{"featureType": "mateConnector", "featureId": "Fmc", "parameters": [
                   {"btType": "BTMParameterQueryList-148", "parameterId": "originQuery",
                    "queries": [_q("%B5$QuerySKETCH_ENTITYSe$sketchEntityIdSc$AXIS000000AAR4")]}]}]}
    suppressed = {"btType": "BTMFeature-134", "featureType": "chamfer", "featureId": "Fsupp", "name": "C",
                  "suppressed": True, "parameters": [], "subFeatures": []}
    return {"_comment": SYNTHETIC, "isComplete": True, "sourceMicroversion": "synthetic000000000000000",
            "microversionSkew": False, "features": [sketch, revolve, suppressed],
            "featureStates": {"Fsketch1": {"featureStatus": "OK", "inactive": False},
                              "Frev": {"featureStatus": "OK", "inactive": False},
                              "Fsupp": {"featureStatus": "OK", "inactive": False}}}


@pytest.fixture
def synthetic_evidence_dir(tmp_path, synthetic_features) -> Path:
    d = tmp_path / "evidence" / "synthetic_run"
    d.mkdir(parents=True)
    (d / "04_features.json").write_text(json.dumps(synthetic_features))
    (d / "01_document.json").write_text(json.dumps({"name": "SYNTHETIC"}))
    (d / "extraction_record.json").write_text(json.dumps({
        "run_id": "synthetic_run", "auth_mode": "synthetic", "api_version": "v17", "started_utc": "2026-01-01T00:00:00+00:00",
        "target": {"host": "https://cad.onshape.com", "document_id": "0" * 24, "url_wvm": "w", "url_wvm_id": "0" * 24,
                   "element_id": "0" * 24},
        "element_type": "PARTSTUDIO", "element_name": "SYNTHETIC",
        "snapshot": {"wvm": "m", "wvm_id": "synthetic000000000000000", "source": SYNTHETIC},
        "endpoints": {"05_parts": {"status": 401, "retrieved": False}, "06_configuration": {"status": 401, "retrieved": False},
                      "07_bodydetails": {"status": 401, "retrieved": False}}}))
    return d


def _vec(x, y, z):
    return {"btType": "BTVector3d-389", "x": x, "y": y, "z": z}


@pytest.fixture
def synthetic_bodydetails() -> dict:
    """Shaft along +X: D60 x 50 + D30 x 50 (metres), 4 axial holes D5 on PCD 40, one radial hole, one flat."""
    faces = [
        {"id": "F1", "area": 0.0094, "surface": {"type": "CYLINDER", "radius": 0.03, "origin": _vec(0, 0, 0), "axis": _vec(1, 0, 0)}},
        {"id": "F2", "area": 0.0047, "surface": {"type": "CYLINDER", "radius": 0.015, "origin": _vec(0.05, 0, 0), "axis": _vec(1, 0, 0)}},
        {"id": "F3", "area": 0.0028, "surface": {"type": "PLANE", "origin": _vec(0, 0, 0), "normal": _vec(-1, 0, 0)}},
        {"id": "F4", "area": 0.0001, "surface": {"type": "PLANE", "origin": _vec(0.08, 0, 0.012), "normal": _vec(0, 0, 1)}},
        {"id": "F5", "area": 0.0001, "surface": {"type": "CYLINDER", "radius": 0.002, "origin": _vec(0.08, 0, 0), "axis": _vec(0, 1, 0)}},
    ]
    for k, ang in enumerate((0, 90, 180, 270)):
        a = math.radians(ang)
        faces.append({"id": f"H{k}", "area": 0.00005, "surface": {"type": "CYLINDER", "radius": 0.0025,
                      "origin": _vec(0, 0.02 * math.cos(a), 0.02 * math.sin(a)), "axis": _vec(1, 0, 0)}})
    verts = [{"id": f"V{i}", "point": _vec(x, r, 0)} for i, (x, r) in enumerate([(0, 0.03), (0.05, 0.03), (0.05, 0.015), (0.1, 0.015)])]
    return {"_comment": SYNTHETIC, "bodies": [{"id": "JHD", "type": "SOLID", "faces": faces, "vertices": verts, "edges": []}]}
