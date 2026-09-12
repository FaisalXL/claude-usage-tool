---
name: weekly-report
description: Generate this week's Claude Code usage report for the assignment (token usage, model usage, tool-call ratios, rate-limit hits) and write it to usage_reports/ for submission. Use when the student asks to run, generate, or submit their weekly usage report.
---

Find `.claude/weekly_report.py`: check the current directory, then each parent directory upward, until you find it. Prefer a Glob tool for this search if one is available in this session — it works identically on every platform, with no dependency on Bash, PowerShell, or git being installed. If no Glob tool is available, fall back to whatever command-execution tool this session has (Bash's `find`/`dirname`, or PowerShell's `Get-ChildItem`) to do the same upward search.

Once found, run it with whichever command-execution tool this session has available (Bash or PowerShell — the command itself is identical either way, so it does not matter which one is used). The exact flags depend on what the student asked for:

```
python3 <path-to-weekly_report.py>
python3 <path-to-weekly_report.py> --days-back <N>
python3 <path-to-weekly_report.py> --since <YYYY-MM-DD>
```

Use whichever Python launcher actually exists on this machine. `python3` is
correct on macOS and Linux, but many Windows installations provide only
`python` or `py` — if `python3` is not found, retry the same command with
`python`, then `py`, before reporting a failure.

Deciding which form to use:
- If the student gave no argument, run with no flags at all — the script defaults to 7 days back (this week, rolling from right now).
- If the student gave a plain number (e.g. `/weekly-report 14`), pass it straight through as `--days-back <N>` — it means "look back N days from right now," not weeks. Use this if a student needs to catch up after missing a run.
- If the student instead gave or asked for a specific start date (e.g. "since September 1st"), use `--since <YYYY-MM-DD>` instead. Convert whatever date format the student gave into `YYYY-MM-DD`.

If no `.claude/weekly_report.py` is found in the current directory or any parent directory, tell the student: "Could not find the assignment folder — make sure you're running this from inside the assignment folder you were given."

The script checks GitHub for a newer version of itself and updates in place before doing anything else, every time it runs — this is automatic and does not need any extra action from you. If you see a line like `weekly_report.py: updated to the latest version, re-running...` on stderr, that is expected behavior, not an error: mention it to the student in passing (so a course-wide fix or field change just rolled out to them transparently) and continue with the rest of the output normally. If the update check fails (no network, GitHub unreachable), the script silently continues on its current version — also not an error, do not report it as one.

Show the full output verbatim to the student. Do not modify it. Tell the student two files were written to `usage_reports/` (a `.json` report and a `.transcript.jsonl` file) and that both need to be submitted.
