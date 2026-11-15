# Validation: digit comparison and error analysis

Sources: frozen manual worksheet `coding/manual_opitz.pdf`, AI runs in `ai_records/outputs/`, sensitivity test in `ai_records/test_case/`. The per-digit table is in `digit_comparison.csv`.

## Codes

| | Code |
|---|---|
| Manual worksheet (frozen before the AI runs) | 15202 |
| AI initial (11 Nov 2026) | 15212 |
| AI after feedback round (13 Nov 2026) | 152U2 |
| **Final team code** | **15212** |

## Reliability

**A_K = n_agree / n_applicable = 4 / 5 = 0.8**

- 4 of the 5 form-code digits of the AI's initial output agree with the manual worksheet; only d4 differs.
- No form-code digit was excluded: both sides coded all five positions, and the AI's initial output had no UNKNOWN.
- Supplementary digits d6–d9 are not counted: material, stock and accuracy are not in the CAD data, and the AI was told not to assign them (rule R8).
- The d4 difference was a human error, so the AI's initial output matches the final code on 5 of 5 digits.

## Error analysis (causes from the brief)

- **Ambiguous CAD geometry:** present but harmless. Two feature parameters contradict the geometry (hole Ø14 vs. Ø12; thread major diameter 25 mm); the pipeline uses the evaluated geometry, so no digit was affected.
- **Lost export features:** not applicable; no file was exported, and the API returns the native feature list and B-rep geometry.
- **Parser failure:** minor only. The axial-hole evidence cites 3 of the 6 hole faces, and the extrude feature is not linked to the bore fact; Claude noted both, and neither affected a digit.
- **Misapplied OPITZ rules:** blamed by the reviewers for d4, but the AI applied row 1 correctly to the key faces.
- **Missing manufacturing data:** caused the revised UNKNOWN for d4 and prevents coding d6–d9.
- **Unsupported AI inference:** none. Every digit cited existing manifest facts, and the automatic checks found no invalid references in any run.

## Sensitivity test

- **Change:** six axial holes (6 × Ø3.52 on PCD 46 mm) removed in a copy of the model (tab APSD-LATHE-Final-Sensitivity-Test).
- **Prediction (locked before the query):** 15210, by Alexei Khovrenkov, 13 Nov 2026.
- **AI result (new incognito chat):** 15210. Only d5 changed (2 → 0); the prediction was met on 5 of 5 digits.
