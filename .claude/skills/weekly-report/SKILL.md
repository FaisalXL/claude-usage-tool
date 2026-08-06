---
name: weekly-report
description: Generate this week's Claude Code usage report for the assignment (token usage, model usage, tool-call ratios, rate-limit hits) and write it to usage_reports/ for submission. Use when the student asks to run, generate, or submit their weekly usage report.
---

Find `.claude/weekly_report.py` by searching the current directory, then its parent directories, upward, until you find it. Use whatever tool fits the current shell — do not assume a POSIX shell is available (on Windows without Git installed, only a PowerShell tool may be available, not Bash).

Once found, run it:

```
python3 <path-to-weekly_report.py> --weeks-back <N>
```

Use `<N>` as the number the student gave as an argument to this command (e.g. `/weekly-report 1` means `--weeks-back 1`). If the student gave no argument, use `0`.

If no `.claude/weekly_report.py` is found in the current directory or any parent directory, tell the student: "Could not find the assignment folder — make sure you're running this from inside the assignment folder you were given."

Show the full output verbatim to the student. Do not modify it. Tell the student two files were written to `usage_reports/` (a `.json` report and a `.transcript.jsonl` file) and that both need to be submitted.
