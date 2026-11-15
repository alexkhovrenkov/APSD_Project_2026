# AI model information

| Item | Value |
|---|---|
| Provider | Anthropic |
| Model | Claude **Opus 5.5 High** (name as shown in the claude.ai model selector) |
| Interface | claude.ai web chat, **incognito**, outside any Project, no attached files; prompt pasted manually |
| Operator | Julissa M Barahona |
| Training / fine-tuning | None. The model was configured only through the prompt (contextual prompting) |

## Runs

| # | Run | Date of use | Chat | Input | AI code |
|---|---|---|---|---|---|
| 1 | Initial | 2026-11-11 | new incognito chat | `prompts/01_initial_prompt.txt` | 15212 |
| 2 | Feedback round | 2026-11-13 | same chat as run 1 | `prompts/02_feedback_round_prompt.txt` | 152U2 |
| 3 | Sensitivity test | 2026-11-13 | new incognito chat | `prompts/03_sensitivity_test_prompt.txt` | 15210 |

Final team code: **15212**. Frozen manual answer: 15202 (`coding/manual_opitz.pdf`), never sent to the AI.

Why incognito: the account held earlier conversations about this project and other material that could have influenced the answer.

## Contents of this folder

- `prompts/`: the exact text sent to Claude in each run.
- `outputs/`: Claude's unedited replies (`response_raw.txt`), the parsed classification, automatic checks, run metadata and the human review, for runs 1 and 2.
- `test_case/`: the sensitivity test (six axial holes removed). The prediction (locked before the query), the manifest change, the AI result (`ai_*` files) and the evaluation (`result.json`).

These are copies. The originals, written by the pipeline, are in `pipeline/output/ai_runs/` and `pipeline/output/sensitivity/`.

Other AI use: Claude Code (Opus 5.5, in VS Code) helped write the pipeline, tests and documentation, and drafted report text; see the AI-use disclosure in the report.
