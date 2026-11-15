"""Sensitivity-test tooling (SYNTHETIC manifests built from the synthetic fixture)."""

import copy
import json

import pytest

from apsd_pipeline import sensitivity
from apsd_pipeline.manifest import build_manifest


def test_diff_reports_only_engineering_changes(synthetic_evidence_dir):
    base = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    test = copy.deepcopy(base)
    test["facts"]["chamfers"] = {**test["facts"]["chamfers"], "evidence": [{"source": "onshape_api", "ref": "other"}]}
    for f in test["feature_inventory"]:
        f["index"] += 1  # position shift only
    test["facts"]["suppressed_or_failed_features"]["value"]["excluded_from_geometry"][0]["index"] += 1
    assert sensitivity.diff_manifests(base, test)["facts_changed"] == {}
    test["facts"]["max_relevant_diameter"]["value"] = 41.0
    assert list(sensitivity.diff_manifests(base, test)["facts_changed"]) == ["max_relevant_diameter"]


def test_prompt_refused_until_prediction_complete(tmp_path):
    d = tmp_path / "sens"
    d.mkdir()
    (d / "prediction.json").write_text(json.dumps(sensitivity.PREDICTION_TEMPLATE))
    with pytest.raises(SystemExit, match="BEFORE asking the AI"):
        sensitivity.make_prompt(d, tmp_path)
    assert not (tmp_path / "ai_runs").exists()
