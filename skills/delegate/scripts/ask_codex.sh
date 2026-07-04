#!/bin/bash
# ask_codex.sh — Send a query to Codex CLI (headless mode)
# Uses ChatGPT Plus/Pro subscription auth (no API key needed)
#
# Usage:
#   bash ask_codex.sh "your prompt here" [output_file]
#   bash ask_codex.sh @prompt_file.md [output_file]
#   CODEX_TIMEOUT=900 bash ask_codex.sh "your prompt" output.md
#
# Output format:
#   For routine code tasks, append compact_code.txt to your prompt before calling.
#   Read output with: head -3 output.md
#   Use cat only if STATUS != DONE.
#
# Examples:
#   bash ask_codex.sh "Fix the bootstrap coverage in sim.R" result.md
#   bash ask_codex.sh @research/debug_mc.md research/codex_fix.md
#
# Environment variables:
#   CODEX_TIMEOUT   Internal timeout in seconds (default: 600)
#   CODEX_MODEL     Model to use (default: gpt-5.5)
#   CODEX_EFFORT    Reasoning effort for gpt-5.5: low|medium|high|xhigh (default: xhigh).
#                   Other models may accept additional tiers (e.g., `minimal`); verify
#                   per-model via: codex debug models

set -euo pipefail

# Portable timeout: GNU timeout → gtimeout (Homebrew) → perl fallback
_timeout() {
    local secs="$1"; shift
    if command -v timeout &>/dev/null; then
        timeout "$secs" "$@"
    elif command -v gtimeout &>/dev/null; then
        gtimeout "$secs" "$@"
    else
        perl -e '
            use POSIX ":sys_wait_h";
            alarm shift @ARGV;
            $SIG{ALRM} = sub { kill 9, $pid; exit 124 };
            $pid = fork // die "fork: $!";
            if ($pid == 0) { exec @ARGV; die "exec: $!" }
            waitpid($pid, 0);
            exit ($? >> 8);
        ' "$secs" "$@"
    fi
}

# Portable realpath -m (macOS lacks -m flag)
_realpath_m() {
    local target="$1"
    local dir; dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd || echo "$(dirname "$target")")"
    echo "$dir/$(basename "$target")"
}

QUERY="${1:-}"
OUTPUT="${2:-/tmp/claude/codex_result_$(date +%s)_$$.md}"
TIMEOUT="${CODEX_TIMEOUT:-600}"
MODEL="${CODEX_MODEL:-gpt-5.5}"
EFFORT="${CODEX_EFFORT:-xhigh}"

# Empty prompt guard
if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_codex.sh \"prompt\" [output.md]" >&2
    exit 1
fi

# Resolve output path to absolute
OUTPUT="$(_realpath_m "$OUTPUT")"
mkdir -p "$(dirname "$OUTPUT")"

# Check Codex CLI is available
if ! command -v codex &> /dev/null; then
    echo "ERROR: codex CLI not found. Install: npm install -g @openai/codex" >&2
    exit 1
fi

# If query starts with @, read from file
if [[ "$QUERY" == @* ]]; then
    QUERY_FILE="${QUERY:1}"
    if [[ ! -f "$QUERY_FILE" ]]; then
        echo "ERROR: Query file not found: $QUERY_FILE" >&2
        exit 1
    fi
    QUERY=$(cat "$QUERY_FILE")
fi

echo "[delegate/codex] Sending query ($(echo "$QUERY" | wc -c) chars)..." >&2
echo "[delegate/codex] Output: $OUTPUT" >&2

# Run Codex in exec (headless) mode with timeout
# --output-last-message writes the final assistant message to file
ERR_LOG="${OUTPUT%.md}.err"
if _timeout "$TIMEOUT" codex exec --model "$MODEL" -c model_reasoning_effort="$EFFORT" --skip-git-repo-check "$QUERY" --output-last-message "$OUTPUT" </dev/null 2>"$ERR_LOG"; then
    # Prepend metadata header
    TMP="$(mktemp)"
    { echo "<!-- ask_codex.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0 -->"; echo ""; cat "$OUTPUT"; } > "$TMP"
    mv "$TMP" "$OUTPUT"
    RESULT_SIZE=$(wc -c < "$OUTPUT")
    echo "[delegate/codex] Done. Result: $RESULT_SIZE bytes → $OUTPUT" >&2
else
    EXIT_CODE=$?
    # Prepend metadata header to whatever output exists
    if [[ -f "$OUTPUT" ]]; then
        TMP="$(mktemp)"
        { echo "<!-- ask_codex.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE -->"; echo ""; cat "$OUTPUT"; } > "$TMP"
        mv "$TMP" "$OUTPUT"
    else
        echo "<!-- ask_codex.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE -->" > "$OUTPUT"
    fi
    if [ $EXIT_CODE -eq 124 ]; then
        echo "ERROR: Codex timed out after ${TIMEOUT}s" >&2
        # Prepend timeout STATUS
        TMP="$(mktemp)"
        { echo "STATUS: FAILED — Codex timed out after ${TIMEOUT}s"; echo ""; cat "$OUTPUT"; } > "$TMP"
        mv "$TMP" "$OUTPUT"
    else
        echo "ERROR: Codex exited with code $EXIT_CODE (see $ERR_LOG)" >&2
    fi
    exit $EXIT_CODE
fi

echo "$OUTPUT"
