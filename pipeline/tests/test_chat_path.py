"""Claude chat path: prompt building and reply import (SYNTHETIC manifest + SYNTHETIC replies)."""

import json

import pytest

from apsd_pipeline.ai_handoff import (CHAT_FORMAT_HEADER, build_chat_prompt, build_chat_revision_prompt,
                                      build_payload, extract_json_reply, import_chat_response)
from apsd_pipeline.manifest import build_manifest, write_manifest


@pytest.fixture
def manifest_file(tmp_path, synthetic_evidence_dir):
    m = build_manifest(synthetic_evidence_dir, evidence_class="SYNTHETIC_TEST_FIXTURE")
    p = tmp_path / "manifest.json"
    write_manifest(m, p)
    return p


def _reply(code="1UUUU"):
    pos = lambda d: {"digit": d, "course_table_row": "", "justification": "synthetic", "course_rule_reference": "x",  # noqa: E731
                     "feature_evidence": [{"manifest_fact": "facts.length_to_diameter_ratio", "observation": "2.5"}] if d != "UNKNOWN" else [],
                     "uncertainty": "low", "uncertainty_reason": "x", "competing_candidates": []}
    return {"part": "SYNTHETIC", "form_code": code, "information_not_derivable_from_geometry": [], "overall_notes": "",
            "digits": {"d1": pos("1"), **{p: pos("UNKNOWN") for p in ("d2", "d3", "d4", "d5")}}}


def _run_dir(tmp_path, manifest_file, parent=None):
    text, ctx = build_chat_prompt(manifest_file)
    d = tmp_path / "run"
    d.mkdir()
    (d / "chat_prompt.txt").write_text(text)
    if parent:
        ctx["parent_run"] = parent
    (d / "payload_context.json").write_text(json.dumps(ctx))
    return d


def test_chat_prompt_matches_api_inputs_and_is_isolated(manifest_file):
    text, ctx = build_chat_prompt(manifest_file)
    payload, _ = build_payload(manifest_file, "x")
    for block in payload["input"][0]["content"]:  # identical content to the API path
        assert block["text"] in text
    assert manifest_file.read_text() in text and CHAT_FORMAT_HEADER in text and '"additionalProperties": false' in text
    assert "15202" not in text and "15U" not in text and "manual_opitz" not in text  # no hint of the manual answer
    assert ctx["interface"].startswith("chat") and len(ctx["chat_prompt_sha256"]) == 64


@pytest.mark.parametrize("wrap,how", [
    (lambda s: s, "whole reply"), (lambda s: "```json\n" + s + "\n```", "code fence"),
    (lambda s: "Here is the result:\n" + s + "\nHope this helps", "cut out"),
])
def test_reply_extraction(wrap, how):
    parsed, note = extract_json_reply(wrap(json.dumps(_reply())))
    assert parsed["form_code"] == "1UUUU" and how in note


def test_import_archives_and_never_overwrites(tmp_path, manifest_file):
    d = _run_dir(tmp_path, manifest_file)
    reply = tmp_path / "reply.txt"
    reply.write_text("```json\n" + json.dumps(_reply()) + "\n```")
    manifest = json.loads(manifest_file.read_text())
    meta = import_chat_response(d, reply, "Synthetic Model 1", "claude.ai web chat (manual paste)", "2026-11-09", manifest, "tester")
    assert meta["outcome"] == "parsed" and meta["provider"] == "Anthropic" and meta["form_code_proposed"] == "1UUUU"
    for f in ("response_raw.txt", "classification_initial.json", "automatic_checks.json", "run_meta.json", "human_review.json"):
        assert (d / f).exists()
    assert (d / "response_raw.txt").read_text() == reply.read_text()
    with pytest.raises(SystemExit, match="never overwritten"):
        import_chat_response(d, reply, "m", "i", "2026-11-09", manifest)


def test_invalid_reply_is_recorded_not_fabricated(tmp_path, manifest_file):
    d = _run_dir(tmp_path, manifest_file)
    reply = tmp_path / "reply.txt"
    reply.write_text("I think the code is 1-something.")
    meta = import_chat_response(d, reply, "m", "i", "2026-11-09", json.loads(manifest_file.read_text()))
    assert meta["outcome"] == "invalid_json" and not (d / "classification_initial.json").exists()


def test_revision_run_writes_separate_file(tmp_path, manifest_file):
    d = _run_dir(tmp_path, manifest_file, parent="parent_run")
    reply = tmp_path / "reply.txt"
    reply.write_text(json.dumps(_reply()))
    import_chat_response(d, reply, "m", "i", "2026-11-09", json.loads(manifest_file.read_text()))
    assert (d / "classification_revised.json").exists() and not (d / "classification_initial.json").exists()


def test_revision_prompt_needs_feedback(tmp_path):
    review = {"reviewer": "R", "digits": {p: {"feedback_to_ai": None} for p in ("d1", "d2", "d3", "d4", "d5")}}
    (tmp_path / "human_review.json").write_text(json.dumps(review))
    with pytest.raises(SystemExit):
        build_chat_revision_prompt(tmp_path)
    review["digits"]["d2"]["feedback_to_ai"] = "check facts.external_thread_in_cad_model scope"
    (tmp_path / "human_review.json").write_text(json.dumps(review))
    assert "external_thread_in_cad_model" in build_chat_revision_prompt(tmp_path)
