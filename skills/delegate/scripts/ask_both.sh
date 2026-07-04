#!/bin/bash
# ask_both.sh — Send the same query to Antigravity and Codex in parallel.
# Optionally adds Claude as a 3rd voter (majority-of-3 vote pattern).
# Useful for cross-verifying proofs, derivations, and identification arguments.
#
# Usage:
#   bash ask_both.sh "your prompt here" [antigravity_out] [codex_out] [claude_out]
#   bash ask_both.sh @prompt_file.md [antigravity_out] [codex_out] [claude_out]
#   INCLUDE_CLAUDE=1 bash ask_both.sh "prompt" agy.md cdx.md cl.md   # adds Claude as 3rd voter
#
# After completion:
#   head -3 antigravity_out.md   # VERDICT line
#   head -3 codex_out.md         # VERDICT line
#   Compare; read full output only on disagreement or ERROR/GAP.
#
# Examples:
#   bash ask_both.sh "Verify Proposition 3: ..." agy_verify.md codex_verify.md
#   bash ask_both.sh @research/verify_prop3.md research/agy_verify.md research/codex_verify.md
#
# Environment variables:
#   ANTIGRAVITY_TIMEOUT  Timeout for Antigravity in seconds (default: 600)
#   CODEX_TIMEOUT        Timeout for Codex in seconds (default: 600)
#   CLAUDE_TIMEOUT       Timeout for Claude in seconds (default: 600)
#   INCLUDE_CLAUDE       Set to "1" to also dispatch to Claude in parallel.
#                        When set, Claude failure is FATAL (exit 1) — callers opt in because
#                        they need majority-of-3 semantics; silent degradation to 2-voter
#                        would corrupt downstream vote logic.
#   DELEGATE_DIR         Directory containing ask_antigravity.sh, ask_codex.sh, ask_claude.sh
#                        (default: same directory as this script)

set -euo pipefail

# Portable realpath -m
_realpath_m() {
    local target="$1"
    local dir; dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd || echo "$(dirname "$target")")"
    echo "$dir/$(basename "$target")"
}

QUERY="${1:-}"
ANTIGRAVITY_OUT="${2:-${TMPDIR:-/tmp}/claude/antigravity_cross_$(date +%s)_$$.md}"
CODEX_OUT="${3:-${TMPDIR:-/tmp}/claude/codex_cross_$(date +%s)_$$.md}"
CLAUDE_OUT="${4:-${TMPDIR:-/tmp}/claude/claude_cross_$(date +%s)_$$.md}"

# Detect a legacy 4-arg call: if a 4th arg is passed WITHOUT INCLUDE_CLAUDE=1,
# warn and ignore (catches stale callers).
if [[ -n "${4:-}" && "${INCLUDE_CLAUDE:-}" != "1" ]]; then
    echo "[delegate/both] WARN: 4th argument provided ('$4') but INCLUDE_CLAUDE!=1. Ignoring." >&2
    echo "[delegate/both]       (An earlier version accepted a 4th output arg; that path has been removed.)" >&2
fi

# Empty prompt guard
if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_both.sh \"prompt\" [antigravity_out.md] [codex_out.md] [claude_out.md]" >&2
    echo "  Set INCLUDE_CLAUDE=1 to also dispatch to Claude (3rd voter for majority-of-3 votes)" >&2
    exit 1
fi

# Resolve script directory
SCRIPT_DIR="${DELEGATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ASK_ANTIGRAVITY="${SCRIPT_DIR}/ask_antigravity.sh"
ASK_CODEX="${SCRIPT_DIR}/ask_codex.sh"
ASK_CLAUDE="${SCRIPT_DIR}/ask_claude.sh"

# Script existence checks
if [[ ! -f "$ASK_ANTIGRAVITY" ]]; then
    echo "ERROR: ask_antigravity.sh not found at ${ASK_ANTIGRAVITY}" >&2
    exit 1
fi
if [[ ! -f "$ASK_CODEX" ]]; then
    echo "ERROR: ask_codex.sh not found at ${ASK_CODEX}" >&2
    exit 1
fi
if [[ "${INCLUDE_CLAUDE:-}" == "1" && ! -f "$ASK_CLAUDE" ]]; then
    echo "ERROR: INCLUDE_CLAUDE=1 set but ask_claude.sh not found at ${ASK_CLAUDE}" >&2
    exit 1
fi

# Resolve output paths
ANTIGRAVITY_OUT="$(_realpath_m "$ANTIGRAVITY_OUT")"
CODEX_OUT="$(_realpath_m "$CODEX_OUT")"
CLAUDE_OUT="$(_realpath_m "$CLAUDE_OUT")"

echo "[delegate/both] Starting parallel cross-verification..." >&2
echo "[delegate/both]   Antigravity → ${ANTIGRAVITY_OUT}" >&2
echo "[delegate/both]   Codex       → ${CODEX_OUT}" >&2

# Launch primary 2 legs in parallel
bash "$ASK_ANTIGRAVITY" "$QUERY" "$ANTIGRAVITY_OUT" &
ANTIGRAVITY_PID=$!

bash "$ASK_CODEX" "$QUERY" "$CODEX_OUT" &
CODEX_PID=$!

# Optionally launch Claude (3rd voter)
CLAUDE_PID=""
if [[ "${INCLUDE_CLAUDE:-}" == "1" ]]; then
    echo "[delegate/both]   Claude      → ${CLAUDE_OUT}" >&2
    bash "$ASK_CLAUDE" "$QUERY" "$CLAUDE_OUT" &
    CLAUDE_PID=$!
fi

# Wait for all and capture exit codes
ANTIGRAVITY_EXIT=0
CODEX_EXIT=0
CLAUDE_EXIT=0

wait $ANTIGRAVITY_PID || ANTIGRAVITY_EXIT=$?
wait $CODEX_PID       || CODEX_EXIT=$?
if [[ -n "$CLAUDE_PID" ]]; then
    wait $CLAUDE_PID || CLAUDE_EXIT=$?
fi

# Report results
echo "" >&2
echo "[delegate/both] Results:" >&2

if [[ $ANTIGRAVITY_EXIT -eq 0 ]]; then
    echo "[delegate/both]   Antigravity: OK — $(head -3 "$ANTIGRAVITY_OUT" | tail -1)" >&2
else
    echo "[delegate/both]   Antigravity: FAILED (exit=${ANTIGRAVITY_EXIT})" >&2
fi

if [[ $CODEX_EXIT -eq 0 ]]; then
    echo "[delegate/both]   Codex:       OK — $(head -3 "$CODEX_OUT" | tail -1)" >&2
else
    echo "[delegate/both]   Codex:       FAILED (exit=${CODEX_EXIT})" >&2
fi

if [[ -n "$CLAUDE_PID" ]]; then
    if [[ $CLAUDE_EXIT -eq 0 ]]; then
        echo "[delegate/both]   Claude:      OK — $(head -3 "$CLAUDE_OUT" | tail -1)" >&2
    else
        echo "[delegate/both]   Claude:      FAILED (exit=${CLAUDE_EXIT})" >&2
    fi
fi

echo "" >&2
if [[ -n "$CLAUDE_PID" ]]; then
    echo "[delegate/both] Next step: head -3 ${ANTIGRAVITY_OUT} && head -3 ${CODEX_OUT} && head -3 ${CLAUDE_OUT}" >&2
else
    echo "[delegate/both] Next step: head -3 ${ANTIGRAVITY_OUT} && head -3 ${CODEX_OUT}" >&2
fi
echo "[delegate/both] Compare verdicts. Read full output only on disagreement or ERROR/GAP." >&2

# Output file paths for the caller
echo "$ANTIGRAVITY_OUT"
echo "$CODEX_OUT"
if [[ -n "$CLAUDE_PID" ]]; then
    echo "$CLAUDE_OUT"
fi

# Exit non-zero on ANY failure. When INCLUDE_CLAUDE=1, Claude failure is fatal
# (callers opt in because they need majority-of-3 semantics; silent degradation
# to 2-voter would corrupt vote logic).
if [[ $ANTIGRAVITY_EXIT -ne 0 || $CODEX_EXIT -ne 0 ]]; then
    exit 1
fi
if [[ -n "$CLAUDE_PID" && $CLAUDE_EXIT -ne 0 ]]; then
    exit 1
fi
