# Report material: Part C (Q4–Q6)

Draft text for the group report, §4 "Generative AI configuration and implementation". Everything below refers to files in `pipeline/`. Items marked **PENDING** have not happened yet. Do not report them as done.

## Q4 — Architecture and choice

**AI engine.** Anthropic **Claude**, used through the **claude.ai web chat**. The brief (§1.6.1) lists Claude as an option and does not require a paid API. The team originally planned OpenAI ("ChatGPT") and switched to Claude chat to avoid needing an API key. Each run's `run_meta.json` records:
* provider;
* the model name as shown in the chat's model selector;
* interface ("claude.ai web chat (manual paste)");
* date of use and operator;
* hashes of the exact prompt and of the reply.

*PENDING: model designation, date of use and operator, because no Claude run has been made yet.*

**CAD interface.** Path B uses the Onshape REST API (`https://cad.onshape.com/api/v17`) with Python `requests`. Requests are read-only GETs, signed with the team's API keys as documented by Onshape. The data never passes through a neutral CAD file. The format is JSON at every stage: Onshape responses → `manifest.json` → the manifest embedded verbatim in `chat_prompt.txt` → Claude's JSON reply checked against `schemas/opitz_classification.schema.json`.

**Why.**
* The feature list exposes the *design intent*: the revolve axis, sketch dimensions with units, hole specifications (e.g. tapped M16, pitch 2.00 mm) and fillet radii. A STEP or STL file would lose cosmetic thread data and parameter values.
* The microversion is pinned, so every response describes one model state. Revision changes are detected.
* Claude chat needs no API key or billing. The prompt is plain text, so any team member can reproduce the run.

**Reproducibility.**
* Pinned dependency versions (`requirements.txt`).
* Saved, sanitized raw responses with SHA-256 hashes and a request log.
* A schema-validated manifest.
* The exact prompt text, with hashes of the manifest and every rule file.
* The unedited reply (`response_raw.txt`).
* An offline test suite (57 tests, including regression tests on the archived live runs).

A chat window cannot enforce structured output, so the JSON structure is checked **after** import. The chat model can also change over time and outputs vary between conversations, so the first run is archived unchanged and the model name and date are recorded.

**Costs and access.**
* **Onshape:** API keys are personal and stack-specific (`cad.onshape.com`), created under *My account → Developer → API keys* with read permission. Onshape caps API calls per account per year. One extraction makes 11 calls (including the drawing view list).
* **Observed access:**
  * 2026-11-09, anonymous link access: only the document, element list and feature list were readable (parts, configuration, body details and bounding boxes returned HTTP 401).
  * 2026-11-11, signed API keys: every endpoint returned HTTP 200, including body details and the drawing view list.
* **Claude:** a claude.ai account is enough. The available models and usage limits depend on the account's plan.

**Automation achieved.**
* Extraction, manifest, prompt generation, reply validation and archiving each take one command.
* The handoff to Claude is **manual**: a person pastes the prompt and saves the reply. This is semi-automation, and the report should state it plainly.
* Human review and the final code remain manual by design.
* *Current state:* extraction (anonymous, feature list) and prompt preparation have run on live data. The Claude run is PENDING.
* An automated OpenAI Responses API path exists in the code but was not used.

## Q5 — Model adaptation

| Mechanism | Used? | Where |
|---|---|---|
| Contextual prompting (system instructions) | yes | `course_rules/system_instructions.md` (first block of `chat_prompt.txt`) |
| Course rules supplied in context | yes, the full d1–d5 table is included in every prompt; no search/retrieval system | `course_rules/opitz_form_code_table.json` (transcribed from the course-table figure reproduced in `coding/manual_opitz.pdf` p.5; transcription **not yet verified** against the original slide) |
| Precedence / decision rules | yes, **team draft**, not course-supplied | `course_rules/precedence_rules.json` |
| Labelled example(s) | **missing**: required by brief §1.6.3 b | `course_rules/labelled_examples.json` (empty) |
| Scripted geometry extraction | yes | `apsd_pipeline/feature_parser.py`, `geometry_parser.py` |
| Structured output | JSON Schema written into the prompt; not enforced by the chat, validated after import | `schemas/opitz_classification.schema.json`, `automatic_checks.json` |
| Iterative human feedback | supported (`chat-revision` builds the feedback message for the same chat, imported as a new run); **PENDING** | `human_review.json` → `ai_runs/<run>_claude_chat_revised/` |
| Fine-tuning / parameter training | **no**, not performed | n/a |

## Q6 — Evidence chains

Brief chain: CAD feature → parsed attribute → OPITZ rule → AI digit → human decision.
* **Source:** Part Studio tab **APSD-LATHE-Final** (`147da5f19a2ac767328e5f7f`), authenticated run `20261111T113317Z_hmac`, model revision `6b0abea4f2d902aa1ae07fbc`. Earlier runs of the "WIP" tab are archived and not used.
* Links 1–2 are observed. Link 3 lists the *candidate* table rows the evidence touches; it is not a decision. Links 4–5 are PENDING.

### Chain 1 — d1 (part class)
1. **CAD feature:**
   * Evaluated geometry (`07_bodydetails.json`): the coaxial rotational faces share the axis (1, 0, 0) through the origin. The 33 analytic faces span 0.0–0.21 m along it.
   * The largest external coaxial cylinder has radius 0.05 m (`#/bodies/0/faces/8`).
   * **Cross-check:** revolve profile sketch `04_features.json#/features/0`, dimension `"210 mm"` at `#/features/0/constraints/24/parameters/2/expression`.
2. **Parsed:** `facts.overall_axial_length = 210.0 mm`, `facts.max_relevant_diameter = 100.0 mm`, `facts.length_to_diameter_ratio = 2.1`. All three are high confidence, and geometry and feature list agree.
3. **Course table, d1 rows:** "0: L/D ≤ 0.5", "1: 0.5 < L/D < 3", "2: L/D > 3" (rotational parts).
4. **AI digit:** PENDING.
5. **Human decision:** PENDING.

### Chain 2 — d5 (auxiliary holes)
1. **CAD feature:**
   * Evaluated geometry: 6 cylinder faces of radius 0.00176 m (`#/bodies/0/faces/12`, `/14`, `/16`, …), parallel to the part axis and 23 mm from it, at 0°, 60°, …, 300°.
   * Feature list: extrude `Y20` (`04_features.json#/features/3`), REMOVE, BLIND, depth `"4.1 mm"`.
2. **Parsed:**
   * `facts.axial_holes`: 6 × Ø3.52 mm, PCD 46 mm centred on the axis, equally spaced, parallel to the axis (high confidence).
   * `facts.radial_holes`: absent (scope: evaluated geometry).
3. **Course table, d5 rows touched:** "1: Axial, not on pitch circle dia" and "2: Axial on pitch circle diameter".
4. **AI digit:** PENDING.
5. **Human decision:** PENDING.

### Chain 3 — d3 (internal shape)
1. **CAD features:**
   * Evaluated geometry: internal coaxial cylinders Ø12 mm spanning axial 0–41.42 (`#/bodies/0/faces/38`), Ø13 mm spanning 41.42–170 (`/26`) and Ø30 mm spanning 170–210 (`/4`).
   * Helical (non-analytic) faces lie inside the bore at about axial 0–42 (`/34`–`/36`).
   * Feature list: `threadCreator` with `internalThreads = true` and `p = "2 mm"` (`04_features.json#/features/11`). The Hole feature (`#/features/5`) is a custom, untapped hole.
2. **Parsed:**
   * `facts.bores_and_counterbores`: a continuous axial opening end to end with three internal diameters (12 → 13 → 30).
   * `facts.internal_threads`: a modelled internal thread, pitch 2 mm, at the axial-0 end. Its `majorDiameter = 25 mm` parameter is flagged as inconsistent.
3. **Course table, d3 rows touched:** rows with "Thread" for "smooth or stepped on one end" (2) and "stepped both ends" (5). Which applies depends on how the stepped bore is read under the course definitions. This is a human decision, flagged in the AI input via decision rule R6.
4. **AI digit:** PENDING.
5. **Human decision:** PENDING.

### What the AI cannot derive from this geometry alone
* **Thread specification.** The Final model has *modelled* threads: external pitch 5 mm on the Ø50 shaft (about 83–210 mm) and internal pitch 2 mm in the bore.
  * The custom thread feature's `majorDiameter = 25 mm` fits neither thread.
  * Its labels ("1.9685-5.08", "0.5118-12.7") are reported as text only.
  * Designation, standard and tolerance class must come from the APSD-LATHE-Final drawing, as human-verified facts (`human_inputs/drawing_supplement.json`, currently unverified). `facts.external_thread_on_physical_part` stays `unknown` until then.
* **Drawing annotations.** The drawing API returns view definitions only, not dimension text. Callouts need a human reading.
* **Material, initial stock form, tolerances, surface finish.** All four are `unknown`. No material is assigned in Onshape. Supplementary digits d6–d9 are therefore not requested.
* **Model vs drawing.** Mapping axial coordinates to drawing left/right, and checking that the Final model matches the Final drawing, are human checks.
