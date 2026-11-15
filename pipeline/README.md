# Pipeline: from the Onshape model to an OPITZ code

This folder holds our Python pipeline for Part C (Path B). It reads our lathe part from Onshape, turns it into a list of facts (`manifest.json`), and prepares a prompt for Claude. Claude proposes the OPITZ code, and the team checks it.

## The idea in 5 steps

1. **Read the model.** The script connects to Onshape with our API keys and downloads the part data (features and geometry). It can only *read*; it cannot change the model.
2. **Make the manifest.** The script measures the part (length, diameters, bores, holes, flat faces, threads) and writes `manifest.json`. Every value says where it came from. Anything the CAD data can't show, like material or tolerances, is written as `UNKNOWN`.
3. **Make the prompt.** The manifest, the OPITZ table from the course slides, our rules R1–R9 and one worked example are put into one text file, `chat_prompt.txt`.
4. **Ask Claude.** A team member pastes the prompt into an **incognito** chat on claude.ai, so old chats can't influence the answer, and copies Claude's reply back into `reply.txt`.
5. **Check and review.** The script checks the reply's format and saves everything. Then the team compares each digit with the drawing and with our manual answer.

## How to run it

**Setup (once):**
```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
Open `.env` and add your Onshape API keys (Onshape → My account → Developer → API keys). Never share or commit this file.

**Run:**
```bash
python -m apsd_pipeline extract        # 1. read the model from Onshape
python -m apsd_pipeline manifest       # 2. write output/manifest.json
python -m apsd_pipeline chat-prompt    # 3. write the prompt file
```
Then paste the prompt into Claude, save the reply as `reply.txt` in the run folder, and import it:
```bash
python -m apsd_pipeline import-response output/ai_runs/<run> output/ai_runs/<run>/reply.txt --model "Opus 5.5 High" --operator "<your name>"
```

**Tests** (offline, no keys needed): `python -m pytest -q`

## What's in this folder

| Path | What it is |
|---|---|
| `apsd_pipeline/` | The Python code |
| `course_rules/` | OPITZ table, our rules R1–R9, worked example, instructions for the AI |
| `human_inputs/` | Facts we read from the drawing ourselves (the external thread) |
| `schemas/` | The required format of the manifest and of Claude's reply |
| `manifest.json` | The manifest of our final part |
| `output/evidence/` | Saved Onshape data from each extraction, with keys and names removed |
| `output/ai_runs/` | Each AI run: prompt, Claude's reply, checks and human review |
| `output/sensitivity/` | The sensitivity test (six holes removed) |
| `tests/` | Automatic tests |

## Our results

| Run | Code |
|---|---|
| Manual worksheet (done before the AI) | 15202 |
| Claude, first answer | 15212 |
| Claude, after our feedback on d4 | 152U2 |
| **Final team code** | **15212** |
| Sensitivity test (six holes removed; we predicted 15210) | 15210 |

The manual answer differed only in d4, because we had missed a key on the drawing. Claude's first answer was right.
