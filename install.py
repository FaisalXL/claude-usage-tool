#!/usr/bin/env python3
"""One-command installer for the weekly-report tool.

Run this from your course root -- the one folder that contains all your
assignment folders:

    python3 install.py        (macOS / Linux)
    python install.py         (Windows)

Written in Python rather than shell on purpose. The tool already requires
Python to run at all, so a Python installer is guaranteed to work anywhere
the tool itself works -- and it sidesteps every shell difference that a
copy-pasted setup command runs into: cmd.exe vs PowerShell vs bash, `~`
expansion, path separators, `mkdir -p`, curl vs Invoke-WebRequest.

Installs to two places, both one-time (see README for why two):
  1. <course root>/.claude/  -- weekly_report.py + SKILL.md
  2. ~/.claude/skills/weekly-report/SKILL.md -- so /weekly-report is
     recognized even inside a subfolder with its own nested git repo
"""

import sys
import urllib.error
import urllib.request
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main"
SKILL_RELPATH = ".claude/skills/weekly-report/SKILL.md"
SCRIPT_RELPATH = ".claude/weekly_report.py"


def fetch(relpath):
    url = f"{RAW_BASE}/{relpath}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        sys.exit(f"error: could not download {url}\n  {e}\nCheck your internet connection and try again.")


def write(dest: Path, data: bytes):
    # Only ever creates the specific folders it needs, never wipes an
    # existing .claude/ -- students may already have other things in there.
    dest.parent.mkdir(parents=True, exist_ok=True)
    existed = dest.exists()
    dest.write_bytes(data)
    print(f"  {'updated' if existed else 'created'}  {dest}")


def main():
    course_root = Path.cwd()
    home = Path.home()

    print(f"Installing weekly-report tool")
    print(f"  course root: {course_root}")
    print(f"  home:        {home}\n")

    if course_root == home:
        sys.exit(
            "error: refusing to install with your home directory as the course root.\n"
            "cd into your actual course folder (the one containing your assignment\n"
            "folders) and run this again."
        )

    skill_data = fetch(SKILL_RELPATH)
    script_data = fetch(SCRIPT_RELPATH)

    print("Course root:")
    write(course_root / SCRIPT_RELPATH, script_data)
    write(course_root / SKILL_RELPATH, skill_data)

    print("\nHome directory:")
    write(home / SKILL_RELPATH, skill_data)

    print("\nDone. Start Claude Code and run /weekly-report from anywhere")
    print("inside your course folder.")


if __name__ == "__main__":
    main()
