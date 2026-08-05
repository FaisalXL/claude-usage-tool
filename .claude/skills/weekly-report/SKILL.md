---
name: weekly-report
description: Generate this week's Claude Code usage report for the assignment (token usage, model usage, tool-call ratios, rate-limit hits) and write it to usage_reports/ for submission. Use when the student asks to run, generate, or submit their weekly usage report.
---

Run this exact command and show the student the full output verbatim:

```bash
dir="$(pwd)"
found=""
while [ "$dir" != "/" ]; do
  if [ -f "$dir/.claude/weekly_report.py" ]; then
    found="$dir/.claude/weekly_report.py"
    break
  fi
  dir="$(dirname "$dir")"
done
if [ -z "$found" ]; then
  echo "Could not find the assignment folder above $(pwd) — make sure you're running this from inside the assignment folder you were given."
else
  python3 "$found" --weeks-back "${1:-0}"
fi
```

Do not modify the output. Tell the student two files were written to `usage_reports/` (a `.json` report and a `.transcript.jsonl` file) and that both need to be submitted.
