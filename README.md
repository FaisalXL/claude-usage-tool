# claude-usage-tool

Autonomous usage verification for Claude Code coursework — tracks per-model token usage, detects real rate-limit hits, and flags gamed vs. purposeful AI activity without manual log review.

## The problem

Courses that require heavy Claude Code usage (e.g. "hit your rate limit N times a week," "use 40% of your weekly token budget") are easy to game — spin up a bunch of parallel filler agents, burn through the quota in minutes, without doing any real assignment work. This tool exists so a TA/professor can verify usage was *purposeful*, autonomously, at class scale, without reading student session logs by hand.

## How it works

Ships as a Claude Code Skill (`/weekly-report`) that a student runs once at the end of each week. It reads Claude Code's own local session logs (`~/.claude/projects/**/*.jsonl`) — no telemetry, no server, no student account access required — and produces two files:

- **`usage_reports/<week>.json`** — the computed report
- **`usage_reports/<week>.transcript.jsonl`** — the exact raw session lines behind it, hash-linked to the report so tampering with either file after the fact is detectable

### What the report includes

- **Model usage** — exact token counts per model, per week
- **Rate-limit-hit count** — deduplicated into real episodes (one genuine 429 event can produce dozens of raw error lines from parallel in-flight requests; this counts episodes, not raw lines)
- **Purposeful-use signal** — ratio of real code-editing tool calls (Edit/Write/Bash/NotebookEdit) vs. filler, and whether files touched actually belong to the assignment repo
- **Burst-vs-baseline check** — for every rate-limit hit, compares tool-call throughput in the 15 minutes before the hit against the student's own 5-hour baseline. A student gaming the hit-count requirement with parallel filler agents shows an extreme spike (tested against a real deliberate burst: 23.6 calls/min vs. a 1.4 calls/min baseline — a ~17x spike); real work sustained at that pace doesn't happen

### Zero-config by design

No git required, no manual path configuration. The script and the skill both walk upward from wherever they're run to find the assignment root (same trick git/npm use to find a project root from any subdirectory) — it just works whichever subfolder a student happens to be in.

## Setup

Copy the `.claude/` folder from this repo into the root of your assignment template (the folder you hand out to students):

```
your-assignment-repo/
└── .claude/
    ├── weekly_report.py
    └── skills/
        └── weekly-report/
            └── SKILL.md
```

That's it — no further configuration. Every student who receives the template automatically has the `/weekly-report` command available.

## Usage

At the end of each week, the student runs:

```
/weekly-report
```

inside Claude Code, from anywhere in the assignment folder. It reports on the current week by default; pass a number of weeks back to look at a prior week (e.g. `/weekly-report 1` for last week).

Both output files go to `usage_reports/` in the assignment root — the student submits both alongside their regular coursework.

## Known limitations / open items

- **Weekly cap %** (`weekly_cap_pct_estimate`) requires knowing the student's actual plan tier — Anthropic's published caps have changed multiple times through 2026, so this field is `null` until real numbers are filled in for `WEEKLY_TOKEN_CAP_ESTIMATE` in `weekly_report.py`.
- Only catches gaming patterns visible in tool-call structure and pacing — a sufficiently slow, sequential gaming attempt (not parallel/bursty) would need to be caught by the code-ratio/file-overlap signals instead of the burst check; both are included for exactly this reason.
- The transcript file contains full raw conversation content (prompts, tool inputs/outputs), not just metrics — students should know this is being collected, not just usage numbers.

## Validation

The rate-limit-hit detection schema (`"error": "rate_limit"`, `apiErrorStatus: 429`) and the burst-detection logic were both validated against a real, deliberately-triggered rate-limit event, not just inferred from documentation.
