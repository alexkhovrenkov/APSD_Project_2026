"""Stages 3-4 - AI hand-off through the claude.ai web chat.

build_chat_prompt   saved manifest.json + course_rules/* -> one paste-ready prompt (chat_prompt.txt)
import_chat_response  the reply pasted back from the chat -> response_raw.txt, parsed classification,
                      automatic checks, run_meta.json and an empty human_review.json
build_chat_revision_prompt  human feedback (human_review.json) -> message for the same chat

The manifest is embedded verbatim as JSON text. The prompt is built ONLY from
manifest.json + course_rules/*; the frozen manual answer is never read here.
A chat window cannot enforce structured output, so the reply is validated after import.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from .config import PIPELINE_DIR
from .manifest import validate_manifest
from .redact import redact_text

RULES_DIR = PIPELINE_DIR / "course_rules"
OUTPUT_SCHEMA_PATH = PIPELINE_DIR / "schemas" / "opitz_classification.schema.json"
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strict_schema() -> dict:
    """Inline $defs/$ref so the schema is self-contained for strict mode."""
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(x) for x in node]
        return node

    return inline(schema)


def build_inputs(manifest_path: Path) -> tuple[str, list[str], dict]:
    """Return (instructions, input text blocks, context record) shared by the chat and API paths."""
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    validate_manifest(manifest)  # never hand an invalid manifest to the AI

    table_text = (RULES_DIR / "opitz_form_code_table.json").read_text(encoding="utf-8")
    rules_text = (RULES_DIR / "precedence_rules.json").read_text(encoding="utf-8")
    examples_text = (RULES_DIR / "labelled_examples.json").read_text(encoding="utf-8")
    instructions = (RULES_DIR / "system_instructions.md").read_text(encoding="utf-8")
    examples = json.loads(examples_text).get("examples", [])
    validated_examples = [e for e in examples if e.get("validated_by") and e.get("validated_on")]

    table = json.loads(table_text)
    # Send definitions + course source only; the transcription note names the team's manual
    # worksheet file, which the AI must not be pointed to (manual answer stays separate).
    table_for_ai = {"title": table["title"], "source": table["provenance"]["original_source"],
                    "sequence_definition": table["sequence_definition"], "digits": table["digits"]}
    blocks = [
        "=== COURSE OPITZ TABLE (digit definitions) ===\n" + json.dumps(table_for_ai, indent=1, ensure_ascii=False),
        "=== DECISION RULES ===\n" + rules_text,
        "=== LABELLED EXAMPLES (other parts - method only, NOT the target) ===\n"
        + (json.dumps(validated_examples, indent=1) if validated_examples else "No labelled example supplied."),
        "=== TARGET MANIFEST (APSD-LATHE, extracted from Onshape) ===\n" + manifest_text,
        "Return the d1-d5 proposal for the TARGET in the required JSON format.",
    ]
    table_meta = json.loads(table_text)["provenance"]
    rules_meta = json.loads(rules_text)
    context = {
        "manifest_file": str(manifest_path.relative_to(PIPELINE_DIR)) if manifest_path.is_relative_to(PIPELINE_DIR) else str(manifest_path),
        "manifest_sha256": _sha(manifest_text),
        "manifest_evidence_class": manifest.get("evidence_class"),
        "manifest_extraction_run": manifest.get("extraction", {}).get("run_id"),
        "rule_files_sha256": {n: _sha((RULES_DIR / n).read_text(encoding="utf-8"))
                              for n in ("opitz_form_code_table.json", "precedence_rules.json",
                                        "labelled_examples.json", "system_instructions.md")},
        "output_schema_sha256": _sha(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8")),
        "labelled_examples_supplied": len(validated_examples),
        "course_table_transcription_verified_by": table_meta.get("transcription_verified_by"),
        "precedence_rules_approved_by": rules_meta.get("approved_by"),
        "frozen_manual_answer_included": False,
        "credentials_included": False,
    }
    return instructions, blocks, context


CHAT_FORMAT_HEADER = "=== REQUIRED OUTPUT FORMAT ==="


def build_chat_prompt(manifest_path: Path) -> tuple[str, dict]:
    """Claude chat path (team's chosen interface): one paste-ready text with the SAME
    instructions and blocks as the API payload, plus the output JSON Schema written out,
    because a chat window cannot enforce structured output."""
    instructions, blocks, context = build_inputs(manifest_path)
    schema = json.dumps(_strict_schema(), indent=1)
    text = "\n\n".join([
        "=== INSTRUCTIONS ===\n" + instructions.strip(),
        *blocks,
        CHAT_FORMAT_HEADER + "\nReply with ONE JSON object only: no markdown code fences and no text before or "
        "after it. It must validate against this JSON Schema (every field is required; use \"UNKNOWN\" as the digit "
        "when evidence is insufficient):\n" + schema,
    ]) + "\n"
    return text, {**context, "interface": "chat (manual paste)", "chat_prompt_sha256": _sha(text)}


def preflight_problems(context: dict) -> list[str]:
    probs = []
    if context["labelled_examples_supplied"] < 1:
        probs.append("No validated labelled example (brief 1.6.3 b): add one to course_rules/labelled_examples.json.")
    if not context["course_table_transcription_verified_by"]:
        probs.append("Course table transcription not verified: fill provenance.transcription_verified_by/on.")
    if not context["precedence_rules_approved_by"]:
        probs.append("Decision rules are a team draft: fill approved_by/approved_on in course_rules/precedence_rules.json.")
    if context["manifest_evidence_class"] != "LIVE_ONSHAPE_API":
        probs.append("Manifest is not from a live Onshape extraction.")
    return probs


def new_run_dir(output_dir: Path, label: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = output_dir / "ai_runs" / f"{stamp}_{label}"
    d.mkdir(parents=True, exist_ok=False)
    return d


# ---------------------------------------------------------------------------------------
def check_classification(parsed: dict, manifest: dict) -> dict:
    """Structure + evidence-reference checks. Correct structure != correct OPITZ decision."""
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(parsed))
    findings = []
    digits = parsed.get("digits", {}) if isinstance(parsed, dict) else {}
    expected = "".join("U" if digits.get(p, {}).get("digit") == "UNKNOWN" else str(digits.get(p, {}).get("digit", "?"))
                       for p in ("d1", "d2", "d3", "d4", "d5"))
    if parsed.get("form_code") != expected:
        findings.append(f"form_code '{parsed.get('form_code')}' does not match digits -> '{expected}'")
    facts = manifest.get("facts", {})
    n_human = len(manifest.get("human_supplied_facts", []))
    for pos, d in digits.items():
        cited = d.get("feature_evidence", [])
        if d.get("digit") != "UNKNOWN" and not cited:
            findings.append(f"{pos}: digit given without manifest evidence")
        for c in cited:
            ref = c.get("manifest_fact", "")
            key = ref.split("facts.", 1)[-1].split(".")[0].split("[")[0].strip() if "facts." in ref else ref.strip()
            if ref.startswith("human_supplied_facts"):
                if n_human == 0:
                    findings.append(f"{pos}: cites human_supplied_facts but the manifest has none")
                continue
            if key not in facts and key not in ("part", "source"):
                findings.append(f"{pos}: cites '{ref}', which is not a manifest fact")
            elif key in facts and facts[key]["status"] in ("unknown", "not_retrieved") and d.get("digit") != "UNKNOWN":
                findings.append(f"{pos}: uses '{key}' (status {facts[key]['status']}) as evidence for a definite digit")
    return {"schema_valid": not errors, "schema_errors": errors, "evidence_reference_findings": findings,
            "note": "Automatic checks only verify structure and that cited facts exist/are usable. "
                    "They do not verify that the OPITZ decision is correct; that is the human review."}


def _review_template(parsed: dict | None, run_dir: Path) -> dict:
    digits = (parsed or {}).get("digits", {})
    return {
        "record_type": "human_review", "ai_run": run_dir.name,
        "instructions": "Fill in after comparing each AI digit with the drawing and the FROZEN manual worksheet "
                        "(coding/manual_opitz.pdf). Do not edit classification_initial.json.",
        "reviewer": None, "review_date": None,
        "digits": {p: {"ai_initial": digits.get(p, {}).get("digit"), "human_decision": None, "final": None,
                       "agree_with_ai": None, "discrepancy_cause": None,
                       "discrepancy_cause_options": ["ambiguous CAD geometry", "lost export features", "parser failure",
                                                     "misapplied OPITZ rules", "missing manufacturing data",
                                                     "unsupported AI inference"],
                       "feedback_to_ai": None, "comment": None} for p in ("d1", "d2", "d3", "d4", "d5")},
    }


# ---------------------------------------------------------------------------------------
# Claude chat path: import a reply that a team member pasted back from the chat window
# ---------------------------------------------------------------------------------------
def extract_json_reply(text: str) -> tuple[dict | None, str]:
    """Parse the chat reply. Returns (parsed or None, how it was obtained)."""
    t = text.strip()
    try:
        return json.loads(t), "whole reply was JSON"
    except json.JSONDecodeError:
        pass
    if t.startswith("```"):
        inner = t.split("\n", 1)[1] if "\n" in t else ""
        inner = inner.rsplit("```", 1)[0]
        try:
            return json.loads(inner), "JSON inside a markdown code fence (fence removed)"
        except json.JSONDecodeError:
            pass
    a, b = t.find("{"), t.rfind("}")
    if 0 <= a < b:
        try:
            return json.loads(t[a:b + 1]), "JSON object cut out of surrounding text (instruction 'JSON only' not followed)"
        except json.JSONDecodeError:
            pass
    return None, "no valid JSON object found"


def import_chat_response(run_dir: Path, reply_path: Path, model: str, interface: str, used_on: str,
                         manifest: dict, operator: str | None = None, conversation_ref: str | None = None) -> dict:
    ctx = json.loads((run_dir / "payload_context.json").read_text(encoding="utf-8"))
    revised = bool(ctx.get("parent_run"))
    out_name = "classification_revised.json" if revised else "classification_initial.json"
    if (run_dir / out_name).exists() or (run_dir / "response_raw.txt").exists():
        raise SystemExit(f"{run_dir.name} already has an imported reply; runs are never overwritten. "
                         "Prepare a new run instead.")
    raw = reply_path.read_text(encoding="utf-8")
    (run_dir / "response_raw.txt").write_text(redact_text(raw), encoding="utf-8")
    parsed, how = extract_json_reply(raw)
    meta: dict[str, Any] = {
        "provider": "Anthropic", "model_designation": model,
        "model_designation_source": "as shown in the chat interface's model selector, entered by the operator",
        "interface": interface, "date_of_use": used_on, "operator": operator, "conversation_ref": conversation_ref,
        "imported_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "prompt_file": "revision_prompt.txt" if revised else "chat_prompt.txt",
        "chat_prompt_sha256": ctx.get("chat_prompt_sha256"), "manifest_sha256": ctx.get("manifest_sha256"),
        "parent_run": ctx.get("parent_run"), "structured_output_enforcement": "none (chat); checked after import",
        "json_extraction": how, "response_raw_sha256": _sha(raw),
    }
    if parsed is None:
        meta["outcome"] = "invalid_json"
    else:
        checks = check_classification(parsed, manifest)
        (run_dir / out_name).write_text(json.dumps(parsed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (run_dir / "automatic_checks.json").write_text(json.dumps(checks, indent=1) + "\n", encoding="utf-8")
        meta["outcome"] = "parsed" if checks["schema_valid"] else "parsed_but_schema_invalid"
        meta["form_code_proposed"] = parsed.get("form_code")
    review = run_dir / "human_review.json"
    if not review.exists():
        review.write_text(json.dumps(_review_template(parsed, run_dir), indent=1) + "\n", encoding="utf-8")
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def build_chat_revision_prompt(parent_run: Path) -> str:
    """Feedback text to paste into the SAME chat conversation as the parent run."""
    review = json.loads((parent_run / "human_review.json").read_text(encoding="utf-8"))
    fb = {p: d["feedback_to_ai"] for p, d in review["digits"].items() if d.get("feedback_to_ai")}
    if not fb:
        raise SystemExit("human_review.json has no feedback_to_ai entries; nothing to send.")
    return ("Human reviewer feedback on your previous proposal (reviewer: "
            f"{review.get('reviewer') or 'not stated'}). Re-evaluate using ONLY the same manifest, course table and "
            "rules from my first message. Reply again with the complete d1-d5 JSON object only, in the same format.\n"
            + json.dumps(fb, indent=1, ensure_ascii=False) + "\n")
