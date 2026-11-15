"""APSD-LATHE Part C, Path B: Onshape API -> manifest.json -> Claude (claude.ai chat).

Stages (each runnable on its own, see README.md):
    extract          Onshape REST API (read-only GET) -> sanitized evidence files
    manifest         evidence files -> validated manifest.json
    chat-prompt      manifest.json + course rules -> exact prompt for the claude.ai chat
    import-response  Claude's reply -> raw reply + parsed classification + checks (saved separately)
    chat-revision    human feedback -> follow-up message for the same chat
    sensitivity-*    controlled-change test with a locked prediction
"""

__version__ = "1.1.0"
