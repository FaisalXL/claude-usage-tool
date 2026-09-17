# claude-usage-tool

Measures how much a student actually iterates with Claude Code on their coursework — not just whether they used it once. Reads local session logs, requires no telemetry, server, or account access, and works automatically at class scale.

## Why

Courses that expect students to rely on Claude Code as a constant, iterative collaborator have no easy way to check whether usage actually looks like that. This tool is a meter, not an enforcement mechanism — it gives students and the course a concrete number to check against.

## Setup

Once, from your course root — the top-level folder containing all your assignment folders, not one specific assignment:

macOS / Linux:
```bash
curl -O https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/install.py
python3 install.py
```
Windows (plain `curl` is aliased to `Invoke-WebRequest` in PowerShell, so use `curl.exe`):
```
curl.exe -O https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/install.py
python install.py
```

This installs `weekly_report.py` and `SKILL.md` under `.claude/` in your course root, plus a personal copy of `SKILL.md` in your home directory (needed so `/weekly-report` still works from an assignment subfolder that has its own nested git repo — Claude Code's own skill discovery stops at a git boundary, this doesn't). The installer is Python, not shell, so it behaves the same in cmd.exe, PowerShell, and bash. It only touches the files it needs.

After this, everything stays current automatically — every run checks GitHub for a newer script and `SKILL.md`, updates in place, and fails silently if GitHub is unreachable. No reinstall needed after a fix ships, except once to bootstrap an install old enough to predate this mechanism.

## Usage

Run this once per week, from anywhere inside the course folder — the repo root or any assignment subfolder both work, since it always reports on the whole course:

```
/weekly-report
```

or, running the script directly (from your course root):

```bash
python3 .claude/weekly_report.py --days-back 7
```

Defaults to the last 7 days. To catch up after missing a week, ask for more days back (`/weekly-report 14`) or pass `--days-back 14` yourself; `--since YYYY-MM-DD` also works. Writes `usage_reports/<date>.json` and `usage_reports/<date>.transcript.jsonl`. Submit the `.json`; keep the transcript around locally — it's what lets a report be verified against tampering if one is ever flagged.

## What the report includes

- **`token_usage`** — split into `effective` (input + newly-cached context + output — real new work) and `cache_reread` (context replayed from cache, billed at a steep discount, not new work). `effective` is the number meant to be thresholded.
- **`daily_breakdown`** / **`assignment_breakdown`** — the same aggregate stats (tokens, model usage, code_ratio, tool counts), rebucketed by calendar day and by top-level assignment folder. Both are views over one whole-course scan, not a way to narrow it — no flag or working directory changes what counts as in scope.
- **`model_usage`**, **`code_ratio`**, **`file_overlap`** — per-model token counts, and how much activity maps to real edits in the course repo vs. filler.
- **`limit_hit_episodes`** — distinct rate-limit hits, deduplicated, with burst-vs-baseline tool-call ratios around each one.
- **`integrity_hash_sha256`** — sha256 of the paired transcript, for spot-checking a submitted report against its transcript.

## Known limitations

- `token_usage.effective` is this tool's own methodology, not an Anthropic-published metric — Anthropic documents the four raw usage fields and their billing ([prompt-caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [messages/create](https://platform.claude.com/docs/en/api/messages/create)) but only publishes the trivial sum as a combining formula. It doesn't normalize for model choice or task shape.
- The burst check catches fast, parallel gaming; `code_ratio` and `file_overlap` are what catch slow, sequential gaming.
- The transcript file contains full conversation content — prompts and tool outputs, not just metrics. Students should know this is collected.

## Validation

Detection schema and burst logic were validated against a real, deliberately-triggered rate-limit event (`"error": "rate_limit"`, `apiErrorStatus: 429`), not from documentation alone.
