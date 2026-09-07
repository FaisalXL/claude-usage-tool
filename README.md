# claude-usage-tool

This tool verifies purposeful Claude Code usage for coursework. It tracks per-model token usage. It detects real rate-limit hits. It flags gamed usage. It requires no manual log review.

## The problem

Assignment include hitting the rate limit multiple times per week. Students can game these requirements. The agents burn tokens without doing real work. This tool verifies purposeful usage. It works automatically at class scale.
## How it works

The tool is a Claude Code Skill. Students run `/weekly-report` once per week. The skill reads local session logs at `~/.claude/projects/**/*.jsonl`. It requires no telemetry, server, or account access. The skill produces two files:

- **`usage_reports/<week>.json`** — the computed report.
- **`usage_reports/<week>.transcript.jsonl`** — the raw session data behind it.

A hash links both files. The hash detects tampering with either file.

### What the report includes

- **Model usage.** The report lists exact token counts per model, per week.
- **Rate-limit-hit count.** The report counts distinct rate-limit episodes.
- **Purposeful-use signal.** The report calculates the ratio of code-editing tool calls (Edit/Write/Bash/NotebookEdit) to filler calls. It checks whether touched files belong to the assignment repo.
- **Burst-vs-baseline check.** For each rate-limit hit, the tool compares two time windows before the hit: the 15-minute window and the 5-hour baseline window (this may need tuning after stress testing)

### Zero-config by design

The tool needs no git installation and no manual path configuration. `/weekly-report` itself is always recognized, from anywhere, since it's installed personally rather than discovered per-folder. Once invoked, it searches upward from wherever you are for the course root -- this works from any subfolder, in any assignment, regardless of whether that subfolder has its own git repo.

## Setup

Run this once, from your course root — the one folder that contains all your assignment folders. Not per assignment.

macOS / Linux:
```bash
curl -O https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/install.py
python3 install.py
```

Windows:
```
curl.exe -O https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/install.py
python install.py
```

The installer is Python, not shell, so it behaves identically in cmd.exe, PowerShell, and bash — no `mkdir -p`, no `~` expansion, no path-separator differences. It creates only the files it needs and preserves everything else already in `.claude/`.

It installs to two places: `weekly_report.py` and `SKILL.md` in your course root, plus a second copy of `SKILL.md` in your home directory. The second copy is needed because Claude Code's skill-discovery search stops at a git repository boundary — if you `git init` inside an individual assignment folder (common), a skill installed only at the course root is silently not recognized from inside it. Personal skills in the home directory load unconditionally, regardless of `cwd` or git nesting. `weekly_report.py`'s own root search deliberately ignores any copy found at the home directory itself, so that second copy can't be mistaken for your course root.


## Usage

At the end of each week, the student runs `/weekly-report`.

```
/weekly-report
```

Run this from anywhere in the course folder — any assignment, any subfolder. It always reports on the whole course for that week, not just one assignment. Reports the current week by default; add a number for a prior week (`/weekly-report 1` = last week).

The tool writes both output files to `usage_reports/` in the course root, once per week. Check with your course for what to submit.

## Known limitations / open items

- The field `weekly_cap_pct_estimate` requires the student's plan tier. Anthropic changed published caps multiple times in 2026. This field remains `null` until you add real cap numbers. Add these numbers to `WEEKLY_TOKEN_CAP_ESTIMATE` in `weekly_report.py`.
- The burst check detects fast, parallel gaming patterns. It does not detect slow, sequential gaming patterns. The code-ratio and file-overlap signals catch sequential gaming instead.
- The report is scoped to the whole course, not one assignment — it proves general weekly engagement, not that a specific assignment got worked on. Check the submitted transcript directly if you need that.
- The transcript file contains full conversation content. This includes prompts and tool outputs, not only metrics. Inform students that this data is collected.

## Validation

A real, deliberately-triggered rate-limit event validated the detection schema (`"error": "rate_limit"`, `apiErrorStatus: 429`). The event also validated the burst-detection logic. Documentation alone did not inform this design.
