# Report material: Part C (Q4–Q6)

Draft text for the group report, §4 "Generative AI configuration and implementation" and §5 "Experimental comparison and discussion". Everything below refers to files in `pipeline/`. All items below were observed in the archived runs (status 2026-11-13).

## Q4 — Architecture and choice

**AI engine.** Anthropic **Claude**, used through the **claude.ai web chat**. The brief (§1.6.1) lists Claude as an option and does not require a paid API. The team originally planned OpenAI ("ChatGPT") and switched to Claude chat to avoid needing an API key. Each run's `run_meta.json` records:
* provider;
* the model name as shown in the chat's model selector;
* interface ("claude.ai web chat (manual paste)");
* date of use and operator;
* hashes of the exact prompt and of the reply.

**Recorded:** model **Opus 5.5 High** (as shown in the claude.ai model selector), operator Julissa M Barahona. Dates of use: initial run 2026-11-11, feedback round and sensitivity test 2026-11-13. The model name and operator were first imported as placeholders and then corrected with a logged entry (`run_meta.json` → `metadata_corrections`).

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
* *Current state:* authenticated extraction of the APSD-LATHE-Final tab, the initial Claude run, one feedback round and one sensitivity test have all run on live data.
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
| Iterative human feedback | used once (`chat-revision`): feedback on d4 sent in the same chat, imported as `20261111T124734Z_claude_chat_revised` | `human_review.json` → `ai_runs/<run>_claude_chat_revised/` |
| Fine-tuning / parameter training | **no**, not performed | n/a |

## Q6 — Evidence chains

Brief chain: CAD feature → parsed attribute → OPITZ rule → AI digit → human decision.
* **Source:** Part Studio tab **APSD-LATHE-Final** (`147da5f19a2ac767328e5f7f`), authenticated run `20261111T113317Z_hmac`, model revision `6b0abea4f2d902aa1ae07fbc`. Earlier runs of the "WIP" tab are archived and not used.
* All five links are observed. AI digits come from `20261111T120647Z_claude_chat_initial/classification_initial.json`; human decisions come from the `human_review.json` files of that run and of the revised run.

### Chain 1 — d1 (part class)
1. **CAD feature:**
   * Evaluated geometry (`07_bodydetails.json`): the coaxial rotational faces share the axis (1, 0, 0) through the origin. The 33 analytic faces span 0.0–0.21 m along it.
   * The largest external coaxial cylinder has radius 0.05 m (`#/bodies/0/faces/8`).
   * **Cross-check:** revolve profile sketch `04_features.json#/features/0`, dimension `"210 mm"` at `#/features/0/constraints/24/parameters/2/expression`.
2. **Parsed:** `facts.overall_axial_length = 210.0 mm`, `facts.max_relevant_diameter = 100.0 mm`, `facts.length_to_diameter_ratio = 2.1`. All three are high confidence, and geometry and feature list agree.
3. **Course table, d1 rows:** "0: L/D ≤ 0.5", "1: 0.5 < L/D < 3", "2: L/D > 3" (rotational parts).
4. **AI digit:** 1, row "Rotational parts: 0.5 < L/D < 3", low uncertainty.
5. **Human decision:** 1, agreed (Alexei Khovrenkov & Ezgi Kutlu, 11-11-2026).

### Chain 2 — d5 (auxiliary holes)
1. **CAD feature:**
   * Evaluated geometry: 6 cylinder faces of radius 0.00176 m (`#/bodies/0/faces/12`, `/14`, `/16`, …), parallel to the part axis and 23 mm from it, at 0°, 60°, …, 300°.
   * Feature list: extrude `Y20` (`04_features.json#/features/3`), REMOVE, BLIND, depth `"4.1 mm"`.
2. **Parsed:**
   * `facts.axial_holes`: 6 × Ø3.52 mm, PCD 46 mm centred on the axis, equally spaced, parallel to the axis (high confidence).
   * `facts.radial_holes`: absent (scope: evaluated geometry).
3. **Course table, d5 rows touched:** "1: Axial, not on pitch circle dia" and "2: Axial on pitch circle diameter".
4. **AI digit:** 2, row "Axial on pitch circle diameter", low uncertainty.
5. **Human decision:** 2, agreed. In the sensitivity test, removing these holes changed d5 to 0, as the team predicted.

### Chain 3 — d3 (internal shape)
1. **CAD features:**
   * Evaluated geometry: internal coaxial cylinders Ø12 mm spanning axial 0–41.42 (`#/bodies/0/faces/38`), Ø13 mm spanning 41.42–170 (`/26`) and Ø30 mm spanning 170–210 (`/4`).
   * Helical (non-analytic) faces lie inside the bore at about axial 0–42 (`/34`–`/36`).
   * Feature list: `threadCreator` with `internalThreads = true` and `p = "2 mm"` (`04_features.json#/features/11`). The Hole feature (`#/features/5`) is a custom, untapped hole.
2. **Parsed:**
   * `facts.bores_and_counterbores`: a continuous axial opening end to end with three internal diameters (12 → 13 → 30).
   * `facts.internal_threads`: a modelled internal thread, pitch 2 mm, at the axial-0 end. Its `majorDiameter = 25 mm` parameter is flagged as inconsistent.
3. **Course table, d3 rows touched:** rows with "Thread" for "smooth or stepped on one end" (2) and "stepped both ends" (5). Which applies depends on how the stepped bore is read under the course definitions. This is a human decision, flagged in the AI input via decision rule R6.
4. **AI digit:** 2, row "Smooth or stepped on one end: Thread", **high** uncertainty. Competing rows: 5 (if the thread major diameter exceeds Ø13) and 1 (if the drawing has no internal thread).
5. **Human decision:** 2, agreed. Open point: confirm the internal thread on the drawing.

### Chain 4 — d4 (plane surfaces), including the correction trace
1. **CAD feature:** three planar faces parallel to the axis on the Ø29.9 end section, axial 0–20 mm (`07_bodydetails.json#/bodies/0/faces/27`–`29`). They are created by an ADD extrude (`04_features.json#/features/9`), which forms a raised key.
2. **Parsed:** `facts.plane_surface_features`.
3. **Course table, d4 rows touched:** 1 "Surface plane/curved", 5, 9.
4. **AI digits over time:**
   * Initial answer: 1.
   * After the team's feedback that the faces are not machined: **UNKNOWN**. The machining status is not in the manifest, so decision rule R2 applies.
   * Sensitivity run, fresh chat: 1 again.
5. **Human decisions over time:**
   * 11-11: 0. The key had been missed in the manual worksheet.
   * 13-11: after re-checking the APSD-LATHE-Final drawing, **1**. The key's faces are treated as machined plane surfaces.

### What the AI cannot derive from this geometry alone
* **Thread specification.** The Final model has *modelled* threads: external pitch 5 mm on the Ø50 shaft (about 83–210 mm) and internal pitch 2 mm in the bore.
  * The custom thread feature's `majorDiameter = 25 mm` fits neither thread.
  * Its labels ("1.9685-5.08", "0.5118-12.7") are reported as text only.
  * Designation, standard and tolerance class must come from the APSD-LATHE-Final drawing, as human-verified facts (`human_inputs/drawing_supplement.json`, currently unverified). `facts.external_thread_on_physical_part` stays `unknown` until then.
* **Drawing annotations.** The drawing API returns view definitions only, not dimension text. Callouts need a human reading.
* **Material, initial stock form, tolerances, surface finish.** All four are `unknown`. No material is assigned in Onshape. Supplementary digits d6–d9 are therefore not requested.
* **Model vs drawing.** Mapping axial coordinates to drawing left/right, and checking that the Final model matches the Final drawing, are human checks.

## Part D — Q7 Reliability

Comparison of the frozen manual worksheet (`coding/manual_opitz.pdf`, 15202) with the AI's **initial** output (15212):

| Position | Manual | AI initial | Final | Root cause / justification |
|---|---|---|---|---|
| d1 | 1 | 1 | 1 | agree |
| d2 | 5 | 5 | 5 | agree (thread confirmed by a verified drawing fact) |
| d3 | 2 | 2 | 2 | agree (AI flagged high uncertainty: internal thread to be confirmed on the drawing) |
| d4 | 0 | 1 | 1 | The manual analysis missed the raised key on the small end. The AI was right; a human error was corrected after re-checking the drawing |
| d5 | 2 | 2 | 2 | agree |

* **Initial agreement:** 4 of 5 digits. A_K numerator 4, denominator 5. No digit was excluded, since all five form-code positions were coded by both sides from the same part.
* **Counting convention:** stated by the team. The brief asks for numerator and denominator but this file does not quote a course formula.
* Supplementary digits d6–d9 were not compared: material, stock and accuracy are not in the AI's input (decision rule R8).

**Discrepancy and problem log** (causes from the brief's list):

| Item | Cause |
|---|---|
| d4 initial disagreement | Human analysis error, not one of the brief's AI causes. The AI's revised UNKNOWN came from **missing manufacturing data** (machining status), after the team's incorrect feedback |
| Hole feature parameter Ø14 vs evaluated Ø12; `threadCreator majorDiameter` = 25 mm; internal thread faces reaching about 42.3 mm vs the 41.42 mm step | **Ambiguous CAD geometry** (model inconsistencies, for the CAD owner) |
| `axial_holes` evidence list cut to 3 of 6 faces; "Extrude 1" not linked to the bore fact (both flagged by the AI) | **Parser failure** (reporting limitation of the pipeline) |
| Course table row 8 printed as "Operating Speed" (the AI suspects "operating thread") | Transcribed as printed; worth asking the instructor |

**Human intervention (brief 1.7.1 d):** the d4 feedback round changed the AI's d4 from 1 to UNKNOWN (`20261111T124734Z_claude_chat_revised`). The final decision was then corrected by the team. All runs are archived unchanged.

## Part D — Q8 Sensitivity test and generalisation

* **Controlled change:** in a copy of the model (tab `APSD-LATHE-Final-Sensitivity-Test`) the six Ø3.52 axial holes were removed. The manifest comparison shows exactly one changed fact (`axial_holes`) and two removed features. See `output/sensitivity/20261113T103041Z_remove-axial-holes/change_summary.json`.
* **Prediction before querying:** 15210 (d5: 2 → 0), by Alexei Khovrenkov. It was locked at 2026-11-13 10:33:10 UTC with a SHA-256 fingerprint in `payload_context.json`.
* **Result:** Claude, in a fresh chat with the same model, returned **15210**. The prediction was met on all 5 digits. Only d5 changed, and d4 = 1 was independently reproduced (`result.json`).
* **Reusable for other rotational parts:**
  * authenticated extraction;
  * axis-aware geometry analysis (diameters, bores, holes, flats, threads);
  * the manifest with its UNKNOWN conventions;
  * the course table, rules and prompt;
  * import and automatic checks;
  * the sensitivity tooling.
* **Still needs a human:**
  * reading drawing callouts (the API gives no annotation text);
  * manufacturing assumptions (machining status, stock, material, tolerances);
  * resolving model inconsistencies;
  * judging borderline table rows (d3, d4);
  * the final code.
* **Failure modes seen:**
  * non-analytic thread faces with loose bounding boxes (handled);
  * undocumented API fields (face orientation, query encoding);
  * third-party thread-feature parameters;
  * wrong human feedback, which propagates to the AI (it answered UNKNOWN rather than inventing).
