# NADZOR handoff history

This branch stores sanitized review history and compact trend metrics only. Raw runtime logs, document names and local paths stay out of GitHub.

- `history/` keeps sanitized snapshots.
- `trend.json` keeps compact chronological metrics for regression/improvement comparison.
- Working code remains on `claude/new-session-d44es2`; this data branch must not be merged into `main` automatically.
