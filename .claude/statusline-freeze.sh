#!/bin/bash
#
# Freeze the status line while this session's iTerm2 tab is in the background.
#
# Usage (from settings.json statusLine.command):
#     bash ~/.claude/statusline-freeze.sh <real statusline command...>
#
# stdin (the status line JSON) is read once here and piped through to the real
# command, whose stdout is passed back unchanged.
#
# Why this exists: statusLine.refreshInterval re-runs the status line on a
# timer so the clock stays live. That changed the rendered line every few
# seconds even on an idle session, Claude Code wrote the new frame to the PTY,
# and iTerm2 flagged the tab as having activity -- so every parked Claude tab
# looked like it had news.
#
# Claude Code's renderer diffs the frame cell by cell and emits stdout ops only
# for cells that actually changed, so replaying a BYTE-IDENTICAL line writes
# nothing at all and the indicator never fires. That is the whole trick: when
# the tab is not frontmost, replay the last line verbatim instead of rendering
# a new one. Refocusing restores a live line within one refreshInterval.
#
# This wraps the command rather than living inside it so the bash status line
# and the blackbox Python wrapper share one implementation -- and so only ONE
# layer ever freezes. Two nested layers would each run the focus check and
# fight over the same cache file.

set -uo pipefail

CACHE_DIR="$HOME/.claude/statusline-frozen"
ITERM_BUNDLE_ID="com.googlecode.iterm2"

# Whether this session's iTerm2 tab is the one being looked at.
#
# Returns true (render live) whenever focus cannot be established -- not
# iTerm2, no session id, a lookup that fails -- so an unknown state never
# freezes a stale line.
#
# Cheap check first: lsappinfo (~30ms) settles the common case, where some
# other app is frontmost and therefore EVERY iTerm2 tab is backgrounded. The
# ~200ms AppleScript that identifies the individual tab runs only when iTerm2
# itself is frontmost, which is the only time the answer depends on the tab.
tab_is_focused() {
    local uuid="${ITERM_SESSION_ID:-}"
    uuid="${uuid#*:}"                       # w1t12p0:UUID -> UUID
    [[ -z "$uuid" ]] && return 0

    local asn front_app front_session
    asn=$(lsappinfo front 2>/dev/null) || return 0
    [[ -z "$asn" ]] && return 0

    front_app=$(lsappinfo info -only bundleid "$asn" 2>/dev/null) || return 0
    # Another app entirely is frontmost, so no iTerm2 tab is focused. Note that
    # AppleScript's "current session of current window" answers even when iTerm2
    # is in the background, so this gate is load-bearing, not an optimization.
    [[ "$front_app" != *"$ITERM_BUNDLE_ID"* ]] && return 1

    front_session=$(osascript -e \
        'tell application "iTerm2" to return id of current session of current window' \
        2>/dev/null) || return 0
    [[ -z "$front_session" ]] && return 0
    [[ "$front_session" == "$uuid" ]]
}

[[ $# -gt 0 ]] || { echo "usage: statusline-freeze.sh <command...>" >&2; exit 2; }

input=$(cat)

session_id=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)
cache="$CACHE_DIR/${session_id:-default}.txt"

if [[ -n "$session_id" ]] && ! tab_is_focused && [[ -s "$cache" ]]; then
    cat "$cache"
    exit 0
fi

# Render live. Keep the real command's exit status and output even if the cache
# write fails -- a broken cache must never take down the status line.
output=$(printf '%s' "$input" | "$@")
status=$?

if [[ -n "$session_id" ]]; then
    mkdir -p "$CACHE_DIR" 2>/dev/null && printf '%s' "$output" >"$cache" 2>/dev/null
fi

printf '%s' "$output"
exit $status
