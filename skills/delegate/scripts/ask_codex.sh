#!/bin/bash
# ask_codex.sh — Send a prompt or @file through Codex; paid credit fallback is argv-only and off by default.
# See ../references/legs.md for modes, dependencies, costs and limitations.
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

CREDIT_FALLBACK=off
if [[ "${CODEX_CREDIT_FALLBACK+x}" == x ]]; then
    echo "[delegate/codex] WARN: CODEX_CREDIT_FALLBACK is ignored; use --credit-fallback explicitly" >&2
fi
if [[ "${1:-}" == --credit-fallback* ]]; then
    if [[ "$1" != --credit-fallback ]]; then
        echo "Usage: ask_codex.sh [--credit-fallback off|deepseek|deepseek-api] [--] <prompt|@file> [output]" >&2
        exit 2
    fi
    CREDIT_FALLBACK="${2:-}"
    case "$CREDIT_FALLBACK" in
        off|deepseek|deepseek-api) shift 2 ;;
        *) echo "Usage: ask_codex.sh [--credit-fallback off|deepseek|deepseek-api] [--] <prompt|@file> [output]" >&2; exit 2 ;;
    esac
fi
[[ "${1:-}" == -- ]] && shift
QUERY="${1:-}"
OUTPUT="${2:-/tmp/claude/codex_result_$(date +%s)_$$.md}"
TIMEOUT="${CODEX_TIMEOUT:-3600}"
MODEL="${CODEX_MODEL:-gpt-6.1-sol}"
EFFORT="${CODEX_EFFORT:-xhigh}"
if [[ "$MODEL" == deepseek/* && ( "$EFFORT" == max || "$EFFORT" == ultra ) ]]; then
    echo "Usage error: DeepSeek is forbidden for max and ultra dispatches (CODEX_MODEL=$MODEL)" >&2
    exit 2
fi
if [[ "$CREDIT_FALLBACK" != off && ( "$EFFORT" == max || "$EFFORT" == ultra ) ]]; then
    echo "Usage error: --credit-fallback is forbidden for max and ultra dispatches" >&2
    exit 2
fi
REQUESTED_MODEL="$MODEL"
REQUESTED_EFFORT="$EFFORT"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEEPSEEK_MODEL=deepseek/deepseek-v4.1-flash-1017
DEEPSEEK_HARNESS_MODEL="${DEEPSEEK_MODEL}@preset/deepseek-codex"

# Empty prompt guard
if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_codex.sh [--credit-fallback off|deepseek|deepseek-api] [--] <prompt|@file> [output]" >&2
    exit 1
fi

# Resolve output path to absolute
OUTPUT="$(_realpath_m "$OUTPUT")"
mkdir -p "$(dirname "$OUTPUT")"
rm -f "${OUTPUT%.md}.codex.err"

# Preserve the ordinary missing-CLI failure before reading or logging the prompt.
if [[ "$MODEL" != deepseek/* ]] && ! command -v codex &> /dev/null; then
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

# All attempts share one deadline and publish one terminal envelope. Per-attempt
# output is discarded on retry so headers and partial bodies never accumulate.
ERR_LOG="${OUTPUT%.md}.err"
CAPACITY_RETRIES="${CODEX_CAPACITY_RETRIES:-1}"
CAPACITY_BACKOFF="${CODEX_CAPACITY_BACKOFF:-45}"
CAPACITY_PATTERN='^ERROR: Selected model is at capacity'
_is_capacity() {
    tail -20 "$1" 2>/dev/null | grep -q "$CAPACITY_PATTERN"
}

# Codex stderr can quote the prompt. Only top-level errors at the tail classify;
# C locale makes .{1,3} accept either ASCII or the three-byte curly apostrophe.
_usage_limit() {
    local recent
    recent="$(tail -40 "$1" 2>/dev/null)"
    if LC_ALL=C grep -qE '^ERROR: You.{1,3}ve hit your usage limit for ' <<< "$recent"; then
        echo model
    elif LC_ALL=C grep -qE '^ERROR: You.{1,3}ve hit your usage limit[.]|^ERROR: Your workspace is out of credits|^ERROR: You hit your spend cap|^ERROR: You.{1,3}re out of credits' <<< "$recent"; then
        echo account
    fi
}

# Unknown provenance fails closed: repeating a write-capable prompt after tool
# calls could apply it twice. No session id means the CLI never started a run.
_session_id() {
    local line uuid='^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})[[:space:]]*$'
    # The banner precedes transcript echoes, which may quote another session.
    line="$(awk '/^session id: / {print; exit}' "$1")" || { echo unknown; return; }
    if [[ -z "$line" ]]; then
        echo none
    elif [[ "${line#session id: }" =~ $uuid ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo unknown
    fi
}

_no_tool_calls() {
    local session rollout count
    for session in "${ATTEMPT_SESSIONS[@]}" "$(_session_id "$1")"; do
        case "$session" in none) continue ;; unknown) return 1 ;; esac
        command -v jq >/dev/null 2>&1 || return 1
        rollout="$(find "${CODEX_HOME:-$HOME/.codex}/sessions" -type f -name "rollout-*-${session}.jsonl" -print -quit 2>/dev/null)" || return 1
        [[ -n "$rollout" && -r "$rollout" ]] || return 1
        count="$(jq -s '[.[] | select(.type=="response_item") | .payload | select((.type // "") | test("call"))] | length' "$rollout" 2>/dev/null)" || return 1
        [[ "$count" == 0 ]] || return 1
    done
    return 0
}

DEADLINE=$((SECONDS + TIMEOUT))
ATTEMPT=0
ATTEMPT_SESSIONS=(none)
EXIT_CODE=0
FALLBACK=""
PROVIDER=""
FAILURE_STATUS=""
HARNESS=0
HARNESS_READY=0
TMP_OUT="$(mktemp)"
TMP_ERR="$(mktemp)"
PROMPT_TMP=""
KEY_OUT=""
KEY_ERR=""
FINAL=""
trap 'rm -f "$TMP_OUT" "$TMP_ERR" "$PROMPT_TMP" "$KEY_OUT" "$KEY_ERR" "$FINAL"' EXIT

case "$MODEL" in
    "$DEEPSEEK_MODEL"|"$DEEPSEEK_HARNESS_MODEL")
        FALLBACK=direct
        HARNESS=1
        MODEL="$DEEPSEEK_HARNESS_MODEL"
        REQUESTED_EFFORT="${CODEX_EFFORT:-max}"
        if [[ "${CODEX_EFFORT+x}" == x && "${CODEX_EFFORT:-}" != max ]]; then
            echo "[delegate/codex] WARN: DeepSeek ignores CODEX_EFFORT=$CODEX_EFFORT; using max" >&2
        fi
        EFFORT=max
        ;;
    deepseek/*)
        EXIT_CODE=2
        FAILURE_STATUS="STATUS: FAILED — unadmitted DeepSeek model '$MODEL'"
        ;;
esac

# An unadmitted direct selection publishes its failure even without the CLI.
if [[ "$HARNESS" == 1 ]] && ! command -v codex &> /dev/null; then
    echo "ERROR: codex CLI not found. Install: npm install -g @openai/codex" >&2
    exit 1
fi

_prepare_harness() {
    local rc=0 key_status
    REMAINING=$((DEADLINE - SECONDS))
    if (( REMAINING < 120 )); then
        EXIT_CODE=124
        FAILURE_STATUS="STATUS: FAILED — deadline: fewer than 120s remain for DeepSeek harness"
        return 1
    fi
    KEY_OUT="$(mktemp)"
    KEY_ERR="$(mktemp)"
    # Bound each key-check GET so curl exits cleanly and the wrapper releases its lock.
    _timeout "$REMAINING" env -u OPENROUTER_API_KEY -u DEEPSEEK_DELEGATE_KEY_FILE \
        DEEPSEEK_DELEGATE_TIMEOUT=15 \
        bash "$SCRIPT_DIR/ask_deepseek.sh" --key-check >"$KEY_OUT" 2>"$KEY_ERR" || rc=$?
    cat "$KEY_ERR" >&2
    key_status="$(grep '^STATUS:' "$KEY_OUT" | head -1 || true)"
    # Older wrappers may accept --key-check as a prompt. A successful exit alone
    # cannot establish that the fixed credential's cap was actually checked.
    if (( rc != 0 )) || [[ "$key_status" != 'STATUS: OK — key-check:'* ]]; then
        [[ $rc -eq 0 ]] && rc=1
        EXIT_CODE=$rc
        FAILURE_STATUS="STATUS: FAILED — deepseek key-check: ${key_status:-no STATUS line (exit $rc)}"
        return 1
    fi
    REMAINING=$((DEADLINE - SECONDS))
    if (( REMAINING < 120 )); then
        EXIT_CODE=124
        FAILURE_STATUS="STATUS: FAILED — deadline: fewer than 120s remain for DeepSeek harness"
        return 1
    fi
    HARNESS_READY=1
}

_run_api() {
    local api_output api_header resolved api_timeout
    REMAINING=$((DEADLINE - SECONDS))
    if (( REMAINING < 60 )); then
        EXIT_CODE=124
        FAILURE_STATUS="STATUS: FAILED — deadline: fewer than 60s remain for DeepSeek API"
        return
    fi
    api_timeout=$((REMAINING - 10))
    (( api_timeout > 1800 )) && api_timeout=1800
    PROMPT_TMP="$(mktemp)"
    chmod 600 "$PROMPT_TMP"
    printf '%s' "$QUERY" > "$PROMPT_TMP"
    api_output="${OUTPUT%.md}.deepseek.md"
    # A failed launch must not republish a prior dispatch's sidecar as this body.
    rm -f "$api_output" "${api_output%.md}.json" "${api_output%.md}.err"
    EXIT_CODE=0
    _timeout "$REMAINING" env DEEPSEEK_DELEGATE_TIMEOUT="$api_timeout" \
        DEEPSEEK_DELEGATE_MODEL="$DEEPSEEK_MODEL" DEEPSEEK_DELEGATE_EFFORT=max \
        bash "$SCRIPT_DIR/ask_deepseek.sh" "@$PROMPT_TMP" "$api_output" > /dev/null 2>>"$TMP_ERR" || EXIT_CODE=$?
    : > "$TMP_OUT"
    if [[ -f "$api_output" ]]; then
        api_header="$(head -1 "$api_output")"
        resolved="$(sed -n 's/.* resolved=\([^ |]*\).*/\1/p' <<< "$api_header")"
        if [[ "$resolved" == *+* ]]; then
            MODEL="${resolved%%+*}"
            PROVIDER="${resolved#*+}"
        fi
        awk 'NR==1 && /^<!--/ {header=1; next} header && NR==2 && /^[[:space:]]*$/ {next} {print}' "$api_output" > "$TMP_OUT"
    fi
    if [[ ! -s "$TMP_OUT" ]]; then
        if [[ $EXIT_CODE -eq 124 ]]; then
            FAILURE_STATUS="STATUS: FAILED — DeepSeek API timed out (inner limit ${api_timeout}s; end-to-end deadline ${TIMEOUT}s)"
        elif [[ $EXIT_CODE -ne 0 ]]; then
            FAILURE_STATUS="STATUS: FAILED — DeepSeek API exited $EXIT_CODE (see $ERR_LOG)"
        else
            EXIT_CODE=1
            FAILURE_STATUS="STATUS: FAILED — DeepSeek API returned no body (see $ERR_LOG)"
        fi
    fi
}

while [[ -z "$FAILURE_STATUS" ]]; do
    if [[ "$HARNESS" == 1 && "$HARNESS_READY" == 0 ]]; then
        _prepare_harness || break
    fi
    REMAINING=$((DEADLINE - SECONDS))
    if (( REMAINING <= 0 )); then
        EXIT_CODE=124
        echo "deadline exhausted before attempt could start" > "$TMP_ERR"
        break
    fi
    # Both Codex providers use precisely the same invocation, timeout and output
    # path. The preset and fixed credential bind the checked cap to the paid call.
    COMMAND=(codex exec --model "$MODEL")
    if [[ "$HARNESS" == 1 ]]; then
        COMMAND=(env -u OPENROUTER_API_KEY -u DEEPSEEK_DELEGATE_KEY_FILE "${COMMAND[@]}")
        COMMAND+=(-c model_provider=openrouter
            -c 'model_providers.openrouter={name="OpenRouter",base_url="https://openrouter.ai/api/v1",wire_api="responses",auth={command="/bin/sh",args=["-c","cat \"$HOME/.config/openrouter/key\""]}}')
    fi
    COMMAND+=(-c model_reasoning_effort="$EFFORT" --skip-git-repo-check "$QUERY" --output-last-message "$TMP_OUT")
    EXIT_CODE=0
    _timeout "$REMAINING" "${COMMAND[@]}" </dev/null 2>"$TMP_ERR" || EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 0 && -s "$TMP_OUT" ]]; then
        break
    fi
    if [[ "$HARNESS" == 1 ]]; then
        [[ $EXIT_CODE -eq 0 ]] && EXIT_CODE=1
        if [[ $EXIT_CODE -eq 124 ]]; then
            FAILURE_STATUS="STATUS: FAILED — DeepSeek harness timed out after ${TIMEOUT}s (end-to-end deadline)"
        else
            FAILURE_STATUS="STATUS: FAILED — DeepSeek harness exited $EXIT_CODE (see $ERR_LOG)"
        fi
        break
    fi
    # An empty successful response is a generic failure, never a usage limit.
    SCOPE=""
    if [[ $EXIT_CODE -ne 0 && $EXIT_CODE -ne 124 ]]; then
        SCOPE="$(_usage_limit "$TMP_ERR")"
    fi
    if [[ "$SCOPE" == model ]]; then
        EXIT_CODE=75
        FAILURE_STATUS="STATUS: FAILED — usage-limit (model): Codex usage limit reached for model '$MODEL'. Switch Codex model per /delegate."
        break
    elif [[ "$SCOPE" == account ]]; then
        EXIT_CODE=75
        if [[ "$CREDIT_FALLBACK" == off ]]; then
            FAILURE_STATUS="STATUS: FAILED — usage-limit (account): Codex credit exhausted (model '$MODEL'). Fall back per /delegate (--credit-fallback)."
            break
        elif ! _no_tool_calls "$TMP_ERR"; then
            FAILURE_STATUS="STATUS: FAILED — usage-limit (account, mid-run): Codex credit ran out after tool calls; partial work may exist in $PWD. Re-dispatch in a fresh worktree with CODEX_MODEL=$DEEPSEEK_MODEL."
            break
        fi
        cp "$TMP_ERR" "${OUTPUT%.md}.codex.err"
        : > "$TMP_ERR"
        FALLBACK="$CREDIT_FALLBACK"
        MODEL="$DEEPSEEK_MODEL"
        EFFORT=max
        echo "[delegate/codex] Codex credit exhausted — falling back to DeepSeek ($FALLBACK)..." >&2
        : > "$TMP_OUT"
        if [[ "$FALLBACK" == deepseek-api ]]; then
            PROVIDER=unknown
            _run_api
            break
        fi
        HARNESS=1
        MODEL="$DEEPSEEK_HARNESS_MODEL"
        continue
    fi
    [[ $EXIT_CODE -eq 0 ]] && EXIT_CODE=1
    if [[ $EXIT_CODE -ne 124 ]] && _is_capacity "$TMP_ERR" \
        && (( ATTEMPT < CAPACITY_RETRIES )) \
        && (( DEADLINE - SECONDS > CAPACITY_BACKOFF + 120 )); then
        ATTEMPT=$((ATTEMPT + 1))
        echo "[delegate/codex] Model '$MODEL' at capacity — retrying (${ATTEMPT}/${CAPACITY_RETRIES}) after ${CAPACITY_BACKOFF}s..." >&2
        ATTEMPT_SESSIONS+=("$(_session_id "$TMP_ERR")")
        rm -f "$TMP_OUT" "$TMP_ERR"
        TMP_OUT="$(mktemp)"
        TMP_ERR="$(mktemp)"
        sleep "$CAPACITY_BACKOFF"
        continue
    fi
    break
done

# Stage beside OUTPUT so the final rename stays atomic across filesystem mounts.
if [[ -n "$KEY_ERR" ]]; then
    # Prepend key-check diagnostics only at publication; attempt classification
    # continues to read Codex stderr alone.
    cat "$TMP_ERR" >> "$KEY_ERR"
    mv -f "$KEY_ERR" "$ERR_LOG"
else
    mv -f "$TMP_ERR" "$ERR_LOG"
fi
FINAL="$(mktemp "${OUTPUT}.tmp.XXXXXX")"
{
    if [[ -n "$FALLBACK" ]]; then
        echo "<!-- ask_codex.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE | model=$MODEL | effort=max | fallback=codex-credit:$FALLBACK requested=$REQUESTED_MODEL/$REQUESTED_EFFORT${PROVIDER:+ provider=$PROVIDER} -->"
    else
        echo "<!-- ask_codex.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE | model=$MODEL | effort=$EFFORT -->"
    fi
    echo ""
    if [[ -n "$FAILURE_STATUS" ]]; then
        echo "$FAILURE_STATUS"
        echo ""
    elif [[ -z "$FALLBACK" && $EXIT_CODE -eq 124 ]]; then
        echo "STATUS: FAILED — Codex timed out after ${TIMEOUT}s (end-to-end deadline)"
        echo ""
    elif [[ -z "$FALLBACK" && $EXIT_CODE -ne 0 ]] && _is_capacity "$ERR_LOG"; then
        echo "STATUS: FAILED — capacity: model '$MODEL' at capacity (server). Retry or fall back per /delegate."
        echo ""
    elif [[ -z "$FALLBACK" && $EXIT_CODE -ne 0 ]] && grep -qi "requires a newer version of Codex" "$ERR_LOG" 2>/dev/null; then
        # Server-side version gate applies to any newer model on an older CLI.
        echo "STATUS: FAILED — Codex CLI too old for model '$MODEL' (server gate). Upgrade: npm install -g @openai/codex@latest"
        echo ""
    elif [[ -z "$FALLBACK" && $EXIT_CODE -ne 0 ]]; then
        echo "STATUS: FAILED — Codex exited $EXIT_CODE (see $ERR_LOG)"
        echo ""
    fi
    cat "$TMP_OUT" 2>/dev/null || true
} > "$FINAL"
mv -f "$FINAL" "$OUTPUT"
rm -f "$TMP_OUT"

if [[ $EXIT_CODE -eq 0 ]]; then
    # The CLI resolved a model without metadata: surface potential version skew.
    if grep -q "Model metadata for" "$ERR_LOG" 2>/dev/null; then
        echo "[delegate/codex] WARN: $(grep 'Model metadata for' "$ERR_LOG" | head -1) — update the Codex CLI" >&2
    fi
    RESULT_SIZE=$(wc -c < "$OUTPUT")
    echo "[delegate/codex] Done. Result: $RESULT_SIZE bytes → $OUTPUT" >&2
else
    if [[ -n "$FAILURE_STATUS" ]]; then
        echo "$FAILURE_STATUS" >&2
    elif [[ "$FALLBACK" == deepseek-api ]]; then
        echo "ERROR: DeepSeek API exited with code $EXIT_CODE (see $ERR_LOG)" >&2
    elif [[ $EXIT_CODE -eq 124 ]]; then
        echo "ERROR: Codex timed out after ${TIMEOUT}s (end-to-end deadline)" >&2
    elif [[ -z "$FALLBACK" ]] && _is_capacity "$ERR_LOG"; then
        echo "ERROR: model '$MODEL' at capacity after $((ATTEMPT + 1)) attempt(s) — fall back per /delegate (see $ERR_LOG)" >&2
    elif [[ -z "$FALLBACK" ]] && grep -qi "requires a newer version of Codex" "$ERR_LOG" 2>/dev/null; then
        echo "ERROR: Codex CLI too old for model '$MODEL' (server gate). Upgrade: npm install -g @openai/codex@latest" >&2
    else
        echo "ERROR: Codex exited with code $EXIT_CODE (see $ERR_LOG)" >&2
    fi
    exit $EXIT_CODE
fi

echo "$OUTPUT"
