#!/bin/bash
# ask_antigravity.sh — Send a query to Antigravity CLI (`agy`) in headless mode
# Uses Google Antigravity subscription auth (OAuth from interactive `agy` login).
#
# Usage:
#   bash ask_antigravity.sh "your prompt here" [output_file]
#   bash ask_antigravity.sh @prompt_file.md [output_file]
#   ANTIGRAVITY_TIMEOUT=900 bash ask_antigravity.sh "your prompt" output.md
#
# Output format:
#   For verification tasks, append compact_verify.txt to your prompt before calling.
#   Read output with: head -3 output.md
#   Use cat only if VERDICT != CORRECT or for literature surveys.
#
# Examples:
#   bash ask_antigravity.sh "Verify this asymptotic normality proof: ..." result.md
#   bash ask_antigravity.sh @research/verify_prop3.md research/antigravity_verify.md
#
# Environment variables:
#   ANTIGRAVITY_TIMEOUT   Internal timeout in seconds, integer (default: 600)
#   ANTIGRAVITY_MODEL     Recorded in metadata header only — `agy` v1.0.0 has NO --model flag.
#                         Model is configured in ~/.gemini/antigravity-cli/settings.json or via
#                         `agy` interactive /model command. Default: "gemini-3.1-pro"
#   ANTIGRAVITY_YOLO      Set to "1" to pass --dangerously-skip-permissions (default: 0, off).
#                         Required if agy may call tools (web search, file ops). For pure text
#                         verification queries, leave off.
#
# Notes:
#   - `agy` binds a localhost TCP port for its in-process language server. Under Claude Code
#     sandbox this fails with `bind: operation not permitted`. The script detects this and
#     synthesizes a clear STATUS: FAILED line. Callers must use dangerouslyDisableSandbox=true
#     or add a sandbox carveout.
#   - The sidecar `${output%.md}.err` is preserved on success and failure for forensics.

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
            my $secs = shift @ARGV;
            my $pid = fork // die "fork: $!";
            if ($pid == 0) { exec @ARGV; die "exec: $!" }
            $SIG{ALRM} = sub { kill 9, $pid; exit 124 };
            alarm $secs;
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

# Classify failure from stderr — synthesize specific STATUS: FAILED line
_classify_failure() {
    local err_file="$1"
    local exit_code="$2"
    local timeout="$3"

    if [[ "$exit_code" == "124" ]]; then
        echo "STATUS: FAILED — Antigravity timed out after ${timeout}s"
        return
    fi
    if [[ ! -s "$err_file" ]]; then
        echo "STATUS: FAILED — Antigravity exited $exit_code with empty stderr (see ${err_file%.err}.err if any)"
        return
    fi

    local stderr_head
    stderr_head="$(head -20 "$err_file" 2>/dev/null)"

    if grep -qiE "operation not permitted|bind: address" <<< "$stderr_head"; then
        echo "STATUS: FAILED — Sandbox blocked agy TCP bind. Run outside sandbox or use dangerouslyDisableSandbox=true"
    elif grep -qiE "unauthenticated|please log in|not.{0,5}authenticated|auth.{0,10}required|invalid.{0,5}api.{0,5}key" <<< "$stderr_head"; then
        echo "STATUS: FAILED — Antigravity auth not configured. Run \`agy\` interactively to log in"
    elif grep -qiE "quota|rate.?limit|429|too many requests" <<< "$stderr_head"; then
        echo "STATUS: FAILED — Antigravity quota exhausted or rate-limited"
    elif grep -qiE "unknown model|model not.{0,10}(found|supported|available)|404" <<< "$stderr_head"; then
        echo "STATUS: FAILED — Antigravity model unavailable (configured in ~/.gemini/antigravity-cli/settings.json)"
    else
        echo "STATUS: FAILED — Antigravity exited $exit_code; first stderr lines: $(head -3 "$err_file" | tr '\n' ' ' | cut -c1-200)"
    fi
}

# === Inputs ===
QUERY="${1:-}"
OUTPUT="${2:-${TMPDIR:-/tmp}/claude/antigravity_result_$(date +%s)_$$.md}"
TIMEOUT="${ANTIGRAVITY_TIMEOUT:-600}"
MODEL="${ANTIGRAVITY_MODEL:-gemini-3.1-pro}"
YOLO="${ANTIGRAVITY_YOLO:-0}"

# Empty prompt guard
if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_antigravity.sh \"prompt\" [output.md]" >&2
    echo "       ask_antigravity.sh @prompt_file.md [output.md]" >&2
    exit 1
fi

# Validate TIMEOUT is integer seconds
if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: ANTIGRAVITY_TIMEOUT must be integer seconds, got: $TIMEOUT" >&2
    echo "       Reject: '5m', '600s', decimals, empty. Use plain integer (e.g. 600)." >&2
    exit 1
fi

# Resolve output path to absolute
OUTPUT="$(_realpath_m "$OUTPUT")"
mkdir -p "$(dirname "$OUTPUT")"

# Binary lookup: prefer PATH, fall back to ~/.local/bin/agy
if command -v agy &>/dev/null; then
    AGY_BIN="agy"
elif [[ -x "$HOME/.local/bin/agy" ]]; then
    AGY_BIN="$HOME/.local/bin/agy"
else
    echo "ERROR: agy CLI not found. Install: curl -fsSL https://antigravity.google/cli/install.sh | bash" >&2
    exit 1
fi

# CLI contract check: required flags must be present
HELP_OUT="$("$AGY_BIN" --help 2>&1 || true)"
for flag in "--print" "--print-timeout"; do
    if ! grep -q -- "$flag" <<< "$HELP_OUT"; then
        echo "ERROR: agy CLI missing required flag '$flag'. Installed: $("$AGY_BIN" --version 2>&1 | head -1)" >&2
        exit 1
    fi
done

# If query starts with @, read from file
if [[ "$QUERY" == @* ]]; then
    QUERY_FILE="${QUERY:1}"
    if [[ ! -f "$QUERY_FILE" ]]; then
        echo "ERROR: Query file not found: $QUERY_FILE" >&2
        exit 1
    fi
    QUERY=$(cat "$QUERY_FILE")
fi

echo "[delegate/antigravity] Sending query ($(echo "$QUERY" | wc -c | tr -d ' ') chars)..." >&2
echo "[delegate/antigravity] Output: $OUTPUT" >&2

# Compute inner --print-timeout (10s lower than outer wrapper, min 30s)
if [[ "$TIMEOUT" -lt 30 ]]; then
    INNER_TIMEOUT="${TIMEOUT}s"
else
    INNER_TIMEOUT="$((TIMEOUT - 10))s"
fi

# Build agy command
AGY_CMD=("$AGY_BIN" --print "$QUERY" --print-timeout "$INNER_TIMEOUT")
if [[ "$YOLO" == "1" ]]; then
    AGY_CMD+=(--dangerously-skip-permissions)
fi

# Run agy in headless mode with outer timeout
ERR_LOG="${OUTPUT%.md}.err"
EXIT_CODE=0
if _timeout "$TIMEOUT" "${AGY_CMD[@]}" </dev/null > "$OUTPUT" 2>"$ERR_LOG"; then
    # On success, check for empty output (a known silent-failure mode)
    if [[ ! -s "$OUTPUT" ]]; then
        FAILURE_LINE="$(_classify_failure "$ERR_LOG" 0 "$TIMEOUT")"
        TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
        {
            echo "<!-- ask_antigravity.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0-empty | model=$MODEL | yolo=$YOLO -->"
            echo ""
            echo "$FAILURE_LINE"
        } > "$TMP"
        mv "$TMP" "$OUTPUT"
        echo "[delegate/antigravity] WARN: agy exited 0 but produced empty output. Classified failure written to $OUTPUT" >&2
        echo "$OUTPUT"
        exit 1
    fi

    # Normal success: prepend metadata header
    TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
    {
        echo "<!-- ask_antigravity.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0 | model=$MODEL | yolo=$YOLO -->"
        echo ""
        cat "$OUTPUT"
    } > "$TMP"
    mv "$TMP" "$OUTPUT"
    RESULT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
    echo "[delegate/antigravity] Done. Result: $RESULT_SIZE bytes → $OUTPUT (stderr: $ERR_LOG)" >&2
else
    EXIT_CODE=$?
    FAILURE_LINE="$(_classify_failure "$ERR_LOG" "$EXIT_CODE" "$TIMEOUT")"

    # Prepend metadata header + classified failure line
    TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
    {
        echo "<!-- ask_antigravity.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE | model=$MODEL | yolo=$YOLO -->"
        echo ""
        echo "$FAILURE_LINE"
        echo ""
        [[ -f "$OUTPUT" ]] && cat "$OUTPUT" || true
    } > "$TMP"
    mv "$TMP" "$OUTPUT"
    echo "ERROR: $FAILURE_LINE (see $ERR_LOG)" >&2
    echo "$OUTPUT"
    exit "$EXIT_CODE"
fi

echo "$OUTPUT"
