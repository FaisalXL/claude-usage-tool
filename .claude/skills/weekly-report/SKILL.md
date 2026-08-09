---
name: weekly-report
description: Generate this week's Claude Code usage report for the assignment (token usage, model usage, tool-call ratios, rate-limit hits) and write it to usage_reports/ for submission. Use when the student asks to run, generate, or submit their weekly usage report.
---

Find `.claude/weekly_report.py`: check the current directory, then each parent directory upward, until you find it. Prefer a Glob tool for this search if one is available in this session — it works identically on every platform, with no dependency on Bash, PowerShell, or git being installed. If no Glob tool is available, fall back to whatever command-execution tool this session has (Bash's `find`/`dirname`, or PowerShell's `Get-ChildItem`) to do the same upward search.

Once found, run it with whichever command-execution tool this session has available (Bash or PowerShell — the command itself is identical either way, so it does not matter which one is used):

```
python3 <path-to-weekly_report.py> --weeks-back <N>
```

Use `<N>` as the number the student gave as an argument to this command (e.g. `/weekly-report 1` means `--weeks-back 1`). If the student gave no argument, use `0`.

If no `.claude/weekly_report.py` is found in the current directory or any parent directory, tell the student: "Could not find the assignment folder — make sure you're running this from inside the assignment folder you were given."

Show the full output verbatim to the student. Do not modify it. Tell the student two files were written to `usage_reports/` (a `.json` report and a `.transcript.jsonl` file) and that both need to be submitted.
