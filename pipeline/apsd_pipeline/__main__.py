"""Command line: python -m apsd_pipeline <command>

  check-env              show which settings are present (never their values)
  extract [--auth MODE]  Onshape API -> output/evidence/<run>/   (MODE: hmac|basic|anonymous)
  manifest [EVIDENCE]    evidence -> output/manifest.json (validated); default: latest evidence run

  Claude chat path (team's chosen AI interface):
  chat-prompt [--allow-preflight-gaps]
                         manifest.json -> output/ai_runs/<run>_claude_chat_initial/chat_prompt.txt
  import-response RUN_DIR REPLY_FILE --model NAME [--date YYYY-MM-DD] [--operator NAME]
                         validate + archive Claude's pasted reply in RUN_DIR
  correct-run-meta RUN_DIR --reason TEXT [--model NAME] [--operator NAME] [--date YYYY-MM-DD]
                         fix operator-entered metadata in run_meta.json (logged, reply untouched)
  chat-revision RUN_DIR  human feedback (RUN_DIR/human_review.json) -> NEW run with revision_prompt.txt

  Alternative, not used by the team (OpenAI Responses API, needs OPENAI_API_KEY/OPENAI_MODEL):
  payload | classify [--allow-preflight-gaps] | revise RUN_DIR
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import PIPELINE_DIR, ConfigError, get_onshape_credentials, get_openai_settings, get_target, load_dotenv_if_present

OUTPUT_DIR = Path(os.environ.get("APSD_OUTPUT_DIR", PIPELINE_DIR / "output"))
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"


def _latest_evidence() -> Path:
    runs = sorted((OUTPUT_DIR / "evidence").glob("*/extraction_record.json"))
    if not runs:
        raise SystemExit("No evidence found. Run: python -m apsd_pipeline extract")
    return runs[-1].parent


def cmd_check_env(_args) -> None:
    for name in ("ONSHAPE_DOCUMENT_URL", "ONSHAPE_AUTH_MODE", "ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY",
                 "OPENAI_API_KEY", "OPENAI_MODEL"):
        print(f"{name:22s} {'set' if os.environ.get(name, '').strip() else 'not set'}")
    t = get_target()
    print(f"\nTarget: host={t.host} document={t.document_id} {t.wvm}={t.wvm_id} element={t.element_id}")


def cmd_extract(args) -> None:
    from .extract import run_extraction

    creds = get_onshape_credentials(args.auth)
    if creds.mode == "anonymous":
        print("NOTE: anonymous mode only reads what the document's link sharing exposes; "
              "endpoints needing keys will be recorded as not retrieved.")
    from .config import get_drawing_element_id

    run_extraction(get_target(), creds, OUTPUT_DIR, drawing_element_id=get_drawing_element_id())


def cmd_manifest(args) -> None:
    from .manifest import build_manifest, write_manifest

    ev = Path(args.evidence) if args.evidence else _latest_evidence()
    manifest = build_manifest(ev)
    write_manifest(manifest, MANIFEST_PATH)
    print(f"manifest.json written and validated against schemas/feature_manifest.schema.json -> {MANIFEST_PATH}")
    f = manifest["facts"]
    for k in ("overall_axial_length", "max_relevant_diameter", "length_to_diameter_ratio"):
        print(f"  {k:28s} {f[k]['value']} {f[k]['unit'] or ''}  [{f[k]['status']}, {f[k]['confidence']}]")
    print(f"  unknown / not retrieved: {', '.join(u['fact'] for u in manifest['unknowns']) or 'none'}")


def _prepare(label: str, allow_gaps: bool) -> tuple:
    from .ai_handoff import build_payload, new_run_dir, preflight_problems, save_payload

    model = os.environ.get("OPENAI_MODEL", "").strip() or "<OPENAI_MODEL not set>"
    payload, context = build_payload(MANIFEST_PATH, model)
    problems = preflight_problems(context)
    for p in problems:
        print(f"  PREFLIGHT: {p}")
    if problems and label != "dryrun" and not allow_gaps:
        raise SystemExit("Live classification stopped because of the preflight items above. Fix them, or re-run "
                         "with --allow-preflight-gaps to record them as accepted deviations.")
    run_dir = new_run_dir(OUTPUT_DIR, label)
    save_payload(run_dir, payload, context, problems if (allow_gaps or label == "dryrun") else [])
    return payload, context, run_dir


def cmd_chat_prompt(args) -> None:
    from .ai_handoff import build_chat_prompt, new_run_dir, preflight_problems, save_payload

    text, context = build_chat_prompt(MANIFEST_PATH)
    problems = preflight_problems(context)
    for p in problems:
        print(f"  PREFLIGHT: {p}")
    if problems and not args.allow_preflight_gaps:
        raise SystemExit("Stopped because of the preflight items above. Fix them, or re-run with "
                         "--allow-preflight-gaps to record them as accepted deviations.")
    run_dir = new_run_dir(OUTPUT_DIR, "claude_chat_initial")
    (run_dir / "chat_prompt.txt").write_text(text, encoding="utf-8")
    ctx = {k: v for k, v in context.items()}
    (run_dir / "payload_context.json").write_text(
        json.dumps({**ctx, "preflight_deviations_accepted": problems}, indent=1) + "\n", encoding="utf-8")
    print(f"""
Prompt ready: {run_dir / 'chat_prompt.txt'}  ({len(text):,} characters)

1. Open a NEW conversation on claude.ai (no Project, no attached files, no earlier messages).
2. Note the model name shown in the model selector.
3. Paste the whole file as ONE message and send it.
4. Copy Claude's complete reply into a text file, e.g. {run_dir / 'reply.txt'}
5. python -m apsd_pipeline import-response {run_dir} {run_dir / 'reply.txt'} --model "<model name>" --operator "<your name>"
""")


def cmd_import_response(args) -> None:
    import datetime as dt

    from .ai_handoff import import_chat_response

    run_dir = Path(args.run_dir).resolve()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    ctx = json.loads((run_dir / "payload_context.json").read_text(encoding="utf-8"))
    if ctx.get("manifest_sha256") and ctx["manifest_sha256"] != __import__("hashlib").sha256(
            MANIFEST_PATH.read_text(encoding="utf-8").encode()).hexdigest():
        raise SystemExit("output/manifest.json changed since this prompt was prepared; checks would use the wrong "
                         "manifest. Restore it or prepare a new run.")
    used_on = args.date or dt.date.today().isoformat()
    meta = import_chat_response(run_dir, Path(args.reply_file), args.model, args.interface, used_on, manifest,
                                args.operator, args.conversation_ref)
    print(json.dumps({k: meta.get(k) for k in ("outcome", "json_extraction", "form_code_proposed",
                                                "model_designation", "date_of_use")}, indent=1))
    print(f"Archived in {run_dir}. Next: fill human_review.json there (do not edit the classification file).")


def cmd_correct_run_meta(args) -> None:
    """Correct operator-entered metadata (model name, operator, date, conversation ref) in run_meta.json.
    The reply and classification files are never touched; every change is logged with old and new value."""
    import datetime as dt

    run_dir = Path(args.run_dir).resolve()
    meta_path = run_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    changes = {"model_designation": args.model, "operator": args.operator, "date_of_use": args.date,
               "conversation_ref": args.conversation_ref}
    log = meta.setdefault("metadata_corrections", [])
    for field, new in changes.items():
        if new is not None and meta.get(field) != new:
            log.append({"field": field, "old": meta.get(field), "new": new, "reason": args.reason,
                        "corrected_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            meta[field] = new
    meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: meta.get(k) for k in ("model_designation", "operator", "date_of_use")}, indent=1))
    print(f"{len(log)} correction(s) logged in {meta_path}")


def cmd_chat_revision(args) -> None:
    from .ai_handoff import build_chat_revision_prompt, new_run_dir

    parent = Path(args.run_dir).resolve()
    text = build_chat_revision_prompt(parent)
    ctx = json.loads((parent / "payload_context.json").read_text(encoding="utf-8"))
    ctx.update({"parent_run": parent.name, "chat_prompt_sha256": __import__("hashlib").sha256(text.encode()).hexdigest()})
    run_dir = new_run_dir(OUTPUT_DIR, "claude_chat_revised")
    (run_dir / "revision_prompt.txt").write_text(text, encoding="utf-8")
    (run_dir / "payload_context.json").write_text(json.dumps(ctx, indent=1) + "\n", encoding="utf-8")
    print(f"""
Revision prompt ready: {run_dir / 'revision_prompt.txt'}
Paste it into the SAME claude.ai conversation as {parent.name}, save the reply, then:
python -m apsd_pipeline import-response {run_dir} <reply file> --model "<model name>" --operator "<your name>"
""")


def cmd_payload(_args) -> None:
    _, _, run_dir = _prepare("dryrun", True)
    print(f"Exact request payload saved (NOT sent): {run_dir / 'request_payload.json'}")


def cmd_classify(args) -> None:
    from .ai_handoff import run_classification

    key, _model = get_openai_settings()
    payload, context, run_dir = _prepare("initial", args.allow_preflight_gaps)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    meta = run_classification(payload, context, run_dir, key, manifest)
    print(json.dumps({k: meta.get(k) for k in ("outcome", "model_returned", "response_id", "request_id",
                                                "form_code_proposed")}, indent=1))
    print(f"Saved to {run_dir}. Next: fill human_review.json in that folder.")


def cmd_revise(args) -> None:
    from .ai_handoff import build_revision_payload, new_run_dir, run_classification, save_payload

    key, _ = get_openai_settings()
    parent = Path(args.run_dir).resolve()
    payload, feedback = build_revision_payload(parent)
    ctx = json.loads((parent / "payload_context.json").read_text(encoding="utf-8"))
    ctx["parent_run"] = parent.name
    run_dir = new_run_dir(OUTPUT_DIR, "revised")
    save_payload(run_dir, payload, ctx, ctx.get("preflight_deviations_accepted", []))
    (run_dir / "human_feedback_sent.txt").write_text(feedback, encoding="utf-8")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    meta = run_classification(payload, ctx, run_dir, key, manifest)
    print(json.dumps({k: meta.get(k) for k in ("outcome", "form_code_proposed", "response_id")}, indent=1))


def main(argv: list[str] | None = None) -> None:
    load_dotenv_if_present()
    ap = argparse.ArgumentParser(prog="python -m apsd_pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-env").set_defaults(fn=cmd_check_env)
    p = sub.add_parser("extract")
    p.add_argument("--auth", choices=["hmac", "basic", "anonymous"], default=None)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("manifest")
    p.add_argument("evidence", nargs="?")
    p.set_defaults(fn=cmd_manifest)
    p = sub.add_parser("chat-prompt")
    p.add_argument("--allow-preflight-gaps", action="store_true")
    p.set_defaults(fn=cmd_chat_prompt)
    p = sub.add_parser("import-response")
    p.add_argument("run_dir")
    p.add_argument("reply_file")
    p.add_argument("--model", required=True, help="model name exactly as shown in the chat model selector")
    p.add_argument("--interface", default="claude.ai web chat (manual paste)")
    p.add_argument("--date", default=None, help="date of use, YYYY-MM-DD (default: today)")
    p.add_argument("--operator", default=None, help="team member who ran the chat")
    p.add_argument("--conversation-ref", default=None, help="optional share link or note identifying the chat")
    p.set_defaults(fn=cmd_import_response)
    p = sub.add_parser("correct-run-meta")
    p.add_argument("run_dir")
    p.add_argument("--model", default=None)
    p.add_argument("--operator", default=None)
    p.add_argument("--date", default=None)
    p.add_argument("--conversation-ref", default=None)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_correct_run_meta)
    p = sub.add_parser("chat-revision")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_chat_revision)
    sub.add_parser("payload").set_defaults(fn=cmd_payload)
    p = sub.add_parser("classify")
    p.add_argument("--allow-preflight-gaps", action="store_true")
    p.set_defaults(fn=cmd_classify)
    p = sub.add_parser("revise")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_revise)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except ConfigError as exc:
        sys.exit(f"Configuration problem: {exc}")


if __name__ == "__main__":
    main()
