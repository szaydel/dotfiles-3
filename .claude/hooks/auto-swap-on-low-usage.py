#!/usr/bin/env python3
"""Claude Code hook: keep the active claude-swap account on the
primary (asmeurer@gmail.com) and bridge to the fallback
(aaronmeurer@gmail.com) only when the primary's 5-hour budget is about to
run dry.

SOURCE_EMAIL is the *primary* account; TARGET_EMAIL is the fallback. Both
decisions are driven off a single ``claude-swap --list --json`` call, which
reports each account's 5-hour and 7-day usage windows (the used percentage
plus the fixed ``resetsAt`` timestamp for each).

The 5-hour window is a rolling budget with a *fixed* reset time: it can be
spent to 100% hours before it resets, and when it is, ``usageStatus`` still
reads "ok" (that field tracks the weekly/overall state, not the 5-hour
wall). So neither the reset clock nor usageStatus is a usable "about to be
walled" signal. Instead we use the same burn-rate projection the status line
shows -- projected minutes until the 5-hour budget hits 100% at the average
rate since the window opened:

  * Switch AWAY (primary -> fallback) when that projection drops under
    AWAY_PROJECTED_EMPTY_MINUTES minutes (about to run dry), when the 5-hour
    window's free headroom falls to AWAY_MIN_FREE_PCT regardless of what the
    projection says, OR whenever the primary's usageStatus is not "ok"
    (already walled, e.g. weekly cap). The absolute floor exists because the
    projection is an *average* over the window: a burst late in a mostly-idle
    window empties the budget far faster than the average predicts, and the
    average keeps reporting a comfortable margin right up to the wall.

  * Switch BACK (fallback -> primary) once the primary's 5-hour window
    has reset. The main, fetch-independent signal is wall-clock time: the
    5-hour ``resetsAt`` is a fixed timestamp, recorded when we switch away,
    so once ``now`` passes it the window has definitely reset -- no need to
    re-fetch the primary's usage, which as the *inactive* account is often
    "unavailable" (its usage-API token gets rate-limited). A secondary signal
    still fires when the inactive usage *is* fetchable and its 5-hour free
    headroom has jumped back to RETURN_FRESH_FREE_PCT. Either triggers the
    return; we hold only if we can positively see the weekly window is walled.

    Note this returns as soon as the primary is usable, *not* when the
    fallback runs low -- in the typical case the fallback still has plenty of
    headroom left when we switch back. That is deliberate: the fallback is a
    bridge across the primary's 5-hour gap, and the primary is the account we
    want to be on whenever it is available.

Caveats: the usage snapshot behind every decision is served from claude-swap's
store with a 180-second freshness floor (poll_policy.SERVE_TTL_S), dropping to
~60s for the active account while it is visibly burning toward a limit -- so
even a fresh read can be a few minutes behind reality. AWAY_PROJECTED_EMPTY_MINUTES
and AWAY_MIN_FREE_PCT are both margin against that staleness plus a burst.

Wired in ~/.claude/settings.json on:

  * PostToolUse   -- fires after every tool call, so a long turn that never
                     reaches a Stop still gets evaluated. This is the trigger
                     that matters: turn-boundary-only hooks can go hours
                     without firing while the budget drains inside one turn.
  * Stop, StopFailure, SubagentStop, UserPromptSubmit, PreCompact -- turn and
    session boundaries, including the failure path (a turn that walls the
    account mid-response never cleanly Stops, but does StopFailure).

Because PostToolUse fires per tool call, CHECK_INTERVAL_SECONDS throttles the
actual work: invocations inside that window exit silently without running
claude-swap or writing a log line. The throttle clock is the mtime of
THROTTLE_PATH; a nonblocking process lock serializes that throttle decision
with switch-state mutation so concurrent sessions cannot duplicate a switch or
race while reading/writing STATE_PATH.

A mid-turn switch does take effect on the running session -- claude-swap
rewrites ~/.claude/.credentials.json on every switch specifically to bump its
mtime and force Claude Code to drop its memoized OAuth token (see
claude_swap/credentials.py::_write_oauth_credentials). No restart needed;
worst case the running session takes ~30s (the macOS Keychain cache TTL) to
pick up the new account.

Always exits 0 on expected conditions so it never blocks a turn; unexpected
errors are allowed to surface as a traceback.

TO TURN THIS OFF: set ENABLED = False below (one line, tracked in dotfiles).
That is the switch to flip once the fallback account goes away. Nothing else
needs undoing -- the hook then no-ops on every turn, and the status line's
"↩ asmeurer N% free" note self-disables because it only renders while the
fallback account is the active one. For a temporary, per-shell disable
instead, export CLAUDE_AUTO_SWAP_DISABLED=1.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Master on/off switch. Flip to False to retire auto-swapping entirely (e.g.
# when the fallback account is gone) without unwiring the hook from
# settings.json -- the settings file is not tracked in dotfiles, so keeping the
# switch here is what makes turning it off stick.
ENABLED = True

SOURCE_EMAIL = "asmeurer@gmail.com"      # primary account
TARGET_EMAIL = "aaronmeurer@gmail.com"   # fallback account
# Short names for log/notification lines.
SOURCE_NAME = SOURCE_EMAIL.split("@")[0]
TARGET_NAME = TARGET_EMAIL.split("@")[0]
# Leave the primary when its 5-hour budget is projected to empty within this
# many minutes at the current average burn rate (the status line's estimate).
AWAY_PROJECTED_EMPTY_MINUTES = 15.0
# Hard floor: leave the primary once its 5-hour window has this little free
# left, whatever the projection says. The projection is a window *average*, so a
# burst late in an otherwise-idle window keeps reporting a roomy margin while the
# budget actually falls off a cliff -- the 2026-08-07 near-miss went 7% free
# ("empties in ~16m", stay) to 1% free in four minutes. This floor is what
# guarantees the switch lands before the wall, at the cost of leaving a few
# percent of the window unspent.
AWAY_MIN_FREE_PCT = 8.0
# The 5-hour window length, used to derive elapsed time for the burn-rate
# projection (mirrors the status line's 300-minute constant).
FIVE_HOUR_WINDOW_MINS = 300.0
# Minimum seconds between real evaluations. PostToolUse fires per tool call, but
# the underlying usage snapshot only refreshes every ~60-180s
# (poll_policy.SERVE_TTL_S), so checking more often than this buys no new data
# and just adds latency to every tool result.
CHECK_INTERVAL_SECONDS = 60.0
# Timeouts on the claude-swap subprocesses. Without these a hung usage API would
# stall every tool result at PostToolUse cadence. The switch gets a longer budget
# than the read: it writes the macOS Keychain, which can prompt or block.
LIST_TIMEOUT_SECONDS = 10.0
SWITCH_TIMEOUT_SECONDS = 30.0
NOTIFY_TIMEOUT_SECONDS = 5.0
# Return to the primary once its 5-hour window reads at least this % free again
# (i.e. it has reset -- a fresh window reads ~100% free, a spent one ~0%). This
# is the secondary return signal, used only when the inactive account's usage is
# fetchable; the main signal is wall-clock past the recorded reset time.
RETURN_FRESH_FREE_PCT = 90.0
# Hold on the fallback if we can positively see the primary's weekly window has
# this little free left -- it would be walled until the 7-day reset, days away.
WEEKLY_WALL_FREE_PCT = 2.0
# usageStatus values that mean the usage API did not return a usable status for
# an inactive account, not that the account is positively limit-walled.
USAGE_UNAVAILABLE_STATUSES = {"unavailable"}
# Known install location, used first so the hook works even if the spawning
# shell's PATH lacks ~/.local/bin; falls back to PATH lookup if it moves.
CLAUDE_SWAP_PATH = str(Path.home() / ".local" / "bin" / "claude-swap")
# Machine-local runtime state: the most recent switch (audit) plus
# ``pending_source_reset_at`` -- the primary's 5-hour resetsAt captured when we
# switched away, used to time the return without re-fetching its usage.
# Deliberately not in Dropbox -- it is per-machine, ephemeral state.
STATE_PATH = Path.home() / ".claude" / "auto-swap-state.json"
# Throttle clock for CHECK_INTERVAL_SECONDS. Deliberately a *separate*, empty
# file whose mtime is the timestamp, rather than a key in STATE_PATH. Access to
# it is protected by LOCK_PATH, which also protects STATE_PATH mutation.
THROTTLE_PATH = Path.home() / ".claude" / "auto-swap-last-check"
# Nonblocking inter-process lock. If another hook is already evaluating, this
# invocation silently exits; the active evaluator owns the throttle stamp and
# any state-file mutation.
LOCK_PATH = Path.home() / ".claude" / "auto-swap.lock"
# Append-only audit log: one line per *non-throttled* invocation (so at most one
# per CHECK_INTERVAL_SECONDS across all sessions) recording what usage the hook
# saw and what it decided. This is the diagnostic for "it didn't switch in time":
# a steady ~60s cadence of lines through a long turn means the hook is watching
# and the thresholds are what need tuning, while a multi-minute gap during active
# work means it is not being invoked at all. Machine-local, not Dropbox.
LOG_PATH = Path.home() / ".claude" / "auto-swap.log"
LOG_MAX_BYTES = 1_000_000  # rotate (keep tail) past this size


def resolve_swap() -> str | None:
    """Locate the claude-swap executable, preferring the known absolute path."""
    if Path(CLAUDE_SWAP_PATH).is_file():
        return CLAUDE_SWAP_PATH
    return shutil.which("claude-swap")


def is_unavailable_status(status: str | None) -> bool:
    """True when usageStatus means no current data, not a known account wall."""
    return status in USAGE_UNAVAILABLE_STATUSES


def is_limiting_status(status: str | None) -> bool:
    """True when usageStatus positively indicates the account should not run."""
    return (
        status is not None
        and status != "ok"
        and not is_unavailable_status(status)
    )


def window_free_pct(usage: dict, key: str) -> float | None:
    """Free headroom (100 - utilization) for one usage window, or None.

    ``key`` is "fiveHour" or "sevenDay". Returns None when the window or its
    ``pct`` is missing, so callers can distinguish "no data" from "0% free".
    """
    window = usage.get(key)
    if isinstance(window, dict) and isinstance(window.get("pct"), (int, float)):
        return 100.0 - window["pct"]
    return None


def minutes_until_reset(usage: dict, key: str) -> float | None:
    """Minutes until the given window's ``resetsAt``, or None if unavailable.

    ``resetsAt`` is a fixed UTC timestamp, so this is accurate even when the
    cached usage snapshot is a few minutes stale. Can be negative briefly at
    the reset boundary (resetsAt just passed but the snapshot not yet
    refreshed); callers treat that as "already reset".
    """
    window = usage.get(key)
    if not isinstance(window, dict):
        return None
    resets_at = window.get("resetsAt")
    if not isinstance(resets_at, str):
        return None
    reset_dt = datetime.fromisoformat(resets_at)
    return (reset_dt - datetime.now(timezone.utc)).total_seconds() / 60.0


def project_empty_minutes(usage: dict, key: str, window_mins: float) -> float | None:
    """Projected minutes until the window's budget hits 100%, or None.

    Mirrors ``project_empty_mins()`` in statusline-command.sh: extrapolates the
    average burn rate since the window opened (used_pct / elapsed) forward to
    when the remaining budget is spent. Returns None when the projection is not
    meaningful -- no usage yet, no elapsed time, missing reset clock, or the
    budget is on track to outlast the window (won't run out before it resets).
    """
    window = usage.get(key)
    if not isinstance(window, dict):
        return None
    used_pct = window.get("pct")
    if not isinstance(used_pct, (int, float)) or used_pct <= 0:
        return None
    mins_left = minutes_until_reset(usage, key)
    if mins_left is None:
        return None
    mins_elapsed = window_mins - mins_left
    if mins_elapsed <= 0:
        return None
    projected = (100.0 - used_pct) * mins_elapsed / used_pct
    projected = max(projected, 0.0)
    # Only meaningful if the budget runs out before the window resets.
    return projected if projected < mins_left else None


def acquire_evaluation_lock():
    """Acquire the process lock, or return None if another hook has it."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def throttled(now: float) -> bool:
    """True if another invocation already evaluated within CHECK_INTERVAL_SECONDS.

    Uses THROTTLE_PATH's mtime as the clock and stamps it when the caller is
    cleared to proceed. Call only while holding LOCK_PATH, which makes the
    stat/touch decision atomic across hook processes. A missing file -- first
    run after a reboot that clears the state dir -- reads as "not throttled".
    """
    try:
        if now - THROTTLE_PATH.stat().st_mtime < CHECK_INTERVAL_SECONDS:
            return True
    except FileNotFoundError:
        pass
    THROTTLE_PATH.touch()
    return False


def read_state() -> dict:
    """Load the runtime state file, or {} on the expected first-run absence."""
    try:
        return json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}


def write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def record_switch(to_email: str, now: float, source_reset_at: str | None) -> None:
    """Persist the most recent switch (audit) and arm/clear the pending reset.

    ``source_reset_at`` is the primary's 5-hour ``resetsAt`` at the moment we
    switch AWAY -- stored so the return can be timed off wall clock alone. On
    the return switch (source_reset_at=None) the pending value is cleared.
    """
    state = read_state()
    state["last_switch_ts"] = now
    state["last_switch_to"] = to_email
    if source_reset_at is not None:
        state["pending_source_reset_at"] = source_reset_at
    else:
        state.pop("pending_source_reset_at", None)
    write_state(state)


def arm_pending_reset(source_reset_at: str) -> None:
    """Persist an away-time reset before attempting a possibly slow switch."""
    state = read_state()
    state["pending_source_reset_at"] = source_reset_at
    write_state(state)


def get_pending_reset() -> datetime | None:
    """The primary's recorded away-time 5-hour resetsAt as a datetime, or None."""
    s = read_state().get("pending_source_reset_at")
    if not isinstance(s, str):
        return None
    return datetime.fromisoformat(s)


def clear_pending_reset() -> None:
    """Drop any armed pending reset (we are back on / already using the primary)."""
    state = read_state()
    if "pending_source_reset_at" in state:
        state.pop("pending_source_reset_at", None)
        write_state(state)


def log(line: str) -> None:
    """Append a timestamped line to the audit log, with light tail rotation."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            tail = LOG_PATH.read_text().splitlines()[-2000:]
            LOG_PATH.write_text("\n".join(tail) + "\n")
    except FileNotFoundError:
        pass
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a") as fh:
        fh.write(f"{stamp} {line}\n")


def notify(msg: str) -> None:
    """Best-effort macOS notification plus a stderr line for the transcript."""
    print(msg, file=sys.stderr)
    if shutil.which("osascript"):
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{msg}" with title "claude-swap"'],
                capture_output=True, timeout=NOTIFY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            log(f"notification timed out after {NOTIFY_TIMEOUT_SECONDS:.0f}s")


def do_switch(
    swap: str, to_email: str, now: float, success_msg: str,
    source_reset_at: str | None = None,
) -> None:
    """Run claude-swap --switch-to; record + notify on success, notify on fail.

    ``source_reset_at`` (set only on the AWAY switch) arms the pending reset
    before the subprocess starts. If claude-swap times out after changing
    credentials, later fallback evaluations still know when to return.
    """
    if source_reset_at is not None:
        arm_pending_reset(source_reset_at)
    try:
        sw = subprocess.run(
            [swap, "--switch-to", to_email], capture_output=True, text=True,
            timeout=SWITCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log(
            f"switch to {to_email} timed out after "
            f"{SWITCH_TIMEOUT_SECONDS:.0f}s"
        )
        return
    if sw.returncode == 0:
        record_switch(to_email, now, source_reset_at)
        notify(success_msg)
    else:
        if source_reset_at is not None:
            clear_pending_reset()
        notify(
            f"Tried to switch to {to_email} but claude-swap failed: "
            f"{sw.stderr.strip() or sw.stdout.strip()}"
        )


def handle_source_active(
    swap: str, usage: dict, status: str | None, now: float
) -> str:
    """On the primary: switch away when the 5h budget is about to empty, or if
    walled.

    Returns a short decision string for the audit log.
    """
    # Capture the primary's 5-hour resetsAt now (it is the active, freshly-read
    # account) so the return can be timed off wall clock even if its usage later
    # reads "unavailable" as the inactive account.
    fh = usage.get("fiveHour")
    reset_at = fh.get("resetsAt") if isinstance(fh, dict) else None

    if status is not None and status != "ok":
        do_switch(
            swap, TARGET_EMAIL, now,
            f"{SOURCE_EMAIL} usageStatus={status} (walled) "
            f"-- switched to {TARGET_EMAIL}.",
            source_reset_at=reset_at,
        )
        return f"AWAY->{TARGET_NAME} (usageStatus={status})"
    # Floor first: it is the signal that survives a burst the window-average
    # projection cannot see, and it fires even when the projection is None
    # (a mostly-idle window whose average says the budget outlasts the reset).
    free = window_free_pct(usage, "fiveHour")
    if free is not None and free <= AWAY_MIN_FREE_PCT:
        do_switch(
            swap, TARGET_EMAIL, now,
            f"{SOURCE_EMAIL} 5-hour budget down to {free:.0f}% free "
            f"-- switched to {TARGET_EMAIL}.",
            source_reset_at=reset_at,
        )
        return f"AWAY->{TARGET_NAME} (5h at {free:.0f}% free)"

    proj = project_empty_minutes(usage, "fiveHour", FIVE_HOUR_WINDOW_MINS)
    if proj is None:
        # No usage yet, or the budget is on track to outlast the window.
        return f"stay-on-{SOURCE_NAME} (5h budget outlasts window)"
    if proj > AWAY_PROJECTED_EMPTY_MINUTES:
        return f"stay-on-{SOURCE_NAME} (5h empties in ~{proj:.0f}m)"
    do_switch(
        swap, TARGET_EMAIL, now,
        f"{SOURCE_EMAIL} 5-hour budget ~{proj:.1f}m from empty at current burn "
        f"-- switched to {TARGET_EMAIL}.",
        source_reset_at=reset_at,
    )
    return f"AWAY->{TARGET_NAME} (5h empties in ~{proj:.1f}m)"


def handle_target_active(swap: str, source: dict | None, now: float) -> str:
    """On the fallback: return to the primary once its 5h window has reset.

    Returns a short decision string for the audit log.
    """
    now_dt = datetime.now(timezone.utc)

    # Signal 1 (robust, fetch-independent): wall clock has passed the reset time
    # we recorded when switching away. Works even when the primary's usage reads
    # "unavailable" as the inactive account.
    pending = get_pending_reset()
    reason = None
    if pending is not None and now_dt >= pending:
        reason = f"past recorded 5h reset {pending:%H:%M}Z"

    # Signal 2 (only when the inactive usage IS fetchable): 5h headroom jumped
    # back to fresh. Also read usageStatus / the weekly window to guard against
    # returning to a positively walled account. A status like "unavailable"
    # means "no current data" for the inactive account and does not block the
    # wall-clock return signal.
    weekly_walled = False
    source_status = source.get("usageStatus") if source else None
    if is_limiting_status(source_status):
        weekly_walled = True
    if source and source_status == "ok":
        usage = source.get("usage") or {}
        weekly_free = window_free_pct(usage, "sevenDay")
        weekly_walled = (
            weekly_free is not None and weekly_free <= WEEKLY_WALL_FREE_PCT
        )
        if reason is None:
            free = window_free_pct(usage, "fiveHour")
            if free is not None and free >= RETURN_FRESH_FREE_PCT:
                reason = f"5h reset observed (~{free:.0f}% free)"

    if reason is None:
        if pending is not None:
            mins = (pending - now_dt).total_seconds() / 60.0
            return f"stay-on-{TARGET_NAME} ({SOURCE_NAME} 5h resets in ~{mins:.0f}m)"
        status = source.get("usageStatus") if source else "no-source"
        return f"stay-on-{TARGET_NAME} (no reset signal; source {status})"
    if weekly_walled:
        if is_limiting_status(source_status):
            return (
                f"stay-on-{TARGET_NAME} "
                f"({SOURCE_NAME} usageStatus={source_status})"
            )
        return f"stay-on-{TARGET_NAME} ({SOURCE_NAME} weekly walled)"
    do_switch(
        swap, SOURCE_EMAIL, now,
        f"{SOURCE_EMAIL} 5-hour window reset ({reason}) "
        f"-- switched back from {TARGET_EMAIL}.",
    )
    return f"RETURN->{SOURCE_NAME} ({reason})"


def account_snapshot(accounts: list) -> str:
    """Compact one-line view of both accounts' free headroom for the log."""
    parts = []
    for a in accounts:
        email = (a.get("email") or "?").split("@")[0]
        usage = a.get("usage") or {}
        h = window_free_pct(usage, "fiveHour")
        w = window_free_pct(usage, "sevenDay")
        r = minutes_until_reset(usage, "fiveHour")
        e = project_empty_minutes(usage, "fiveHour", FIVE_HOUR_WINDOW_MINS)
        flag = "*" if a.get("active") else " "
        hs = f"{h:.0f}" if h is not None else "?"
        ws = f"{w:.0f}" if w is not None else "?"
        rs = f"{r:.0f}m" if r is not None else "?"
        es = f"{e:.0f}m" if e is not None else "-"
        parts.append(
            f"{flag}{email}[5h={hs}% empty_in={es} resets_in={rs} "
            f"7d={ws}% {a.get('usageStatus')}]"
        )
    return " ".join(parts)


def main() -> int:
    # Drain the hook payload on stdin. PostToolUse passes the full tool_response,
    # which can exceed the pipe buffer -- leaving it unread risks blocking the
    # writer. We do not use any of it; the decision comes from claude-swap.
    sys.stdin.read()

    now = time.time()
    lock_file = acquire_evaluation_lock()
    if lock_file is None:
        return 0
    try:
        return evaluate(now)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def evaluate(now: float) -> int:
    # Throttle before anything else, including the log: at PostToolUse cadence
    # the un-throttled path would run per tool call and bury the audit log.
    if throttled(now):
        return 0
    # Heartbeat next, before anything can raise, so a gap in the log during
    # active work means "the hook never fired" (not "fired but crashed early").
    log("invoked")
    # Kill-switch: flip ENABLED above (permanent) or set
    # CLAUDE_AUTO_SWAP_DISABLED=1 (per-shell) to disable auto-swapping without
    # unwiring the hook from settings.json. Default is enabled.
    if not ENABLED or os.environ.get("CLAUDE_AUTO_SWAP_DISABLED", "0") == "1":
        log("skip: auto-swap disabled")
        return 0
    swap = resolve_swap()
    if not swap:
        log("skip: claude-swap not found")
        return 0  # claude-swap not installed; nothing to do

    try:
        proc = subprocess.run(
            [swap, "--list", "--json"], capture_output=True, text=True,
            timeout=LIST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # Same class as the failure below -- the usage API is unreachable or
        # slow. Logged, not silenced, and retried on the next check.
        log(f"skip: --list timed out after {LIST_TIMEOUT_SECONDS:.0f}s")
        return 0
    if proc.returncode != 0 or not proc.stdout.strip():
        # Usage API unreachable / not logged in: note it and skip this turn.
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        log(f"skip: --list failed rc={proc.returncode} {err[:1]}")
        return 0

    accounts = json.loads(proc.stdout).get("accounts") or []
    active = next((a for a in accounts if a.get("active")), None)
    snap = account_snapshot(accounts)
    if not active:
        log(f"{snap} :: skip (no active account)")
        return 0  # can't tell which account is active; nothing to do

    if active.get("email") == SOURCE_EMAIL:
        # We are on the primary: any armed "return at reset" is moot. Clear it so
        # a stale value can't drive an unwanted return after a manual switch.
        clear_pending_reset()
        decision = handle_source_active(
            swap, active.get("usage") or {}, active.get("usageStatus"), now
        )
    elif active.get("email") == TARGET_EMAIL:
        source = next(
            (a for a in accounts if a.get("email") == SOURCE_EMAIL), None
        )
        decision = handle_target_active(swap, source, now)
    else:
        decision = f"other account active ({active.get('email')})"
    log(f"{snap} :: {decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
