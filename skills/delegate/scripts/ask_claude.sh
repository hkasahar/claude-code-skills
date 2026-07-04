#!/bin/bash
# ask_claude.sh — Send a query to Claude Code CLI (`claude`) in headless mode
# Used as the 3rd voter in a majority-of-3 vote (e.g. to break Codex/Antigravity ties).
#
# Usage:
#   bash ask_claude.sh "your prompt here" [output_file]
#   bash ask_claude.sh @prompt_file.md [output_file]
#   CLAUDE_TIMEOUT=900 CLAUDE_MODEL=opus bash ask_claude.sh "your prompt" output.md
#
# Output format:
#   For verification/voting tasks, append compact_verify.txt to your prompt.
#   Read output with: head -3 output.md
#   Use cat only if VERDICT != CORRECT.
#
# Examples:
#   bash ask_claude.sh "Verify this asymptotic normality proof: ..." result.md
#   bash ask_claude.sh @research/verify_prop3.md research/claude_verify.md
#
# Environment variables:
#   CLAUDE_TIMEOUT   Internal timeout in seconds, integer (default: 600)
#   CLAUDE_MODEL     Model alias or full name (default: "opus" → latest Claude Opus).
#                    Examples: "opus", "sonnet", "claude-opus-4-7", "claude-sonnet-4-6"
#   CLAUDE_EFFORT    Reasoning effort: low|medium|high|xhigh|max (default: max)
#   ANTHROPIC_API_KEY  Required for headless invocation (no OAuth in non-interactive mode).
#                      Alternative: pre-configure via `claude setup-token`.
#
# Notes:
#   - Headless `claude -p` requires API key auth — OAuth keychain is not used in non-interactive mode.
#   - The wrapper passes `--tools ""` to disable all tool use during voting (verified in agy v2.1.150).
#     This makes the 3rd voter a pure judgment dispatch, not an agentic call.
#   - Independence caveat: Claude as 3rd voter shares the model family with the orchestrating
#     Claude Code session. Mechanical 2-of-3 vote semantics are preserved, but statistical
#     independence is reduced vs. a third model from a different vendor.

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

# Portable realpath -m
_realpath_m() {
    local target="$1"
    local dir; dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd || echo "$(dirname "$target")")"
    echo "$dir/$(basename "$target")"
}

# Classify failure from stderr AND stdout (claude often writes errors to stdout)
_classify_failure() {
    local err_file="$1"
    local exit_code="$2"
    local timeout="$3"
    local out_file="${4:-}"  # optional: output file to also scan

    if [[ "$exit_code" == "124" ]]; then
        echo "STATUS: FAILED — Claude timed out after ${timeout}s"
        return
    fi

    # Concatenate stderr + stdout heads for pattern matching
    local diag_text=""
    [[ -s "$err_file" ]] && diag_text="$(head -20 "$err_file" 2>/dev/null)"
    if [[ -n "$out_file" && -s "$out_file" ]]; then
        diag_text="${diag_text}
$(head -20 "$out_file" 2>/dev/null)"
    fi

    if [[ -z "$diag_text" ]]; then
        echo "STATUS: FAILED — Claude exited $exit_code with empty stderr/stdout"
        return
    fi

    if grep -qiE "invalid.{0,5}api.{0,5}key|fix external api key|unauthenticated|api key.{0,10}(missing|not set)" <<< "$diag_text"; then
        echo "STATUS: FAILED — Claude headless auth not configured. Run \`claude setup-token\` or set ANTHROPIC_API_KEY"
    elif grep -qiE "quota|rate.?limit|429|too many requests|usage limit" <<< "$diag_text"; then
        echo "STATUS: FAILED — Claude quota exhausted or rate-limited"
    elif grep -qiE "unknown model|model not.{0,10}(found|supported|available)" <<< "$diag_text"; then
        echo "STATUS: FAILED — Claude model unavailable: $CLAUDE_MODEL"
    elif grep -qiE "operation not permitted" <<< "$diag_text"; then
        echo "STATUS: FAILED — Sandbox restriction blocked claude (likely nice/keychain)"
    else
        local diag_summary
        diag_summary="$(echo "$diag_text" | head -3 | tr '\n' ' ' | cut -c1-200)"
        echo "STATUS: FAILED — Claude exited $exit_code; first lines: ${diag_summary}"
    fi
}

# === Inputs ===
QUERY="${1:-}"
OUTPUT="${2:-${TMPDIR:-/tmp}/claude/claude_result_$(date +%s)_$$.md}"
TIMEOUT="${CLAUDE_TIMEOUT:-600}"
MODEL="${CLAUDE_MODEL:-opus}"
EFFORT="${CLAUDE_EFFORT:-max}"

# Empty prompt guard
if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_claude.sh \"prompt\" [output.md]" >&2
    echo "       ask_claude.sh @prompt_file.md [output.md]" >&2
    exit 1
fi

# Validate TIMEOUT is integer seconds
if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: CLAUDE_TIMEOUT must be integer seconds, got: $TIMEOUT" >&2
    exit 1
fi

# Resolve output path to absolute
OUTPUT="$(_realpath_m "$OUTPUT")"
mkdir -p "$(dirname "$OUTPUT")"

# Check claude CLI is available
if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found. Install Claude Code from https://claude.com/claude-code" >&2
    exit 1
fi

# CLI contract check
HELP_OUT="$(claude --help 2>&1 || true)"
for flag in "--print" "--model" "--tools"; do
    if ! grep -q -- "$flag" <<< "$HELP_OUT"; then
        echo "ERROR: claude CLI missing required flag '$flag'. Installed: $(claude --version 2>&1 | head -1)" >&2
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

echo "[delegate/claude] Sending query ($(echo "$QUERY" | wc -c | tr -d ' ') chars)..." >&2
echo "[delegate/claude] Output: $OUTPUT" >&2

# Build claude command — --tools "" disables all tools for pure judgment
# Use --bare to skip CLAUDE.md auto-discovery (3rd voter must be context-free)
CLAUDE_CMD=(
    claude
    --print
    --model "$MODEL"
    --effort "$EFFORT"
    --tools ""
    --bare
    --no-session-persistence
    --output-format text
)

# Run claude with outer timeout; prompt is positional (last arg)
ERR_LOG="${OUTPUT%.md}.err"
EXIT_CODE=0
if _timeout "$TIMEOUT" "${CLAUDE_CMD[@]}" "$QUERY" </dev/null > "$OUTPUT" 2>"$ERR_LOG"; then
    if [[ ! -s "$OUTPUT" ]]; then
        FAILURE_LINE="$(_classify_failure "$ERR_LOG" 0 "$TIMEOUT" "$OUTPUT")"
        TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
        {
            echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0-empty | model=$MODEL | effort=$EFFORT -->"
            echo ""
            echo "$FAILURE_LINE"
        } > "$TMP"
        mv "$TMP" "$OUTPUT"
        echo "[delegate/claude] WARN: claude exited 0 but produced empty output. Classified: $FAILURE_LINE" >&2
        echo "$OUTPUT"
        exit 1
    fi

    TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
    {
        echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0 | model=$MODEL | effort=$EFFORT -->"
        echo ""
        cat "$OUTPUT"
    } > "$TMP"
    mv "$TMP" "$OUTPUT"
    RESULT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
    echo "[delegate/claude] Done. Result: $RESULT_SIZE bytes → $OUTPUT (stderr: $ERR_LOG)" >&2
else
    EXIT_CODE=$?
    FAILURE_LINE="$(_classify_failure "$ERR_LOG" "$EXIT_CODE" "$TIMEOUT" "$OUTPUT")"

    TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.tmp")"
    {
        echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE | model=$MODEL | effort=$EFFORT -->"
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
