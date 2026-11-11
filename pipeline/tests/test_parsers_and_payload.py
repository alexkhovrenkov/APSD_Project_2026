"""Parser, payload and output-check tests.

* synthetic_* fixtures: SYNTHETIC data (see conftest.py).
* test_live_evidence_regression: re-parses the ARCHIVED live Onshape evidence committed
  under output/evidence/ (anonymous run of 2026-11-09). It checks that the code still
  reproduces what was extracted then; it does not contact Onshape.
"""

import glob
import json
from pathlib import Path

import pytest

from apsd_pipeline.ai_handoff import _strict_schema, build_payload, check_classification
from apsd_pipeline.feature_parser import parse_features
from apsd_pipeline.geometry_parser import parse_bodydetails
from apsd_pipeline.manifest import build_manifest, write_manifest

PIPELINE = Path(__file__).resolve().parent.parent


def test_feature_parser_synthetic(synthetic_features):
    o = parse_features(synthetic_features)
    rv = o["revolves"][0]
    assert rv["axis_entity_id"] == "AXIS000000AA"
    assert rv["sketch_units"]["unit"] == "m"
    p = rv["profile"]
    assert p["axial_extent_mm"] == pytest.approx(100)
    assert [s["diameter_mm"] for s in p["outer_cylinders"]] == [40.0, 20.0]
    assert o["excluded_features"][0]["feature_id"] == "Fsupp"


def test_sketch_units_not_assumed_when_inconsistent(synthetic_features):
    # break the cross-check: a constraint that disagrees with the geometry
    synthetic_features["features"][0]["constraints"][1]["parameters"][1]["expression"] = "33 mm"
    o = parse_features(synthetic_features)
    assert o["revolves"][0]["sketch_units"]["factor_to_mm"] is None
    assert o["revolves"][0]["profile"] is None


def test_geometry_parser_synthetic(synthetic_bodydetails):
    g = parse_bodydetails(synthetic_bodydetails)
    assert g["rotation_axis"]["direction"] == [1.0, 0.0, 0.0]
    assert g["axial_length_mm"] == pytest.approx(100.0)
    assert g["max_coaxial_cylinder_diameter_mm"] == pytest.approx(60.0)
    pat = g["axial_off_axis_cylinders"][0]
    assert pat["cylinder_faces"] == 4 and pat["pitch_circle_diameter_mm"] == pytest.approx(40.0) and pat["equally_spaced"]
    assert len(g["radial_cylinders"]) == 1
    assert g["planes_parallel_to_axis"][0]["distance_from_axis_mm"] == pytest.approx(12.0)


def _latest_live(kind):
    runs = sorted(glob.glob(str(PIPELINE / "output" / "evidence" / f"*_{kind}" / "04_features.json")))
    return Path(runs[-1]).parent if runs else None


def _run(run_id):
    d = PIPELINE / "output" / "evidence" / run_id
    return d if (d / "04_features.json").exists() else None


WIP_RUN, FINAL_RUN = "20261111T105407Z_hmac", "20261111T113317Z_hmac"


@pytest.mark.skipif(_latest_live("anonymous") is None, reason="no archived anonymous live evidence present")
def test_live_anonymous_evidence_regression(tmp_path):
    """2026-11-09 anonymous run (model revision BEFORE the external thread was added)."""
    m = build_manifest(_latest_live("anonymous"))
    write_manifest(m, tmp_path / "manifest.json")  # validates
    f = m["facts"]
    assert m["evidence_class"] == "LIVE_ONSHAPE_API"
    assert f["overall_axial_length"]["value"] == pytest.approx(210.0)
    assert f["max_relevant_diameter"]["value"] == pytest.approx(100.0)
    assert f["length_to_diameter_ratio"]["value"] == pytest.approx(2.1)
    assert f["axial_holes"]["value"]["feature_list"][0]["pitch_circle"]["count"] == 6
    assert f["internal_threads"]["value"]["items"][0]["designation"] == "M16"
    assert f["external_thread_in_cad_model"]["status"] == "absent"
    assert m["source"]["part"]["status"] == "not_retrieved"


@pytest.mark.skipif(_run(WIP_RUN) is None, reason="archived WIP-tab run not present")
def test_live_authenticated_wip_regression(tmp_path):
    """2026-11-11 authenticated run of the 'WIP' tab (superseded target): geometry + feature-list cross-check."""
    m = build_manifest(_run(WIP_RUN))
    write_manifest(m, tmp_path / "manifest.json")
    f = m["facts"]
    assert m["source"]["geometry_source"].endswith("feature list cross-check")
    for k in ("overall_axial_length", "max_relevant_diameter", "length_to_diameter_ratio", "rotation_axis"):
        assert f[k]["confidence"] == "high", k
    assert f["overall_axial_length"]["value"] == pytest.approx(210.0)
    assert f["rotation_axis"]["value"]["direction"] == [1.0, 0.0, 0.0]
    assert f["external_steps"]["value"]["diameter_sequence_from_axial_0_mm"] == [29.9, 60.0, 100.0, 50.0]
    bores = {(i.get("diameter_mm"), i.get("axial_from_mm"), i.get("axial_to_mm")) for i in f["bores_and_counterbores"]["value"]["items"]}
    assert (13.0, 0.0, 170.0) in bores and (30.0, 170.0, 210.0) in bores
    assert f["bores_and_counterbores"]["value"]["continuous_axial_opening_end_to_end"] is True
    pat = f["axial_holes"]["value"]["patterns"][0]
    assert (pat["count_of_cylinder_faces"], pat["hole_diameter_mm"], pat["pitch_circle_diameter_mm"]) == (6, 3.52, 46.0)
    assert f["radial_holes"]["status"] == "absent"
    assert f["external_thread_in_cad_model"]["value"]["consistency_notes"]  # M48 vs cylinder 50 is flagged
    assert m["source"]["part"]["value"]["part_count"] == 1


def test_payload_contents_and_isolation(tmp_path, synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    mp = tmp_path / "manifest.json"
    write_manifest(m, mp)
    payload, ctx = build_payload(mp, "test-model")
    assert set(payload) == {"model", "instructions", "input", "text"}
    assert payload["text"]["format"]["type"] == "json_schema" and payload["text"]["format"]["strict"] is True
    texts = [c["text"] for c in payload["input"][0]["content"]]
    assert any(mp.read_text() in t for t in texts)  # the saved manifest file, verbatim
    blob = json.dumps(payload)
    assert "15202" not in blob  # the team's frozen manual code must not reach the AI
    assert "manual_opitz" not in blob and "OPENAI_API_KEY" not in blob and "ONSHAPE_SECRET" not in blob
    assert ctx["frozen_manual_answer_included"] is False


def test_strict_schema_is_strict_mode_compatible():
    s = _strict_schema()

    def walk(n):
        if isinstance(n, dict):
            assert "$ref" not in n
            if n.get("type") == "object":
                assert n.get("additionalProperties") is False
                assert set(n.get("required", [])) == set(n.get("properties", {}))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(s)


def _ai_output(d1="1", code="1UUUU", cite="facts.length_to_diameter_ratio"):
    pos = lambda d, c: {"digit": d, "course_table_row": "", "justification": "x", "course_rule_reference": "x",  # noqa: E731
                        "feature_evidence": [{"manifest_fact": c, "observation": "x"}] if c else [],
                        "uncertainty": "low", "uncertainty_reason": "x", "competing_candidates": []}
    return {"part": "SYNTHETIC", "form_code": code, "information_not_derivable_from_geometry": [], "overall_notes": "",
            "digits": {"d1": pos(d1, cite), **{p: pos("UNKNOWN", None) for p in ("d2", "d3", "d4", "d5")}}}


def test_output_checks_detect_problems(synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    ok = check_classification(_ai_output(), m)
    assert ok["schema_valid"] and ok["evidence_reference_findings"] == []
    bad = check_classification(_ai_output(code="15202"), m)
    assert any("does not match" in x for x in bad["evidence_reference_findings"])
    bad = check_classification(_ai_output(cite="facts.material"), m)
    assert any("not_retrieved" in x for x in bad["evidence_reference_findings"])
    bad = check_classification(_ai_output(cite="facts.made_up_fact"), m)
    assert any("not a manifest fact" in x for x in bad["evidence_reference_findings"])
    broken = _ai_output()
    broken["digits"]["d1"]["digit"] = "12"
    assert not check_classification(broken, m)["schema_valid"]


def test_openai_sdk_sends_payload_unchanged(tmp_path, synthetic_evidence_dir):
    """The official SDK accepts the payload and POSTs it verbatim to /v1/responses.
    A mock transport captures the request; nothing leaves the machine."""
    import httpx2 as httpx  # HTTP library used by openai-python 3.x
    from openai import OpenAI

    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    mp = tmp_path / "manifest.json"
    write_manifest(m, mp)
    payload, _ = build_payload(mp, "test-model")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(400, json={"error": {"message": "mock", "type": "invalid_request_error"}})

    client = OpenAI(api_key="test-key-not-real", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception):
        client.responses.create(**payload)
    assert seen["url"].endswith("/responses")
    assert seen["body"] == payload
    assert "test-key-not-real" not in json.dumps(seen["body"])  # key only in the header, never in the payload


@pytest.mark.skipif(_run(FINAL_RUN) is None, reason="archived APSD-LATHE-Final run not present")
def test_live_final_tab_regression(tmp_path):
    """2026-11-11 authenticated run of the 'APSD-LATHE-Final' tab (current target) with modelled threads."""
    m = build_manifest(_run(FINAL_RUN))
    write_manifest(m, tmp_path / "manifest.json")
    f = m["facts"]
    assert m["source"]["element_name"] == "APSD-LATHE-Final"
    assert m["source"]["drawing"]["view_count"] == 5 and m["source"]["drawing"]["section_views"]
    # helical (non-analytic) faces must not inflate the length: their boxes reach 0.88 mm past the end faces
    assert f["overall_axial_length"]["value"] == pytest.approx(210.0) and f["overall_axial_length"]["confidence"] == "high"
    assert f["length_to_diameter_ratio"]["value"] == pytest.approx(2.1)
    bores = {(i.get("diameter_mm"), i.get("axial_from_mm"), i.get("axial_to_mm")) for i in f["bores_and_counterbores"]["value"]["items"]}
    assert {(12.0, 0.0, 41.42), (13.0, 41.42, 170.0), (30.0, 170.0, 210.0)} <= bores
    it = f["internal_threads"]["value"]["items"]
    assert any(i.get("pitch_mm") == 2.0 for i in it) and any(i.get("near_diameters_mm") == [12.0, 13.0] for i in it)
    et = f["external_thread_in_cad_model"]["value"]["items"]
    assert any(i.get("pitch_mm") == 5.0 for i in et) and any(i.get("near_diameters_mm") == [50.0] for i in et)
    assert f["external_thread_in_cad_model"]["value"]["consistency_notes"]  # majorDiameter 25 mm flagged
    hole = next(i for i in f["bores_and_counterbores"]["value"]["items"] if i["kind"].startswith("Hole feature"))
    assert "custom" in hole["kind"]  # inactive lookup tables are not reported as the hole type


def test_freeform_faces_excluded_from_length(synthetic_bodydetails):
    """SYNTHETIC: a non-analytic face with a loose box beyond the ends must not change the length."""
    body = synthetic_bodydetails["bodies"][0]
    body["faces"].append({"id": "HX", "area": 0.001, "surface": {"type": "OTHER"},
                          "box": {"minCorner": {"x": -0.002, "y": -0.016, "z": -0.016}, "maxCorner": {"x": 0.103, "y": 0.016, "z": 0.016}}})
    for f in body["faces"][:2]:  # give the main cylinders boxes so the length comes from analytic faces
        r = f["surface"]["radius"]
        x0 = f["surface"]["origin"]["x"]
        f["box"] = {"minCorner": {"x": x0, "y": -r, "z": -r}, "maxCorner": {"x": x0 + 0.05, "y": r, "z": r}}
    g = parse_bodydetails(synthetic_bodydetails)
    assert g["axial_length_mm"] == pytest.approx(100.0)
    assert "non-analytic faces excluded" in g["axial_length_basis"]
