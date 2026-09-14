# claude-usage-tool

This tool measures how much a student actually iterates with Claude Code on their coursework. It tracks per-model token usage and detects real rate-limit hits. It requires no manual log review.

## The problem

Assignments expect students to rely on Claude Code the way working developers do -- as a constant, iterative collaborator, not a one-shot answer machine. This tool is a meter, not an enforcement mechanism: it gives students and the course a concrete number to check usage against, so "am I actually iterating enough for this to be meaningful" has a real answer instead of a guess. It works automatically at class scale.
## How it works

The tool is a Claude Code Skill. Students run `/weekly-report` once per week. The skill reads local session logs at `~/.claude/projects/**/*.jsonl`. It requires no telemetry, server, or account access. The skill produces two files, named by the date the report was generated:

- **`usage_reports/<date>.json`** — the computed report.
- **`usage_reports/<date>.transcript.jsonl`** — the raw session data behind it.

A hash links both files. The hash detects tampering with either file.

### What the report includes

- **Model usage.** The report lists exact token counts per model, for the reported window.
- **Token usage, split by what it actually costs.** `token_usage.effective` is new work — input tokens, newly-cached context, and output — while `token_usage.cache_reread` is context replayed from cache on a later turn, billed at a steep discount. `effective` is the number meant to be compared or thresholded; `total` is kept only for continuity with what a flat token count used to mean. See Known limitations for exactly what this split does and doesn't prove.
- **Daily breakdown.** Below the range-level aggregate, the report includes a per-day breakdown (token usage, model usage, code_ratio, tool counts, session count) — useful for spotting a specific day's anomaly instead of trusting one averaged number.
- **Rate-limit episodes.** The report lists each distinct rate-limit episode (not just a raw 429 tally) — first/last hit time, which day, and burst-vs-baseline tool-call ratios around it.
- **Purposeful-use signal.** The report calculates the ratio of code-editing tool calls (Edit/Write/Bash/NotebookEdit) to filler calls. It checks whether touched files belong to the assignment repo.
- **Burst-vs-baseline check.** For each rate-limit hit, the tool compares two time windows before the hit: the 15-minute window and the 5-hour baseline window (this may need tuning after stress testing)

### Zero-config by design

The tool needs no git installation and no manual path configuration. `/weekly-report` itself is always recognized, from anywhere, since it's installed personally rather than discovered per-folder. Once invoked, it searches upward from wherever you are for the course root -- this works from any subfolder, in any assignment, regardless of whether that subfolder has its own git repo.

### Stays current automatically

Every run of `weekly_report.py` -- via `/weekly-report`, a raw `python3` call, or a fresh `install.py` -- checks GitHub for a newer version of itself and updates in place before doing anything else. A fix or field change reaches every student on their next run, no reinstall needed. If GitHub is unreachable, it fails silently and just runs the current version.

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

Run this from anywhere in the course folder — any assignment, any subfolder. It always reports on the whole course, not just one assignment. The range is a rolling window ending right now, not a calendar week — by default it looks back 7 days; add a number of days to look back further (`/weekly-report 14` = last 14 days from right now), which is the way to catch up after missing a run. The report itself states its exact `range_start`/`range_end` so it's never ambiguous what window it covers.

The tool writes both output files to `usage_reports/` in the course root, once per run. Check with your course for what to submit.

## Known limitations / open items

- `token_usage.effective` (input + cache-creation + output, excluding cache-reread) is this tool's own methodology, not an Anthropic-published metric. Anthropic documents what each of the four raw fields means and how they're billed (`cache_creation_input_tokens` at 1.25x-2x normal input price, `cache_read_input_tokens` at a 90-97.5% discount) at [prompt-caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) and [messages/create](https://platform.claude.com/docs/en/api/messages/create), but the only formula they publish combining them is the trivial sum. `effective` is a real improvement over a flat total — confirmed directly against real session data, where cache-reread made up as much as 97% of a session's raw total, i.e. mostly the same earlier context being billed again, not new work — but it doesn't normalize for model choice or task shape, and heavily fragmented sessions can still inflate `cache_creation`.
- The burst check detects fast, parallel gaming patterns. It does not detect slow, sequential gaming patterns. The code-ratio and file-overlap signals catch sequential gaming instead.
- The report is scoped to the whole course, not one assignment — it proves general engagement over the reported window, not that a specific assignment got worked on. Check the submitted transcript directly if you need that.
- The transcript file contains full conversation content. This includes prompts and tool outputs, not only metrics. Inform students that this data is collected.

## Validation

A real, deliberately-triggered rate-limit event validated the detection schema (`"error": "rate_limit"`, `apiErrorStatus: 429`). The event also validated the burst-detection logic. Documentation alone did not inform this design.
