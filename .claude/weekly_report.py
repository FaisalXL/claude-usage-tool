#!/usr/bin/env python3
"""
Parses a student's Claude Code session logs (~/.claude/projects/<project>/*.jsonl)
for one week and reports:
  - rate-limit-hit count (system/api_error entries with a populated rateLimits payload)
  - weekly token total, and %-of-cap if a plan tier is given
  - per-model usage breakdown
  - a "purposeful use" ratio: code tool calls vs total, subagent fraction, file overlap
    against the actual assignment repo
  - a sha256 integrity hash over the raw lines that fed the report

NOTE on limit_hit detection: confirmed against a real 429 on 2026-08-05. A hit
is an "assistant"-type entry with message.model == "<synthetic>", a top-level
error == "rate_limit", isApiErrorMessage == true, and apiErrorStatus == 429.
The human-readable text (including the actual reset time) is in
message.content. This is unrelated to system/api_error entries, which cover
connection-level errors, not rate limits.

NOTE on WEEKLY_TOKEN_CAP_ESTIMATE: these are placeholders, not confirmed figures.
Anthropic has changed these multiple times in 2026. Fill in current published
numbers per plan tier before trusting weekly_cap_pct_estimate.

Run with no arguments from inside the assignment repo to get a report for the
current week, across every Claude Code session (any project folder, any
number of sessions) whose cwd falls under the repo:

    python3 weekly_report.py

Sessions are matched by `cwd`, not by which ~/.claude/projects/<mangled-name>
folder they live in, so this is correct even if a student worked from a
subdirectory, moved the repo, or has sessions scattered across multiple
project folders for the same assignment.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEEKLY_TOKEN_CAP_ESTIMATE = {
    "pro": None,
    "max5x": None,
    "max20x": None,
}

CODE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}

LOCAL_WINDOW_HOURS = 5  # matches Claude Code's own 5-hour rate-limit accounting window


def parse_ts(ts_str):
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def in_week(ts_str, week_start, week_end):
    return week_start <= parse_ts(ts_str) < week_end


SHORT_WINDOW_MINUTES = 15  # burst-throughput window: catches parallel-agent filler that
                            # a tool-name ratio can't see once calls happen inside subagents


def ratios_for(events, window_minutes=None):
    """code_ratio/subagent_fraction/throughput for an arbitrary slice of
    (timestamp, tool_name, file_path) tool-call events. Tool-name ratios don't
    discriminate parallel-agent spam well — once you're inside a spawned
    subagent's own transcript, its Bash/Read calls look identical in shape to
    real work. calls_per_minute is what actually catches it: no human-paced
    session sustains the throughput a burst of parallel filler agents does."""
    total = len(events)
    code = sum(1 for _, name, _ in events if name in CODE_TOOLS)
    agent = sum(1 for _, name, _ in events if name in AGENT_TOOLS)
    rate = (total / window_minutes) if window_minutes else None
    return {
        "tool_calls": total,
        "code_ratio": round(code / total, 3) if total else 0,
        "subagent_fraction": round(agent / total, 3) if total else 0,
        "calls_per_minute": round(rate, 1) if rate is not None else None,
    }


def most_recent_monday(today=None):
    today = today or datetime.now(timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


MARKER_RELPATH = ".claude/skills/weekly-report/SKILL.md"  # ships with the assignment template


def find_marker_root(start_path, marker_relpath=MARKER_RELPATH):
    """Walk upward from start_path looking for the marker file that ships
    with the assignment template. Students don't need to know what a
    directory is, let alone git — wherever inside the assignment folder they
    happen to run the report from, this finds the folder that has the
    marker and treats that as the root. Same trick git/npm/cargo use to find
    a project root from any subdirectory.

    Deliberately keeps walking past the first match and returns the
    OUTERMOST one found, not the nearest. If a leftover marker from an old
    assignment template ends up nested inside this semester's folder, the
    whole-assignment root should still win, not the incidental nested one."""
    current = Path(start_path).expanduser().resolve()
    found = None
    for candidate in [current, *current.parents]:
        if (candidate / marker_relpath).exists():
            found = candidate
    return str(found) if found else None


def path_is_under(path_str, root_str):
    """True if path_str is root_str itself or a descendant of it.

    Tries a plain string prefix check first (cheap, and correct on
    case-sensitive filesystems). Falls back to filesystem identity
    (os.path.samefile, which compares device+inode) when that fails --
    on case-insensitive filesystems (the default on macOS and Windows),
    Path.resolve() does NOT normalize casing, so the same real directory
    can come back as two different strings depending on how a student's
    shell/OS happened to report cwd in a given session. samefile() sees
    through that because it asks the filesystem, not the string."""
    if path_str == root_str or path_str.startswith(root_str + os.sep):
        return True
    try:
        candidate = Path(path_str)
    except (OSError, ValueError):
        return False
    for ancestor in [candidate, *candidate.parents]:
        try:
            if os.path.samefile(ancestor, root_str):
                return True
        except OSError:
            continue
    return False


def resolve_repo_root(path):
    marker_root = find_marker_root(path)
    if marker_root:
        return marker_root

    # No marker found (e.g. running this file standalone while testing, not
    # via the shipped template) — fall back to git, then to the literal path.
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        resolved = str(Path(path).expanduser().resolve())
        print(
            f"warning: no {MARKER_RELPATH} found above {path}, and it's not a git repo either; "
            f"falling back to {resolved} as the match root. Matching may be unreliable if this "
            "isn't actually the assignment folder.",
            file=sys.stderr,
        )
        return resolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-path", default=".", help="path to the assignment repo (default: current directory)")
    ap.add_argument("--claude-dir", default="~/.claude/projects", help="root of Claude Code's session logs")
    ap.add_argument("--week-start", default=None, help="YYYY-MM-DD, UTC-anchored (default: most recent Monday)")
    ap.add_argument("--weeks-back", type=int, default=0, help="0 = this week, 1 = last week, etc.")
    ap.add_argument("--plan", default=None, choices=list(WEEKLY_TOKEN_CAP_ESTIMATE), help="plan tier, for cap %% estimate")
    args = ap.parse_args()

    if args.week_start:
        week_start = datetime.fromisoformat(args.week_start).replace(tzinfo=timezone.utc)
    else:
        week_start = most_recent_monday() - timedelta(weeks=args.weeks_back)
    week_end = week_start + timedelta(days=7)

    claude_dir = Path(args.claude_dir).expanduser()
    # Scan every project folder, not just one — a student's sessions for this
    # repo may be split across multiple mangled project dirs (moved the repo,
    # ran from a subdirectory, etc). Association happens by `cwd`, below, not
    # by which folder a session happens to live in.
    jsonl_paths = sorted(
        list(claude_dir.glob("*/*.jsonl")) + list(claude_dir.glob("*/*/subagents/*.jsonl"))
    )
    if not jsonl_paths:
        print(f"No jsonl files found under {claude_dir}", file=sys.stderr)
        sys.exit(1)

    repo_root = resolve_repo_root(args.repo_path)

    model_usage = {}
    total_tokens = 0
    tool_counts = {}
    total_tool_calls = 0
    touched_files = set()
    limit_hit_events_raw = []   # every 429 line — many can share one real episode
    tool_timeline = []          # (timestamp, tool_name, file_path_or_None), for windowed ratios
    raw_lines_for_hash = []
    sessions_seen_this_week = set()   # any cwd, for the sanity check below
    sessions_matched = set()          # cwd under repo_root

    for path in jsonl_paths:
        with open(path, errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = obj.get("timestamp")
                if not ts or not in_week(ts, week_start, week_end):
                    continue

                cwd = obj.get("cwd")
                if not cwd:
                    continue

                session_id = obj.get("sessionId")
                sessions_seen_this_week.add(session_id)

                try:
                    resolved_cwd = str(Path(cwd).resolve())
                except OSError:
                    resolved_cwd = cwd
                if not path_is_under(resolved_cwd, repo_root):
                    continue

                sessions_matched.add(session_id)
                raw_lines_for_hash.append(line.encode())
                t = obj.get("type")

                if t == "assistant" and obj.get("error") == "rate_limit":
                    msg = obj.get("message", {})
                    text = "".join(
                        b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"
                    )
                    limit_hit_events_raw.append({
                        "timestamp": ts,
                        "apiErrorStatus": obj.get("apiErrorStatus"),
                        "message": text,
                    })
                    continue

                if t == "assistant":
                    msg = obj.get("message", {})
                    model = msg.get("model", "unknown")
                    usage = msg.get("usage", {})
                    inp = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    out = usage.get("output_tokens", 0)
                    m = model_usage.setdefault(model, {"turns": 0, "input_tokens": 0, "output_tokens": 0})
                    m["turns"] += 1
                    m["input_tokens"] += inp
                    m["output_tokens"] += out
                    total_tokens += inp + out

                    for block in msg.get("content", []):
                        if block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            tool_counts[name] = tool_counts.get(name, 0) + 1
                            total_tool_calls += 1
                            fp = block.get("input", {}).get("file_path")
                            if fp:
                                touched_files.add(fp)
                            tool_timeline.append((ts, name, fp))


    code_calls = sum(tool_counts.get(t, 0) for t in CODE_TOOLS)
    agent_calls = sum(tool_counts.get(t, 0) for t in AGENT_TOOLS)
    code_ratio = code_calls / total_tool_calls if total_tool_calls else 0
    subagent_fraction = agent_calls / total_tool_calls if total_tool_calls else 0

    # Every 429 during one real rate-limit episode shares the identical
    # reset-time message (every in-flight parallel call fails with the same
    # underlying limit) — group by that exact text so 18 raw error lines from
    # one episode count as 1, not 18.
    episodes_by_message = {}
    for ev in limit_hit_events_raw:
        episodes_by_message.setdefault(ev["message"], []).append(ev)

    limit_hit_episodes = []
    for message, evs in episodes_by_message.items():
        evs.sort(key=lambda e: e["timestamp"])
        first_ts = evs[0]["timestamp"]
        first_dt = parse_ts(first_ts)

        long_start = first_dt - timedelta(hours=LOCAL_WINDOW_HOURS)
        long_events = [
            (ts_, name, fp) for ts_, name, fp in tool_timeline
            if long_start <= parse_ts(ts_) <= first_dt
        ]
        short_start = first_dt - timedelta(minutes=SHORT_WINDOW_MINUTES)
        short_events = [
            (ts_, name, fp) for ts_, name, fp in tool_timeline
            if short_start <= parse_ts(ts_) <= first_dt
        ]

        limit_hit_episodes.append({
            "message": message,
            "first_hit": first_ts,
            "last_hit": evs[-1]["timestamp"],
            "raw_429_count": len(evs),
            f"burst_{SHORT_WINDOW_MINUTES}min_window": ratios_for(short_events, SHORT_WINDOW_MINUTES),
            f"baseline_{LOCAL_WINDOW_HOURS}h_window": ratios_for(long_events, LOCAL_WINDOW_HOURS * 60),
        })

    real_files = set()
    for fp in touched_files:
        try:
            resolved = str(Path(fp).resolve())
        except OSError:
            resolved = fp
        if resolved.startswith(repo_root):
            real_files.add(fp)
    file_overlap = len(real_files) / len(touched_files) if touched_files else 0

    cap = WEEKLY_TOKEN_CAP_ESTIMATE.get(args.plan) if args.plan else None
    weekly_pct = (total_tokens / cap * 100) if cap else None

    transcript_bytes = b"\n".join(raw_lines_for_hash) + (b"\n" if raw_lines_for_hash else b"")
    integrity_hash = hashlib.sha256(transcript_bytes).hexdigest()

    warning = None
    if not sessions_matched:
        if sessions_seen_this_week:
            warning = (
                f"Found {len(sessions_seen_this_week)} Claude Code session(s) this week, "
                f"but none had a working directory under the repo root ({repo_root}). "
                "This report is almost certainly wrong — check you ran Claude Code inside "
                "this repo (or a subdirectory of it) during the week in question."
            )
        else:
            warning = (
                "No Claude Code sessions found at all in this date range. "
                "This report is empty — either nothing was run this week, or the date "
                "range is wrong."
            )
        print(f"WARNING: {warning}", file=sys.stderr)

    report = {
        "week_start": week_start.date().isoformat(),
        "week_end": week_end.date().isoformat(),
        "repo_root_matched_against": repo_root,
        "sessions_matched": len(sessions_matched),
        "sessions_seen_this_week_any_repo": len(sessions_seen_this_week),
        "warning": warning,
        "total_tokens": total_tokens,
        "weekly_cap_pct_estimate": weekly_pct,
        "limit_hit_count": len(limit_hit_episodes),
        "limit_hit_raw_429_count": len(limit_hit_events_raw),
        "limit_hit_episodes": limit_hit_episodes,
        "model_usage": model_usage,
        "tool_counts": tool_counts,
        "total_tool_calls": total_tool_calls,
        "code_ratio": round(code_ratio, 3),
        "subagent_fraction": round(subagent_fraction, 3),
        "file_overlap": round(file_overlap, 3),
        "integrity_hash_sha256": integrity_hash,
    }

    # Write files instead of relying on copy-paste from the terminal — a
    # student manually copying JSON out of a terminal will eventually
    # truncate or mangle it. The transcript written here is exactly the set
    # of lines that fed the report and the hash, so integrity_hash_sha256
    # can be recomputed from the submitted transcript file and checked
    # against the value embedded in the submitted report file.
    out_dir = Path(repo_root) / "usage_reports"
    out_dir.mkdir(exist_ok=True)
    stem = week_start.date().isoformat()
    report_path = out_dir / f"{stem}.json"
    transcript_path = out_dir / f"{stem}.transcript.jsonl"

    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    transcript_path.write_bytes(transcript_bytes)

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report to:     {report_path}", file=sys.stderr)
    print(f"Wrote transcript to: {transcript_path}", file=sys.stderr)
    print("Submit BOTH files.", file=sys.stderr)


if __name__ == "__main__":
    main()
