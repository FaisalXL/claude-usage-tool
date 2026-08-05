# claude-usage-tool

This tool verifies purposeful Claude Code usage for coursework. It tracks per-model token usage. It detects real rate-limit hits. It flags gamed usage. It requires no manual log review.

## The problem

Some courses require heavy Claude Code usage. Requirements include hitting the rate limit multiple times per week. Students can game these requirements. They spawn parallel filler agents. The agents burn tokens without doing real work. This tool verifies purposeful usage. It works automatically at class scale. It does not require manual log review.

## How it works

The tool is a Claude Code Skill. Students run `/weekly-report` once per week. The skill reads local session logs at `~/.claude/projects/**/*.jsonl`. It requires no telemetry, server, or account access. The skill produces two files:

- **`usage_reports/<week>.json`** — the computed report.
- **`usage_reports/<week>.transcript.jsonl`** — the raw session data behind it.

A hash links both files. The hash detects tampering with either file.

### What the report includes

- **Model usage.** The report lists exact token counts per model, per week.
- **Rate-limit-hit count.** The report counts distinct rate-limit episodes. One 429 event can create many raw error lines. Parallel requests fail together. The tool counts episodes, not raw lines.
- **Purposeful-use signal.** The report calculates the ratio of code-editing tool calls (Edit/Write/Bash/NotebookEdit) to filler calls. It checks whether touched files belong to the assignment repo.
- **Burst-vs-baseline check.** For each rate-limit hit, the tool compares two time windows before the hit: the 15-minute window and the 5-hour baseline window. A test showed a burst rate of 23.6 calls per minute. The baseline rate was 1.4 calls per minute. This is a 17x spike. Real work does not sustain this rate.

### Zero-config by design

The tool needs no git installation. The tool needs no manual path configuration. The script and the skill search upward from the current directory. They search for the assignment root folder. This method works from any subfolder.

## Setup

Copy the `.claude/` folder into the root of the assignment template. Distribute this template to students.

```
your-assignment-repo/
└── .claude/
    ├── weekly_report.py
    └── skills/
        └── weekly-report/
            └── SKILL.md
```

No further configuration is necessary. Every student receives the `/weekly-report` command automatically.

## Usage

At the end of each week, the student runs `/weekly-report`.

```
/weekly-report
```

The student runs this command inside Claude Code. The command works from any subfolder in the assignment folder. The command reports the current week by default. Add a number to view a prior week. For example, `/weekly-report 1` reports last week.

The tool writes both output files to `usage_reports/` in the assignment root. The student submits both files with the regular coursework.

## Known limitations / open items

- The field `weekly_cap_pct_estimate` requires the student's plan tier. Anthropic changed published caps multiple times in 2026. This field remains `null` until you add real cap numbers. Add these numbers to `WEEKLY_TOKEN_CAP_ESTIMATE` in `weekly_report.py`.
- The burst check detects fast, parallel gaming patterns. It does not detect slow, sequential gaming patterns. The code-ratio and file-overlap signals catch sequential gaming instead.
- The transcript file contains full conversation content. This includes prompts and tool outputs, not only metrics. Inform students that this data is collected.

## Validation

A real, deliberately-triggered rate-limit event validated the detection schema (`"error": "rate_limit"`, `apiErrorStatus: 429`). The event also validated the burst-detection logic. Documentation alone did not inform this design.
