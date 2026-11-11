"""APSD-LATHE Part C, Path B: Onshape API -> manifest.json -> OpenAI Responses API.

Stages (each runnable on its own, see README.md):
    extract   Onshape REST API (read-only GET) -> sanitized evidence files
    manifest  evidence files -> validated manifest.json
    payload   manifest.json + course rules -> exact OpenAI request payload
    classify  payload -> OpenAI Responses API -> raw + parsed output (saved separately)
"""

__version__ = "1.0.0"
