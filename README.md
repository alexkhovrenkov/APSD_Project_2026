# APSD-LATHE: CAD-to-AI OPITZ classification (Team01)

This repo contains a small pipeline that reads our lathe part from Onshape, describes it as a JSON "feature manifest", and lets Claude propose the OPITZ form code. The team then reviews the proposal.

## How it works

```
Onshape model ──► Python pipeline ──► manifest.json ──► prompt ──► Claude (incognito chat) ──► reply ──► human review ──► final code
                 (read-only API)      (facts + sources)            (copy/paste by a team member)
```

1. **Extract:** Python reads the Part Studio *APSD-LATHE-Final* through the Onshape REST API. It sends only read-only GET requests, signed with our API keys. Responses are saved without keys or personal data.
2. **Manifest:** the script measures the part along its rotation axis (length, diameters, bores, holes, flat faces, threads) and writes `manifest.json`. Every fact says where it came from. Anything the CAD data can't show is marked `UNKNOWN`.
3. **Prompt:** the manifest, the course OPITZ table, our decision rules R1–R9 and one worked example go into a single prompt file.
4. **AI:** a team member pastes the prompt into an incognito claude.ai chat and copies the reply back.
5. **Check and review:** the pipeline validates and archives the reply. The team then compares each digit with the drawing and with our manual answer.

Result: Claude proposed **15212**, which is also our final code. Our manual worksheet said 15202 because we had missed a key on the drawing.

## Run it

```bash
cd pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add your Onshape API keys (never commit .env)

python -m apsd_pipeline extract
python -m apsd_pipeline manifest
python -m apsd_pipeline chat-prompt
# paste chat_prompt.txt into Claude, save the reply as reply.txt, then:
python -m apsd_pipeline import-response output/ai_runs/<run> output/ai_runs/<run>/reply.txt --model "<model>" --operator "<name>"
```

Tests (offline, no keys needed): `python -m pytest -q`

## Where things are

| Folder | What's inside |
|---|---|
| `pipeline/apsd_pipeline/` | The Python code |
| `pipeline/course_rules/` | OPITZ table, decision rules, worked example |
| `pipeline/output/` | Saved API data, `manifest.json`, all AI runs and reviews |
| `report/` | LaTeX sections for the report |
