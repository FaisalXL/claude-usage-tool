#!/usr/bin/env python3
"""
Parses a student's Claude Code session logs (~/.claude/projects/<project>/*.jsonl)
for a rolling window of recent activity and reports:
  - rate-limit-hit count (system/api_error entries with a populated rateLimits payload)
  - token usage for the window, split into effective (new) vs. repeated cache reads
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

NOTE on weekly_cap_pct_estimate: removed in v2. Anthropic does not publish a
token cap for any plan -- confirmed directly against their own docs -- so
this field could never be computed, only guessed, and the guessing caused
real confusion for several students. Not coming back without a real,
disclosed number to compute it against.

Run with no arguments from inside the assignment repo to get a report covering
the last 7 days up through right now, across every Claude Code session (any
project folder, any number of sessions) whose cwd falls under the repo:

    python3 weekly_report.py

This is a genuinely rolling window, not a calendar week: range_end is always
the exact moment the script runs, and range_start is local midnight N days
back (--days-back, default 7) or local midnight on an explicit date
(--since). Deliberately not anchored to Monday/UTC -- an earlier version
computed "this calendar week" from UTC "today", which silently pointed at
the wrong week (or cut off the last few hours of the right one) for anyone
running it in the evening in a US timezone, since UTC's day boundary doesn't
line up with any US local midnight. A pure backward-looking window sidesteps
that whole class of bug: there's no "which week is this" question to get
wrong, since nothing is snapped to a calendar boundary except range_start's
local midnight, which only ever makes the window slightly wider, never
excludes anything from "now" backward.

Sessions are matched by `cwd`, not by which ~/.claude/projects/<mangled-name>
folder they live in, so this is correct even if a student worked from a
subdirectory, moved the repo, or has sessions scattered across multiple
project folders for the same assignment.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_VERSION = "v3"
UPDATE_URL = "https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/.claude/weekly_report.py"
SKILL_UPDATE_URL = "https://raw.githubusercontent.com/FaisalXL/claude-usage-tool/main/.claude/skills/weekly-report/SKILL.md"
UPDATE_TIMEOUT_SECONDS = 4

CODE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}

ASSIGNMENT_ROOT_KEY = "(course root)"  # bucket for activity whose cwd is repo_root itself,
                                        # not inside any assignment subfolder

LOCAL_WINDOW_HOURS = 5  # matches Claude Code's own 5-hour rate-limit accounting window


def check_and_apply_update():
    """Self-update: fetches the latest version of this script from GitHub,
    and if it differs, overwrites itself and restarts with the new code.

    Lives in the script itself rather than in SKILL.md's instructions, so it
    applies no matter how this gets invoked -- through the /weekly-report
    skill, a student manually running `python3 weekly_report.py`, or
    install.py. A stale local copy that never gets updated is a real,
    already-observed problem: one of this tool's own maintainers ran an
    unpatched copy for most of a week without noticing, missing every fix
    made in that time.

    Fails silently and proceeds on the current version for any problem at
    all -- no internet, GitHub unreachable, a permissions error writing the
    file. An update check should never be the reason a report can't be
    generated."""
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=UPDATE_TIMEOUT_SECONDS) as resp:
            latest = resp.read()
        if not latest:
            return

        this_file = Path(__file__).resolve()
        current = this_file.read_bytes()
        if latest == current:
            return

        this_file.write_bytes(latest)
        print("weekly_report.py: updated to the latest version, re-running...", file=sys.stderr)
        os.execv(sys.executable, [sys.executable, str(this_file)] + sys.argv[1:])
    except Exception:
        return


def sync_skill_md():
    """Self-update above only ever overwrites this script -- SKILL.md ships
    as a separate file, is never re-executed, and nothing else gives it a
    chance to notice it's stale. Without this, a future flag/behavior change
    (this exact class of bug already happened once, going from --weeks-back
    to --days-back/--since) leaves already-installed students permanently
    broken: the agent builds its command from SKILL.md's instructions before
    this script ever runs, so an out-of-date SKILL.md keeps constructing
    calls this script no longer accepts, forever, with no way to recover
    short of a manual reinstall.

    This can't fix the CURRENT invocation -- by the time this script starts,
    the agent already built this run's command from whatever SKILL.md said
    a moment ago. What it does instead is make sure that command was the
    LAST time that happens: if SKILL.md is stale, this brings both the course
    root's copy and the personal (~) copy current, so the very next
    invocation reads correct instructions and works. Self-heals within one
    retry instead of breaking permanently.

    Same fail-silent contract as check_and_apply_update() -- never the
    reason a report can't be generated."""
    try:
        with urllib.request.urlopen(SKILL_UPDATE_URL, timeout=UPDATE_TIMEOUT_SECONDS) as resp:
            latest = resp.read()
        if not latest:
            return

        targets = []
        marker_root = find_marker_root(os.getcwd())
        if marker_root:
            targets.append(Path(marker_root) / MARKER_RELPATH)
        targets.append(Path.home() / MARKER_RELPATH)

        for target in targets:
            try:
                if not target.exists() or target.read_bytes() != latest:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(latest)
            except OSError:
                continue
    except Exception:
        return


def parse_ts(ts_str):
    """Parse a jsonl timestamp, or return None if it isn't one.

    Returns None rather than raising: this runs against every line of every
    project on the machine, before any filtering, so a single corrupt or
    unexpected timestamp anywhere -- including in projects entirely unrelated
    to this course -- used to abort the whole report.

    A timestamp with no offset is treated as UTC. Claude Code always writes
    a trailing Z, so a naive one means something else produced the line;
    UTC is overwhelmingly the intended reading, and guessing local time
    would silently shift it by hours."""
    if not isinstance(ts_str, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def in_range(ts_str, range_start, range_end):
    dt = parse_ts(ts_str)
    if dt is None:
        return False
    return range_start <= dt < range_end


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


def resolve_window(days_back=7, since_date_str=None):
    """Returns (range_start, range_end) as UTC-aware datetimes.

    range_end is always the exact instant this runs -- never snapped or
    rounded, so nothing that just happened can ever fall outside the window
    the way it could under the old Monday-anchored, UTC-based calculation.

    range_start is local midnight, either N days back (the default path) or
    on an explicit date (--since). Snapping the start to a local day
    boundary rather than using an exact N*24h offset means the window is
    always *at least* N full days -- slightly wider than a razor-exact
    offset, never narrower, which is the safe direction for this kind of
    imprecision.

    The DST handling here is deliberate: astimezone() is called directly on
    a *naive* local datetime, which asks the system for the correct offset
    for that specific date rather than reusing whatever offset happens to
    be in effect "now". Verified against a real US DST fallback: the same
    wall-clock time on either side of the transition correctly produced UTC
    timestamps an hour apart, using nothing beyond the standard library."""
    range_end = datetime.now(timezone.utc)
    if since_date_str:
        naive_start = datetime.fromisoformat(since_date_str)
    else:
        naive_start = (datetime.now() - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    range_start = naive_start.astimezone(timezone.utc)
    return range_start, range_end


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
    whole-assignment root should still win, not the incidental nested one.

    The home directory itself is deliberately excluded from matches. SKILL.md
    also gets installed personally at ~/.claude/skills/weekly-report/ (see
    SKILL.md's own install notes) so /weekly-report still gets recognized
    even when invoked from inside a subfolder with its own nested git repo --
    Claude Code's skill *discovery* stops at a git boundary; this search does
    not. Without this exclusion, that personal copy would itself match as an
    even more "outermost" marker than the real course root, silently scoping
    every report to the student's whole home directory."""
    current = Path(start_path).expanduser().resolve()
    home = Path.home().resolve()
    found = None
    for candidate in [current, *current.parents]:
        if candidate == home:
            continue
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
    # via the shipped template). No git dependency by design -- fall straight
    # back to the literal path, since the marker is the only thing that ever
    # identifies an assignment root.
    resolved = str(Path(path).expanduser().resolve())
    print(
        f"warning: no {MARKER_RELPATH} found above {path}; falling back to {resolved} "
        "as the match root. Matching may be unreliable if this isn't actually the "
        "assignment folder.",
        file=sys.stderr,
    )
    return resolved


def main():
    check_and_apply_update()
    sync_skill_md()

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-path", default=".", help="path to the assignment repo (default: current directory)")
    ap.add_argument("--claude-dir", default="~/.claude/projects", help="root of Claude Code's session logs")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD, local midnight through now (overrides --days-back)")
    ap.add_argument("--days-back", type=int, default=7, help="rolling window size in days, ending now (default: 7)")
    args = ap.parse_args()

    range_start, range_end = resolve_window(days_back=args.days_back, since_date_str=args.since)

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

    # Resolved before the scan, not after: the transcript is now streamed to
    # disk line by line instead of being accumulated in memory and joined at
    # the end (a heavy week runs to tens of MB), and a bad output path should
    # fail immediately rather than after a full scan.
    out_dir = Path(repo_root) / "usage_reports"
    if out_dir.exists() and not out_dir.is_dir():
        sys.exit(
            f"error: {out_dir} exists but is not a directory. Move or rename it, "
            "then run the report again."
        )
    out_dir.mkdir(exist_ok=True)
    # Named by range_end's date (when the report was generated), not
    # range_start -- range_end is the stable, always-"today" value; range_start
    # drifts by less than a day on every run and isn't a meaningful label.
    #
    # .astimezone() first, not .date() directly on the UTC-aware datetime --
    # range_end is UTC, and UTC midnight falls in the evening across the US,
    # so a student running this in the evening got a filename dated
    # "tomorrow" relative to their own clock (confirmed directly: a report
    # run at 6:10pm Pacific, range_end 01:10 UTC the next calendar day,
    # produced a filename one day ahead of when it was actually run, even
    # though the window itself -- computed via resolve_window() -- was
    # already correctly local-time-based). Every other per-day computation
    # in this file already does .astimezone() before .date(); this was the
    # one place that didn't.
    stem = range_end.astimezone().date().isoformat()
    report_path = out_dir / f"{stem}.json"
    transcript_path = out_dir / f"{stem}.transcript.jsonl"

    model_usage = {}
    # Tracked separately, not just summed, so the report can distinguish
    # effective (new) work from repeated cache reads instead of only exposing
    # one misleading combined total. See token_usage below.
    tokens_input = 0
    tokens_cache_creation = 0
    tokens_cache_read = 0
    tokens_output = 0
    tool_counts = {}
    total_tool_calls = 0
    touched_files = set()
    limit_hit_events_raw = []   # every 429 line — many can share one real episode
    tool_timeline = []          # (timestamp, tool_name, file_path_or_None), for windowed ratios
    sessions_seen_in_range = set()   # any cwd, for the sanity check below
    sessions_matched = set()          # cwd under repo_root

    # Mirrors the aggregate accumulators above, but bucketed by local
    # calendar day -- lets a grader see which specific day looks anomalous
    # (a code_ratio dip, an unrelated file_overlap drop) instead of only a
    # single range-wide number that would dilute or hide it. Deliberately a
    # plain dict via defaultdict rather than a dataclass: it needs to hold
    # exactly the same shape as the aggregate accumulators, and keeping them
    # structurally identical makes the per-day finalization logic below a
    # straight rerun of the aggregate logic, just scoped to one day's data.
    daily = defaultdict(lambda: {
        "sessions": set(),
        "tokens_input": 0,
        "tokens_cache_creation": 0,
        "tokens_cache_read": 0,
        "tokens_output": 0,
        "model_usage": {},
        "tool_counts": {},
        "total_tool_calls": 0,
        "touched_files": set(),
    })

    # Same shape and same reasoning as `daily` above, but bucketed by the
    # top-level folder under repo_root instead of by calendar day -- lets a
    # grader compare one specific assignment's usage against that
    # assignment's own threshold, instead of only the whole-course total.
    # This is a REPORTING breakdown only, computed the same way for every
    # line the whole-course scan already matched -- it does not change what
    # counts as "under repo_root" (still path_is_under against the outermost
    # marker, same as ever) and there is no flag or cwd trick a student can
    # use to make only one assignment's activity appear. A session whose cwd
    # IS repo_root itself (not inside any subfolder) buckets under
    # ASSIGNMENT_ROOT_KEY rather than being dropped or mis-attributed.
    by_assignment = defaultdict(lambda: {
        "sessions": set(),
        "tokens_input": 0,
        "tokens_cache_creation": 0,
        "tokens_cache_read": 0,
        "tokens_output": 0,
        "model_usage": {},
        "tool_counts": {},
        "total_tool_calls": 0,
        "touched_files": set(),
    })

    hasher = hashlib.sha256()
    seen_line_digests = set()   # exact-duplicate suppression, see below
    # Claude Code logs one JSONL line per content block (thinking, text, each
    # tool_use) that makes up a single assistant turn, not one line per turn --
    # every one of those lines carries the SAME message.usage, repeated
    # verbatim (except output_tokens -- see below). Lines sharing a
    # message.id are the same turn, not distinct turns; confirmed directly
    # against real logs, where a single turn (11 parallel tool calls) was
    # split across 13 lines. Summing usage per line (rather than per unique
    # message.id) was inflating token counts by ~2x on average, and by as
    # much as 13x for a single heavily-parallel turn. tool_use blocks are NOT
    # affected by this and must stay counted per line -- each one appears on
    # exactly one line, never repeated, so per-line counting is already
    # correct there. Maps message.id -> {day_key, model, output} for turns
    # already counted once; see the output_tokens handling below for why this
    # needs to track more than just "have I seen this id."
    turn_state = {}
    duplicate_lines = 0
    skipped_bad_timestamp = 0
    skipped_malformed = 0

    with open(transcript_path, "wb") as transcript_out:
      for path in jsonl_paths:
        # Binary mode on purpose. These logs are always UTF-8, but Python's
        # text mode defaults to the *locale* encoding, which on Windows is
        # cp1252 -- that mangles every non-ASCII character (a literal "·" in
        # Claude Code's own rate-limit message came back as "Â·"), and
        # errors="ignore" silently deletes bytes cp1252 leaves undefined.
        # Worse, the mangled text was then re-encoded on the way into the
        # transcript, so the file the integrity hash covers no longer matched
        # the real log bytes it is supposed to attest to. Reading bytes and
        # hashing them directly keeps the transcript byte-exact on every
        # platform; json.loads decodes UTF-8 itself.
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = obj.get("timestamp")
                if not ts:
                    continue  # queue-operation and similar lines carry no timestamp
                ts_dt = parse_ts(ts)
                if ts_dt is None:
                    skipped_bad_timestamp += 1
                    continue
                if not (range_start <= ts_dt < range_end):
                    continue

                cwd = obj.get("cwd")
                if not cwd:
                    continue

                session_id = obj.get("sessionId")
                sessions_seen_in_range.add(session_id)

                try:
                    resolved_cwd = str(Path(cwd).resolve())
                except (OSError, ValueError):
                    resolved_cwd = cwd
                if not path_is_under(resolved_cwd, repo_root):
                    continue

                # Exact-duplicate suppression. A cloud-sync conflict copy
                # ("a (1).jsonl" from OneDrive, which syncs the Windows user
                # profile where ~/.claude lives) would otherwise be counted a
                # second time, doubling tokens and tool calls while
                # sessions_matched still looks normal. Deliberately keyed on
                # the raw line bytes rather than a uuid field: it assumes
                # nothing about the log schema, and two genuinely distinct
                # lines being byte-identical is not realistic given
                # millisecond timestamps and per-line uuids. This also closes
                # the matching gaming vector -- copying your own logs no
                # longer inflates the numbers.
                digest = hashlib.sha256(line).digest()
                if digest in seen_line_digests:
                    duplicate_lines += 1
                    continue
                seen_line_digests.add(digest)

                sessions_matched.add(session_id)
                record = line + b"\n"
                transcript_out.write(record)
                hasher.update(record)

                # Local day, same reasoning as the rate-limit episode
                # grouping above: local, not UTC, since a UTC-day key would
                # split one real evening's work across two "days" for
                # anyone in a US timezone.
                day_key = ts_dt.astimezone().date().isoformat()
                d = daily[day_key]
                d["sessions"].add(session_id)

                # Top-level folder under repo_root, e.g. "hw3" for
                # <repo_root>/hw3/subdir -- computed per line, not once per
                # session, since a student can cd between assignments within
                # one continuous session and cwd is logged per message.
                try:
                    rel_parts = Path(resolved_cwd).relative_to(repo_root).parts
                    assignment_key = rel_parts[0] if rel_parts else ASSIGNMENT_ROOT_KEY
                except ValueError:
                    # path_is_under() already confirmed containment, possibly
                    # via samefile() rather than a string prefix match (case-
                    # insensitive filesystem, symlink) -- relative_to() is a
                    # plain string operation and can fail in exactly that
                    # case even though containment is real. Falls back to the
                    # root bucket rather than crashing the whole report.
                    assignment_key = ASSIGNMENT_ROOT_KEY
                a = by_assignment[assignment_key]
                a["sessions"].add(session_id)

                # Everything below reads into the message body, whose exact
                # shape is Anthropic's to change. One unexpected line should
                # cost that line, not the entire report -- the count is
                # surfaced in the report so this stays visible rather than
                # silently undercounting.
                try:
                    t = obj.get("type")

                    if t == "assistant" and obj.get("error") == "rate_limit":
                        msg = obj.get("message") or {}
                        content = msg.get("content")
                        text = "".join(
                            b.get("text", "")
                            for b in (content if isinstance(content, list) else [])
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                        limit_hit_events_raw.append({
                            "timestamp": ts,
                            "apiErrorStatus": obj.get("apiErrorStatus"),
                            "message": text,
                        })
                        continue

                    if t == "assistant":
                        msg = obj.get("message") or {}
                        model = msg.get("model", "unknown")
                        usage = msg.get("usage") or {}
                        mid = msg.get("id")
                        u_input = usage.get("input_tokens", 0)
                        u_cache_creation = usage.get("cache_creation_input_tokens", 0)
                        u_cache_read = usage.get("cache_read_input_tokens", 0)
                        u_output = usage.get("output_tokens", 0)

                        # input/cache_creation/cache_read are fixed before
                        # generation starts and never vary across a turn's
                        # split lines -- confirmed directly (0 exceptions
                        # across 1,335 real multi-line turns). output_tokens
                        # is different: it's still mid-flight on earlier
                        # lines and grows monotonically as the turn streams
                        # out (also confirmed directly, non-decreasing with
                        # zero exceptions), so counting it on first sight
                        # under-counts real output by ~2%. Rather than key
                        # this off "first" vs "last" line -- which would
                        # require buffering instead of streaming -- track the
                        # running output_tokens per message.id and add only
                        # the delta each time a later line reports a higher
                        # value, which gives the same result as keeping the
                        # last line without giving up single-pass streaming.
                        # model is confirmed constant across a turn's lines
                        # too (0 exceptions), so it's safe to attribute output
                        # deltas to the model/day recorded at first sight.
                        state = turn_state.get(mid) if mid is not None else None
                        if state is None:
                            tokens_input += u_input
                            tokens_cache_creation += u_cache_creation
                            tokens_cache_read += u_cache_read
                            tokens_output += u_output
                            d["tokens_input"] += u_input
                            d["tokens_cache_creation"] += u_cache_creation
                            d["tokens_cache_read"] += u_cache_read
                            d["tokens_output"] += u_output
                            a["tokens_input"] += u_input
                            a["tokens_cache_creation"] += u_cache_creation
                            a["tokens_cache_read"] += u_cache_read
                            a["tokens_output"] += u_output

                            m = model_usage.setdefault(model, {"turns": 0, "input_tokens": 0, "output_tokens": 0})
                            m["turns"] += 1
                            m["input_tokens"] += u_input + u_cache_creation + u_cache_read
                            m["output_tokens"] += u_output
                            dm = d["model_usage"].setdefault(model, {"turns": 0, "input_tokens": 0, "output_tokens": 0})
                            dm["turns"] += 1
                            dm["input_tokens"] += u_input + u_cache_creation + u_cache_read
                            dm["output_tokens"] += u_output
                            am = a["model_usage"].setdefault(model, {"turns": 0, "input_tokens": 0, "output_tokens": 0})
                            am["turns"] += 1
                            am["input_tokens"] += u_input + u_cache_creation + u_cache_read
                            am["output_tokens"] += u_output

                            # A missing id can't be correlated against
                            # anything later, so there's nothing to track --
                            # it's always treated as its own turn, same as
                            # before.
                            if mid is not None:
                                turn_state[mid] = {
                                    "day_key": day_key,
                                    "assignment_key": assignment_key,
                                    "model": model,
                                    "output": u_output,
                                }
                        elif u_output > state["output"]:
                            delta = u_output - state["output"]
                            tokens_output += delta
                            daily[state["day_key"]]["tokens_output"] += delta
                            by_assignment[state["assignment_key"]]["tokens_output"] += delta
                            model_usage[state["model"]]["output_tokens"] += delta
                            daily[state["day_key"]]["model_usage"][state["model"]]["output_tokens"] += delta
                            by_assignment[state["assignment_key"]]["model_usage"][state["model"]]["output_tokens"] += delta
                            state["output"] = u_output

                        # tool_use blocks are unaffected by the dedup above --
                        # each one is logged on exactly one line, never
                        # repeated across a turn's split lines, so every line
                        # still needs to be walked for these regardless of
                        # whether its usage was already counted.
                        content = msg.get("content")
                        for block in (content if isinstance(content, list) else []):
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "unknown")
                                tool_counts[name] = tool_counts.get(name, 0) + 1
                                total_tool_calls += 1
                                d["tool_counts"][name] = d["tool_counts"].get(name, 0) + 1
                                d["total_tool_calls"] += 1
                                a["tool_counts"][name] = a["tool_counts"].get(name, 0) + 1
                                a["total_tool_calls"] += 1
                                block_input = block.get("input")
                                fp = block_input.get("file_path") if isinstance(block_input, dict) else None
                                if fp:
                                    touched_files.add(fp)
                                    d["touched_files"].add(fp)
                                    a["touched_files"].add(fp)
                                tool_timeline.append((ts, name, fp))
                except Exception:
                    skipped_malformed += 1
                    continue


    code_calls = sum(tool_counts.get(t, 0) for t in CODE_TOOLS)
    agent_calls = sum(tool_counts.get(t, 0) for t in AGENT_TOOLS)
    code_ratio = code_calls / total_tool_calls if total_tool_calls else 0
    subagent_fraction = agent_calls / total_tool_calls if total_tool_calls else 0

    # Every 429 during one real rate-limit episode shares the identical
    # reset-time message (every in-flight parallel call fails with the same
    # underlying limit) — group by that exact text so 18 raw error lines from
    # one episode count as 1, not 18.
    #
    # Message text alone is not enough of a key, though: the message names the
    # reset time ("resets 4:50pm") but not the date, so hitting the limit at a
    # similar hour on Monday and again on Wednesday produces byte-identical
    # text, and three separate episodes collapsed into one four-day "episode"
    # -- undercounting the exact thing the coursework grades on.
    #
    # The day is taken in LOCAL time, not UTC. UTC midnight is 5pm in
    # California and 8pm on the US east coast, i.e. squarely inside working
    # hours, so a UTC-day key would split one real evening episode into two
    # and invent a limit hit that never happened. Local midnight is when
    # almost nobody is mid-session, so the boundary is far less likely to fall
    # inside an episode.
    episodes_by_key = {}
    for ev in limit_hit_events_raw:
        ev_dt = parse_ts(ev["timestamp"])
        local_day = ev_dt.astimezone().date().isoformat() if ev_dt else "unknown-date"
        episodes_by_key.setdefault((ev["message"], local_day), []).append(ev)

    limit_hit_episodes = []
    # Parsed once here rather than inside each episode's window filter, which
    # re-parsed every timeline entry for every episode.
    timeline_parsed = [
        (parse_ts(ts_), ts_, name, fp) for ts_, name, fp in tool_timeline
    ]
    timeline_parsed = [row for row in timeline_parsed if row[0] is not None]

    for (message, local_day), evs in episodes_by_key.items():
        evs.sort(key=lambda e: e["timestamp"])
        first_ts = evs[0]["timestamp"]
        first_dt = parse_ts(first_ts)
        if first_dt is None:
            continue

        long_start = first_dt - timedelta(hours=LOCAL_WINDOW_HOURS)
        long_events = [
            (ts_, name, fp) for dt_, ts_, name, fp in timeline_parsed
            if long_start <= dt_ <= first_dt
        ]
        short_start = first_dt - timedelta(minutes=SHORT_WINDOW_MINUTES)
        short_events = [
            (ts_, name, fp) for dt_, ts_, name, fp in timeline_parsed
            if short_start <= dt_ <= first_dt
        ]

        limit_hit_episodes.append({
            "message": message,
            "local_day": local_day,
            "first_hit": first_ts,
            "last_hit": evs[-1]["timestamp"],
            "raw_429_count": len(evs),
            f"burst_{SHORT_WINDOW_MINUTES}min_window": ratios_for(short_events, SHORT_WINDOW_MINUTES),
            f"baseline_{LOCAL_WINDOW_HOURS}h_window": ratios_for(long_events, LOCAL_WINDOW_HOURS * 60),
        })

    limit_hit_episodes.sort(key=lambda e: e["first_hit"])

    real_files = set()
    for fp in touched_files:
        try:
            resolved = str(Path(fp).resolve())
        except OSError:
            resolved = fp
        if path_is_under(resolved, repo_root):
            real_files.add(fp)
    file_overlap = len(real_files) / len(touched_files) if touched_files else 0

    # Per-day breakdown, at the bottom of the report (see daily_breakdown
    # below) -- reruns the same code_ratio/subagent_fraction/file_overlap
    # logic above, just scoped to one day's tool_counts/touched_files at a
    # time, so a specific day's anomaly (a code_ratio dip, an unrelated
    # file_overlap drop) is visible instead of averaged away into one
    # range-wide number. Aggregate stats stay at the top of the report for
    # a quick glance; this is for actually digging into what happened.
    daily_breakdown = {}
    for day_key in sorted(daily):
        d = daily[day_key]
        d_code_calls = sum(d["tool_counts"].get(t, 0) for t in CODE_TOOLS)
        d_agent_calls = sum(d["tool_counts"].get(t, 0) for t in AGENT_TOOLS)
        d_real_files = set()
        for fp in d["touched_files"]:
            try:
                resolved = str(Path(fp).resolve())
            except OSError:
                resolved = fp
            if path_is_under(resolved, repo_root):
                d_real_files.add(fp)

        daily_breakdown[day_key] = {
            "sessions": len(d["sessions"]),
            "token_usage": {
                "effective": d["tokens_input"] + d["tokens_cache_creation"] + d["tokens_output"],
                "cache_reread": d["tokens_cache_read"],
                "total": d["tokens_input"] + d["tokens_cache_creation"] + d["tokens_cache_read"] + d["tokens_output"],
                "breakdown": {
                    "input": d["tokens_input"],
                    "cache_creation": d["tokens_cache_creation"],
                    "cache_read": d["tokens_cache_read"],
                    "output": d["tokens_output"],
                },
            },
            "model_usage": d["model_usage"],
            "code_ratio": round(d_code_calls / d["total_tool_calls"], 3) if d["total_tool_calls"] else 0,
            "subagent_fraction": round(d_agent_calls / d["total_tool_calls"], 3) if d["total_tool_calls"] else 0,
            "file_overlap": round(len(d_real_files) / len(d["touched_files"]), 3) if d["touched_files"] else 0,
            "tool_counts": d["tool_counts"],
            "total_tool_calls": d["total_tool_calls"],
        }

    # Same rerun, bucketed by top-level assignment folder instead of day --
    # see the by_assignment accumulator's own comment for why this exists
    # and why it's a reporting breakdown only, not a scoping mechanism.
    assignment_breakdown = {}
    for assignment_key in sorted(by_assignment):
        a = by_assignment[assignment_key]
        a_code_calls = sum(a["tool_counts"].get(t, 0) for t in CODE_TOOLS)
        a_agent_calls = sum(a["tool_counts"].get(t, 0) for t in AGENT_TOOLS)
        a_real_files = set()
        for fp in a["touched_files"]:
            try:
                resolved = str(Path(fp).resolve())
            except OSError:
                resolved = fp
            if path_is_under(resolved, repo_root):
                a_real_files.add(fp)

        assignment_breakdown[assignment_key] = {
            "sessions": len(a["sessions"]),
            "token_usage": {
                "effective": a["tokens_input"] + a["tokens_cache_creation"] + a["tokens_output"],
                "cache_reread": a["tokens_cache_read"],
                "total": a["tokens_input"] + a["tokens_cache_creation"] + a["tokens_cache_read"] + a["tokens_output"],
                "breakdown": {
                    "input": a["tokens_input"],
                    "cache_creation": a["tokens_cache_creation"],
                    "cache_read": a["tokens_cache_read"],
                    "output": a["tokens_output"],
                },
            },
            "model_usage": a["model_usage"],
            "code_ratio": round(a_code_calls / a["total_tool_calls"], 3) if a["total_tool_calls"] else 0,
            "subagent_fraction": round(a_agent_calls / a["total_tool_calls"], 3) if a["total_tool_calls"] else 0,
            "file_overlap": round(len(a_real_files) / len(a["touched_files"]), 3) if a["touched_files"] else 0,
            "tool_counts": a["tool_counts"],
            "total_tool_calls": a["total_tool_calls"],
        }

    # Field definitions (input_tokens, cache_creation_input_tokens,
    # cache_read_input_tokens, output_tokens) are Anthropic's own, documented
    # at https://platform.claude.com/docs/en/build-with-claude/prompt-caching
    # and https://platform.claude.com/docs/en/api/messages/create. Anthropic
    # documents what each field means and how they're billed (cache_creation
    # at 1.25x-2x normal input price, cache_read at a 90-97.5% discount), but
    # the only formula they publish combining them is the trivial sum
    # (input + cache_creation + cache_read = total_input_tokens).
    #
    # "effective" below -- excluding cache_read -- is this tool's own
    # methodology, not an Anthropic-defined metric. cache_read is real usage
    # that really costs something (at that discount), but it's the SAME
    # earlier context being counted again on every subsequent turn, not new
    # work each time -- confirmed directly against real session data: one
    # session had cache_read at 97% of its raw total. "total" is kept for
    # continuity with what total_tokens used to mean, but "effective" is the
    # number that should actually get compared or thresholded.
    token_usage = {
        "effective": tokens_input + tokens_cache_creation + tokens_output,
        "cache_reread": tokens_cache_read,
        "total": tokens_input + tokens_cache_creation + tokens_cache_read + tokens_output,
        "breakdown": {
            "input": tokens_input,
            "cache_creation": tokens_cache_creation,
            "cache_read": tokens_cache_read,
            "output": tokens_output,
        },
    }

    # Accumulated incrementally as the transcript was streamed out, so this
    # covers exactly the bytes on disk without ever holding them all in memory.
    integrity_hash = hasher.hexdigest()

    warning = None
    if not sessions_matched:
        if sessions_seen_in_range:
            warning = (
                f"Found {len(sessions_seen_in_range)} Claude Code session(s) in this date range, "
                f"but none had a working directory under the repo root ({repo_root}). "
                "This report is almost certainly wrong — check you ran Claude Code inside "
                "this repo (or a subdirectory of it) during that time."
            )
        else:
            warning = (
                "No Claude Code sessions found at all in this date range. "
                "This report is empty — either nothing was run in this window, or the date "
                "range is wrong."
            )
        print(f"WARNING: {warning}", file=sys.stderr)

    # Insertion order, not alphabetical -- range first since it's the thing
    # most worth checking at a glance, then identity/matching info, then the
    # actual usage numbers, then supplementary/audit fields last.
    report = {
        "tool_version": SCRIPT_VERSION,
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "repo_root_matched_against": repo_root,
        "sessions_matched": len(sessions_matched),
        "sessions_seen_in_range_any_repo": len(sessions_seen_in_range),
        "warning": warning,
        "token_usage": token_usage,
        "model_usage": model_usage,
        "code_ratio": round(code_ratio, 3),
        "subagent_fraction": round(subagent_fraction, 3),
        "file_overlap": round(file_overlap, 3),
        "tool_counts": tool_counts,
        "total_tool_calls": total_tool_calls,
        # No separate count fields here: len(limit_hit_episodes) is the
        # episode count, and sum(e["raw_429_count"] for e in ...) is the raw
        # 429 count -- both fully derivable from the list itself, so a
        # parallel top-level number would just be the same data twice.
        "limit_hit_episodes": limit_hit_episodes,
        # Surfaced rather than silently swallowed: anything nonzero here means
        # some lines did not make it into the numbers above, and a grader
        # should be able to see that instead of trusting a quietly short count.
        "duplicate_lines_skipped": duplicate_lines,
        "lines_skipped_bad_timestamp": skipped_bad_timestamp,
        "lines_skipped_malformed": skipped_malformed,
        # Deliberately last -- the aggregate fields above are what a student
        # eyeballs at a glance; this is the detail for actually digging into
        # what happened on a specific day, or (below) a specific assignment.
        "daily_breakdown": daily_breakdown,
        # A reporting breakdown only, not a scoping mechanism -- computed the
        # same way for every line the whole-course scan already matched, so
        # there is no flag or cwd trick that changes what counts. Lets a
        # grader compare one assignment's usage against that assignment's own
        # threshold instead of only the whole-course total.
        "assignment_breakdown": assignment_breakdown,
        # Truly last -- almost never looked at directly by a student or a
        # grader; it only matters on the rare occasion someone needs to
        # confirm a submitted transcript matches the submitted report.
        "integrity_hash_sha256": integrity_hash,
    }

    # The report is written as a file rather than copy-pasted from the
    # terminal — a student manually copying JSON out of a terminal will
    # eventually truncate or mangle it. The transcript was already streamed
    # out during the scan above, and integrity_hash_sha256 covers exactly
    # those bytes, so it can be recomputed from the submitted transcript file
    # and checked against the value embedded in the submitted report.
    #
    # sort_keys=False deliberately -- insertion order above is the whole
    # point of putting range_start/range_end first, alphabetical sorting
    # would undo it (range would land near the bottom, after limit_hit_*).
    report_path.write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")

    # The full report is written to disk, not printed -- a run covering many
    # sessions produces a JSON large enough to be unreadable (and slow to
    # scroll past) in a terminal. Print only where to find it.
    print(f"Wrote report to:     {report_path}")
    print(f"Wrote transcript to: {transcript_path}")


if __name__ == "__main__":
    main()
