"""Offline checks: unit conversion and UNKNOWN handling (synthetic inputs only)."""

import jsonschema
import pytest

from apsd_pipeline.manifest import build_manifest, validate_manifest
from apsd_pipeline.units import length_mm_from_expression, parse_expression


@pytest.mark.parametrize("expr,mm", [
    ("13.000 mm", 13.0), ("13.0*mm", 13.0), ("(7.98/2) mm", 3.99), ("210 mm", 210.0),
    ("0.21 m", 210.0), ("2 in", 50.8), ("1.5 cm", 15.0), ("0.5 meter", 500.0), ("-0.1 mm", -0.1),
])
def test_length_conversion_to_mm(expr, mm):
    value, ev = length_mm_from_expression(expr)
    assert value == pytest.approx(mm)
    assert ev["original_expression"] == expr and "conversion" in ev and ev["conversion"]


@pytest.mark.parametrize("expr", ["#len", "#a + 2 mm", "foo(3) mm", "5 furlong", "", None, "mm", "1/0 mm", "__import__('os') mm"])
def test_non_literal_or_unknown_unit_is_unknown(expr):
    value, ev = length_mm_from_expression(expr)
    assert value is None and ev["conversion"] is None


def test_angles_are_not_lengths():
    assert length_mm_from_expression("90 deg")[0] is None
    assert parse_expression("90 degree").to_deg() == 90
    assert parse_expression("0.5").kind == "dimensionless"


def test_manifest_marks_inaccessible_sources_not_retrieved(synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    validate_manifest(m)
    f = m["facts"]
    assert f["overall_axial_length"]["value"] == pytest.approx(100.0)
    assert f["max_relevant_diameter"]["value"] == pytest.approx(40.0)
    assert f["length_to_diameter_ratio"]["value"] == pytest.approx(2.5)
    assert f["material"]["status"] == "not_retrieved" and f["material"]["value"] is None
    assert m["source"]["part"]["status"] == "not_retrieved"
    for k in ("stock_form", "tolerances", "surface_finish", "external_thread_on_physical_part"):
        assert f[k]["status"] == "unknown" and f[k]["value"] is None and f[k]["reason"]
    # absent is scoped, never a bare claim
    assert f["external_thread_in_cad_model"]["status"] == "absent"
    assert f["external_thread_in_cad_model"]["scope"] == "cad_model_feature_list"
    # suppressed features are listed and excluded
    excl = f["suppressed_or_failed_features"]["value"]["excluded_from_geometry"]
    assert [x["feature_id"] for x in excl] == ["Fsupp"]
    assert {u["fact"] for u in m["unknowns"]} >= {"material", "stock_form", "part"}


def test_schema_rejects_unknown_with_a_value(synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    m["facts"]["tolerances"]["value"] = "IT7"  # fabricated value on an unknown fact
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(m)


def test_schema_rejects_absent_without_scope(synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    del m["facts"]["gear_teeth"]["scope"]
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(m)


def test_human_fact_in_api_facts_is_rejected(synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    m["facts"]["chamfers"]["provenance"] = "human_supplied"
    m["facts"]["chamfers"]["status"] = "known"
    with pytest.raises(jsonschema.ValidationError):
        validate_manifest(m)


def test_human_supplement_only_verified_entries_used(synthetic_evidence_dir, tmp_path, monkeypatch):
    import json

    import apsd_pipeline.manifest as mf
    entry = {"fact": "x", "value": 1, "unit": "mm", "source": "SYNTHETIC drawing callout", "verified": False,
             "verified_by": "Tester", "verified_on": "2026-01-01"}
    f = tmp_path / "supplement.json"
    monkeypatch.setattr(mf, "HUMAN_INPUTS_PATH", f)
    f.write_text(json.dumps({"entries": [entry]}))
    assert build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")["human_supplied_facts"] == []
    f.write_text(json.dumps({"entries": [{**entry, "verified": True}]}))
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    validate_manifest(m)
    assert [h["provenance"] for h in m["human_supplied_facts"]] == ["human_supplied"]
    f.write_text(json.dumps({"entries": [{**entry, "verified": True, "verified_by": None}]}))
    assert build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")["human_supplied_facts"] == []
