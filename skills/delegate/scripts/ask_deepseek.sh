#!/bin/bash
# ask_deepseek.sh — Explicit optional OpenRouter dispatch with key-cap, provider, preset and provenance checks.
# See ../references/legs.md for modes, dependencies, costs and limitations.
set -euo pipefail
umask 077

readonly BASE_URL="https://openrouter.ai/api/v1"
readonly DEFAULT_MODEL="deepseek/deepseek-v4.1-flash-1017"
readonly ALLOWLIST="fireworks together parasail coreweave digitalocean"
readonly CN_BLOCKLIST="deepseek baidu alibaba siliconflow streamlake novita tencent moonshot zhipu minimax"
readonly EFFORT_ENUM="low,medium,high,xhigh,max"
readonly DEFAULT_ZDR="0"
readonly CEILING_USD="10"
readonly WRAPPER_VERSION="ask_deepseek.sh/v1.2-v4.1-flash-1017-2026-11-04"
# Only the key preflight is serialized. A dead lock older than this can be
# reclaimed; a live PID is never reclaimed, even when old.
readonly LOCK_STALE_SECONDS="1200"
readonly LOCK_WAIT_SECONDS="30"

MODEL="${DEEPSEEK_DELEGATE_MODEL:-$DEFAULT_MODEL}"
EFFORT="max"
TIMEOUT="${DEEPSEEK_DELEGATE_TIMEOUT:-570}"
MAX_TOKENS="${DEEPSEEK_DELEGATE_MAX_TOKENS:-65536}"
ZDR="${DEEPSEEK_DELEGATE_ZDR:-$DEFAULT_ZDR}"
LEDGER="${DEEPSEEK_DELEGATE_LEDGER:-${HOME:-}/.claude/delegate-logs/deepseek_ledger.tsv}"
KEY_FILE="${DEEPSEEK_DELEGATE_KEY_FILE:-${HOME:-}/.config/openrouter/key}"
ACTIVE_ALLOWLIST="$ALLOWLIST"

OUTPUT=""
ERR_LOG=""
RAW_JSON=""
RESOLVED_MODEL=""
RESOLVED_PROVIDER=""
PROMPT_TOKENS="0"
COMPLETION_TOKENS="0"
REASONING_TOKENS="0"
RESPONSE_COST="NA"
GENERATION_ID="NA"
FAILURE_CLASS=""
FAILURE_LINE=""
KEY_WARNING=""
KEY_CHECK_ONLY="0"
PRESET_FINGERPRINT_ONLY="0"
KEY_ERR_LOG=""

CURL_PID=""
CURL_CONFIG=""
PAYLOAD_FILE=""
CURL_CODE_FILE=""
KEY_BODY=""
BODY_TXT=""
SELFTEST_DIR=""
LOCK_DIR=""
LOCK_HELD="0"
LEDGER_READY="0"
LEDGER_OK_LOGGED="0"

# Portable realpath -m: the caller creates the parent before using this helper.
_realpath_m() {
    local target="$1"
    local dir
    dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd || echo "$(dirname "$target")")"
    echo "$dir/$(basename "$target")"
}

_sha256_stdin() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 | sed 's/^.*= //'
    else
        return 1
    fi
}

_fingerprint() {
    # Bind EVERY baked policy constant: a CN-blocklist or ceiling change must
    # change the fingerprint just like an allowlist change.
    printf '%s' "${DEFAULT_MODEL}|${ALLOWLIST}|${CN_BLOCKLIST}|${EFFORT_ENUM}|${DEFAULT_ZDR}|${CEILING_USD}|${WRAPPER_VERSION}" | _sha256_stdin
}

_preset_fingerprint() {
    # Hash the full canonical jq output, including its trailing newline.
    jq -S -c '.data.designated_version.config' "$1" | _sha256_stdin
}

_file_uid() {
    stat -f '%u' "$1" 2>/dev/null || stat -c '%u' "$1" 2>/dev/null || true
}

_file_mode() {
    stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null || true
}

_preset_marker_matches() {
    local marker="${HOME:-}/.config/openrouter/deepseek-codex.verified" mode reason
    if [[ -L "$marker" ]]; then
        reason="symlink"
    elif [[ ! -e "$marker" ]]; then
        reason="missing"
    elif [[ ! -f "$marker" ]]; then
        reason="not a regular file"
    elif [[ "$(_file_uid "$marker")" != "$(id -u)" ]]; then
        reason="wrong owner"
    else
        mode="$(_file_mode "$marker")"
        if ! [[ "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & ~ 0600) != 0 )); then
            reason="mode $mode exceeds 0600"
        elif [[ ! -s "$marker" ]]; then
            reason="empty"
        # Match bytes directly so shell substitution cannot discard a NUL or CR.
        elif ! { LC_ALL=C head -n 1 "$marker" 2>/dev/null | LC_ALL=C grep -aqxF "$1"; }; then
            reason="fingerprint mismatch (stale or malformed)"
        else
            return 0
        fi
    fi
    printf '[delegate/deepseek] marker refused: %s\n' "$reason" >&2
    return 1
}

_path_mtime() {
    stat -f '%m' "$1" 2>/dev/null || stat -c '%Y' "$1" 2>/dev/null || true
}

_cleanup() {
    local rc="$1"
    trap - EXIT INT TERM HUP

    if [[ -n "$CURL_PID" ]]; then
        if kill -0 "$CURL_PID" 2>/dev/null; then
            kill "$CURL_PID" 2>/dev/null || true
        fi
        wait "$CURL_PID" 2>/dev/null || true
        CURL_PID=""
    fi

    rm -f "${CURL_CONFIG:-}" "${PAYLOAD_FILE:-}" "${CURL_CODE_FILE:-}" \
        "${KEY_BODY:-}" "${BODY_TXT:-}" "${KEY_ERR_LOG:-}" 2>/dev/null || true
    if [[ -n "$SELFTEST_DIR" && -d "$SELFTEST_DIR" && ! -L "$SELFTEST_DIR" ]]; then
        rm -rf "$SELFTEST_DIR" 2>/dev/null || true
    fi

    _release_lock 2>/dev/null || true
    exit "$rc"
}

# One cleanup path handles normal exit and signals; signal-specific codes avoid a
# trapped signal accidentally becoming success.
trap '_cleanup "$?"' EXIT
trap '_cleanup 130' INT
trap '_cleanup 143' TERM
trap '_cleanup 129' HUP

_header_field() {
    printf '%s' "$1" | tr '\r\n|<>' '      ' | cut -c1-240
}

_write_header_and_body() {
    local tag="$1"
    local failure_line="${2:-}"
    local body_file="${3:-}"
    local tmp resolved

    [[ -n "$OUTPUT" ]] || return 1
    if ! tmp="$(mktemp "${OUTPUT}.tmp.XXXXXX")"; then
        return 1
    fi
    resolved="$(_header_field "${RESOLVED_MODEL:-unknown}+${RESOLVED_PROVIDER:-unknown}")"
    if ! {
        # Keep the resolved model/provider field stable for callers and offline tests.
        echo "<!-- ask_deepseek.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$(_header_field "$tag") | backend=openrouter | model=$(_header_field "$MODEL") | resolved=${resolved} | effort=$(_header_field "$EFFORT") | mode=voter -->"
        echo ""
        if [[ -n "$failure_line" ]]; then
            echo "$failure_line"
            echo ""
        fi
        if [[ -n "$body_file" && -f "$body_file" ]]; then
            cat "$body_file"
        fi
    } > "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if ! chmod 600 "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if ! mv "$tmp" "$OUTPUT"; then
        rm -f "$tmp"
        return 1
    fi
    return 0
}

_tsv_field() {
    printf '%s' "$1" | tr '\t\r\n' '   '
}

_prepare_ledger() {
    local ledger_dir header_tmp
    ledger_dir="$(dirname "$LEDGER")"
    if ! mkdir -p "$ledger_dir"; then
        return 1
    fi
    if [[ -L "$LEDGER" || ( -e "$LEDGER" && ! -f "$LEDGER" ) ]]; then
        return 1
    fi
    if [[ ! -e "$LEDGER" ]]; then
        # Publish a complete header atomically; a racing writer must never
        # truncate the ledger or append a row ahead of its header.
        header_tmp="$(mktemp "${LEDGER}.header.XXXXXX")" || return 1
        if ! printf 'utc_ts\trequested_model\tresolved_model\tresolved_provider\trequested_effort\tprompt_tokens\tcompletion_tokens\treasoning_tokens\tcost\tgeneration_id\tfailure_class\n' > "$header_tmp"; then
            rm -f "$header_tmp"
            return 1
        fi
        if ! ln "$header_tmp" "$LEDGER" 2>/dev/null; then
            if [[ ! -f "$LEDGER" || -L "$LEDGER" ]]; then
                rm -f "$header_tmp"
                return 1
            fi
        fi
        rm -f "$header_tmp"
    fi
    LEDGER_READY="1"
    return 0
}

_append_ledger() {
    local failure_class="$1"
    local utc_ts requested resolved_model resolved_provider requested_effort
    local prompt_tokens completion_tokens reasoning_tokens cost generation_id failure row

    [[ "$LEDGER_READY" == "1" ]] || return 1
    utc_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    requested="$(_tsv_field "$MODEL")"
    resolved_model="$(_tsv_field "${RESOLVED_MODEL:-NA}")"
    resolved_provider="$(_tsv_field "${RESOLVED_PROVIDER:-NA}")"
    requested_effort="$(_tsv_field "$EFFORT")"
    failure="$(_tsv_field "$failure_class")"

    if [[ "$failure_class" == "OK" ]]; then
        prompt_tokens="$PROMPT_TOKENS"
        completion_tokens="$COMPLETION_TOKENS"
        reasoning_tokens="$REASONING_TOKENS"
        cost="$(_tsv_field "$RESPONSE_COST")"
        generation_id="$(_tsv_field "$GENERATION_ID")"
    else
        prompt_tokens="0"
        completion_tokens="0"
        reasoning_tokens="0"
        cost="NA"
        generation_id="NA"
    fi

    printf -v row '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$utc_ts" "$requested" "$resolved_model" "$resolved_provider" \
        "$requested_effort" "$prompt_tokens" "$completion_tokens" \
        "$reasoning_tokens" "$cost" "$generation_id" "$failure"
    # O_APPEND plus one syswrite keeps concurrent rows intact. A short write is
    # an error, never a second write that could interleave with another row.
    perl -MFcntl=O_WRONLY,O_APPEND -e '
        sysopen(my $fh, $ARGV[0], O_WRONLY | O_APPEND) or die "ledger open: $!\n";
        my $row = $ARGV[1];
        my $written = syswrite($fh, $row, length($row));
        defined($written) && $written == length($row) or die "ledger short write\n";
        close($fh) or die "ledger close: $!\n";
    ' "$LEDGER" "$row"
}

_fail() {
    local tag="$1"
    local failure_class="$2"
    local line="$3"

    if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
        printf '%s\n' "$line"
        exit 1
    fi

    # One ledger row per dispatch: if the OK row is already recorded, a late
    # envelope-write failure must not append a second row (ledger integrity).
    if [[ "$LEDGER_READY" == "1" && "$LEDGER_OK_LOGGED" == "0" ]]; then
        if ! _append_ledger "$failure_class"; then
            echo "[delegate/deepseek] WARN: could not append failure to ledger: $LEDGER" >&2
        fi
    fi
    if ! _write_header_and_body "$tag" "$line"; then
        echo "ERROR: $line; additionally could not write failure envelope to ${OUTPUT:-<unset>}" >&2
        exit 1
    fi
    echo "ERROR: $line" >&2
    echo "$OUTPUT"
    exit 1
}

_body_error_text() {
    local body="$1"
    if jq -e . "$body" >/dev/null 2>&1; then
        jq -r '
            [
              .error.message?,
              .error.metadata.raw?,
              .error.metadata.message?,
              .error.metadata?,
              .choices[0].error.message?,
              .choices[0].error.metadata.raw?,
              .choices[0].error.metadata.message?,
              .choices[0].error.metadata?
            ]
            | map(select(. != null) | if type == "string" then . else tostring end)
            | join(" ")
        ' "$body" 2>/dev/null | tr '\r\n' '  ' | cut -c1-500
    else
        head -c 500 "$body" 2>/dev/null | tr '\r\n' '  '
    fi
}

# Populate FAILURE_CLASS and FAILURE_LINE from HTTP/curl state plus API metadata.
_classify_failure() {
    local http_code="$1"
    local curl_exit="$2"
    local body="$3"
    local diag allowlist_csv
    diag="$(_body_error_text "$body")"
    allowlist_csv="${ACTIVE_ALLOWLIST// /,}"

    if [[ "$curl_exit" != "0" || "$http_code" == "000" ]]; then
        FAILURE_CLASS="transport"
        if [[ "$curl_exit" == "28" ]]; then
            FAILURE_LINE="STATUS: FAILED — OpenRouter timed out after ${TIMEOUT}s"
        else
            FAILURE_LINE="STATUS: FAILED — OpenRouter unreachable or timed out after ${TIMEOUT}s (curl exit ${curl_exit})"
        fi
        return
    fi
    if grep -qiE 'no (allowed|available) providers|no providers? (allowed|available)' <<< "$diag"; then
        FAILURE_CLASS="routing"
        FAILURE_LINE="STATUS: FAILED — routing: no allowed/available OpenRouter provider inside allowlist (${allowlist_csv})"
        return
    fi
    case "$http_code" in
        401)
            FAILURE_CLASS="auth"
            FAILURE_LINE="STATUS: FAILED — auth: OpenRouter rejected the API key (HTTP 401)"
            ;;
        402)
            FAILURE_CLASS="credits"
            FAILURE_LINE="STATUS: FAILED — credits: OpenRouter credits exhausted or payment required (HTTP 402)"
            ;;
        403)
            FAILURE_CLASS="moderation-region"
            FAILURE_LINE="STATUS: FAILED — moderation/region: OpenRouter refused this request (HTTP 403)"
            ;;
        404)
            FAILURE_CLASS="bad-slug"
            FAILURE_LINE="STATUS: FAILED — model slug '${MODEL}' not found on OpenRouter (HTTP 404)"
            ;;
        429)
            FAILURE_CLASS="rate-limit"
            FAILURE_LINE="STATUS: FAILED — OpenRouter rate limit reached (HTTP 429)"
            ;;
        5??)
            FAILURE_CLASS="provider"
            FAILURE_LINE="STATUS: FAILED — OpenRouter/provider failure (HTTP ${http_code})"
            ;;
        *)
            FAILURE_CLASS="api-error"
            if [[ -n "$diag" ]]; then
                FAILURE_LINE="STATUS: FAILED — OpenRouter API error (HTTP ${http_code}): ${diag}"
            else
                FAILURE_LINE="STATUS: FAILED — OpenRouter API error (HTTP ${http_code}) with no diagnostics"
            fi
            ;;
    esac
}

_provider_is_allowed() {
    local provider lower allowed
    provider="$1"
    lower="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
    for allowed in $ACTIVE_ALLOWLIST; do
        if [[ "$lower" == "$allowed" ]]; then
            return 0
        fi
    done
    return 1
}

_extract_response_fields() {
    local body="$1"
    RESOLVED_MODEL="$(jq -r 'if (.model | type) == "string" then .model else "" end' "$body")"
    RESOLVED_PROVIDER="$(jq -r 'if (.provider | type) == "string" then .provider else "" end' "$body")"
    PROMPT_TOKENS="$(jq -r 'if (.usage.prompt_tokens | type) == "number" then .usage.prompt_tokens else 0 end' "$body")"
    COMPLETION_TOKENS="$(jq -r 'if (.usage.completion_tokens | type) == "number" then .usage.completion_tokens else 0 end' "$body")"
    REASONING_TOKENS="$(jq -r 'if (.usage.completion_tokens_details.reasoning_tokens | type) == "number" then .usage.completion_tokens_details.reasoning_tokens else 0 end' "$body")"
    RESPONSE_COST="$(jq -r 'if (.usage.cost | type) == "number" then (.usage.cost | tostring) else "NA" end' "$body")"
    GENERATION_ID="$(jq -r 'if (.id | type) == "string" and (.id | length) > 0 then .id else "NA" end' "$body")"
}

# Strict acceptance order: transport/HTTP, JSON, top-level error, choice error,
# finish reason, content, then the resolved-provider runtime invariant.
_assess_response() {
    local http_code="$1"
    local curl_exit="$2"
    local body="$3"
    local finish provider_display

    FAILURE_CLASS=""
    FAILURE_LINE=""
    RESOLVED_MODEL=""
    RESOLVED_PROVIDER=""

    if [[ "$curl_exit" != "0" || "$http_code" == "000" ]]; then
        _classify_failure "$http_code" "$curl_exit" "$body"
        return 1
    fi
    if [[ "$http_code" != "200" ]]; then
        _classify_failure "$http_code" "$curl_exit" "$body"
        return 1
    fi
    if ! jq -e . "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="non-json"
        FAILURE_LINE="STATUS: FAILED — OpenRouter returned HTTP 200 with non-JSON body"
        return 1
    fi
    _extract_response_fields "$body"
    if jq -e '(.error? != null)' "$body" >/dev/null 2>&1; then
        _classify_failure "$http_code" "$curl_exit" "$body"
        return 1
    fi
    if jq -e '(.choices[0].error? != null)' "$body" >/dev/null 2>&1; then
        _classify_failure "$http_code" "$curl_exit" "$body"
        return 1
    fi
    finish="$(jq -r '.choices[0].finish_reason // ""' "$body")"
    if [[ "$finish" != "stop" ]]; then
        FAILURE_CLASS="finish-${finish:-missing}"
        FAILURE_LINE="STATUS: FAILED — incomplete response: finish_reason='${finish:-missing}' (increase DEEPSEEK_DELEGATE_MAX_TOKENS or simplify the prompt)"
        return 1
    fi
    if ! jq -e '(.choices[0].message.content | type) == "string" and (.choices[0].message.content | length) > 0' "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="empty-content"
        FAILURE_LINE="STATUS: FAILED — OpenRouter returned an empty or non-string response body"
        return 1
    fi
    if [[ -z "$RESOLVED_PROVIDER" ]]; then
        # Fail closed: a response that omits the serving provider defeats the
        # post-route allowlist invariant (review G1) — never accept it.
        FAILURE_CLASS="provider-missing"
        FAILURE_LINE="STATUS: FAILED — CONTRACT-DRIFT: response omitted the serving provider (allowlist invariant unverifiable)"
        return 1
    fi
    if ! _provider_is_allowed "$RESOLVED_PROVIDER"; then
        provider_display="$(_header_field "$RESOLVED_PROVIDER")"
        FAILURE_CLASS="contract-drift"
        FAILURE_LINE="STATUS: FAILED — CONTRACT-DRIFT: routed to '${provider_display}' outside allowlist"
        return 1
    fi
    return 0
}

_assess_key_response() {
    local http_code="$1"
    local curl_exit="$2"
    local body="$3"
    local limit usage

    KEY_WARNING=""
    FAILURE_CLASS=""
    FAILURE_LINE=""
    if [[ "$curl_exit" != "0" || "$http_code" == "000" ]]; then
        FAILURE_CLASS="ceiling-transport"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key endpoint unreachable or timed out"
        return 1
    fi
    if [[ "$http_code" != "200" ]]; then
        _classify_failure "$http_code" "$curl_exit" "$body"
        FAILURE_CLASS="ceiling-${FAILURE_CLASS}"
        FAILURE_LINE="STATUS: FAILED — ceiling preflight: ${FAILURE_LINE#STATUS: FAILED — }"
        return 1
    fi
    if ! jq -e . "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="ceiling-non-json"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter /key returned non-JSON"
        return 1
    fi
    if ! jq -e '(.data.limit | type) == "number"' "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="ceiling-null-limit"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key limit is missing or null"
        return 1
    fi
    if ! jq -e '(.data.usage | type) == "number"' "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="ceiling-malformed-usage"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key usage is missing or non-numeric"
        return 1
    fi
    if ! jq -e '(.data.limit_remaining == null) or ((.data.limit_remaining | type) == "number")' "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="ceiling-malformed-remaining"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key remaining balance is malformed"
        return 1
    fi
    if jq -e --argjson ceiling "$CEILING_USD" '.data.limit > $ceiling' "$body" >/dev/null 2>&1; then
        limit="$(jq -r '.data.limit' "$body")"
        FAILURE_CLASS="ceiling-too-high"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key limit ${limit} exceeds USD ${CEILING_USD}"
        return 1
    fi
    if jq -e '((.data.limit_remaining | type) == "number" and .data.limit_remaining <= 0) or (.data.usage >= .data.limit)' "$body" >/dev/null 2>&1; then
        FAILURE_CLASS="ceiling-exhausted"
        FAILURE_LINE="STATUS: FAILED — ceiling: OpenRouter key limit reached"
        return 1
    fi
    limit="$(jq -r '.data.limit' "$body")"
    usage="$(jq -r '.data.usage' "$body")"
    if jq -e '(.data.limit > 0) and (.data.usage >= (0.8 * .data.limit))' "$body" >/dev/null 2>&1; then
        KEY_WARNING="[delegate/deepseek] WARN: OpenRouter key usage is ${usage} of ${limit} (at least 80% of limit)"
    fi
    return 0
}

_trim_spaces() {
    printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

_word_in_list() {
    local needle="$1"
    local list="$2"
    local item
    for item in $list; do
        [[ "$needle" == "$item" ]] && return 0
    done
    return 1
}

_validate_providers() {
    local configured raw provider lower active=""
    local -a parts=()

    if [[ "${DEEPSEEK_DELEGATE_PROVIDERS+x}" != "x" ]]; then
        ACTIVE_ALLOWLIST="$ALLOWLIST"
        return 0
    fi
    configured="$DEEPSEEK_DELEGATE_PROVIDERS"
    if [[ -z "$configured" ]]; then
        FAILURE_CLASS="validation-providers-empty"
        FAILURE_LINE="STATUS: FAILED — validation: DEEPSEEK_DELEGATE_PROVIDERS is explicitly empty"
        return 1
    fi

    # Split on commas with an IFS scoped to the read alone — a function-wide
    # IFS=',' would leak into _word_in_list and collapse its space-separated
    # lists to a single word (over-rejecting every legitimate subset).
    IFS=',' read -r -a parts <<< "$configured"
    for raw in "${parts[@]}"; do
        provider="$(_trim_spaces "$raw")"
        if [[ -z "$provider" ]]; then
            FAILURE_CLASS="validation-provider-member"
            FAILURE_LINE="STATUS: FAILED — validation: DEEPSEEK_DELEGATE_PROVIDERS contains an empty member"
            return 1
        fi
        lower="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
        if _word_in_list "$lower" "$CN_BLOCKLIST"; then
            FAILURE_CLASS="data-governance"
            FAILURE_LINE="STATUS: FAILED — data-governance doctrine forbids provider '${provider}' (CN provider blocklist)"
            return 1
        fi
        if ! _word_in_list "$lower" "$ALLOWLIST"; then
            FAILURE_CLASS="validation-provider-unknown"
            FAILURE_LINE="STATUS: FAILED — validation: provider '${provider}' is outside the baked allowlist (${ALLOWLIST// /,})"
            return 1
        fi
        if ! _word_in_list "$lower" "$active"; then
            active="${active:+${active} }${lower}"
        fi
    done

    # A trailing comma does not produce a word in Bash splitting, so reject it
    # explicitly rather than silently altering the caller's set.
    if [[ "$configured" == ,* || "$configured" == *, || "$configured" == *,,* || -z "$active" ]]; then
        FAILURE_CLASS="validation-provider-member"
        FAILURE_LINE="STATUS: FAILED — validation: DEEPSEEK_DELEGATE_PROVIDERS contains an empty member"
        return 1
    fi
    ACTIVE_ALLOWLIST="$active"
    return 0
}
_acquire_lock() {
    local log_dir waited now created age owner stale
    log_dir="${HOME}/.claude/delegate-logs"
    LOCK_DIR="${log_dir}/.deepseek.lock"
    if ! mkdir -p "$log_dir"; then
        FAILURE_CLASS="lock-directory"
        FAILURE_LINE="STATUS: FAILED — lock: cannot create OpenRouter ledger directory"
        return 1
    fi
    waited="0"
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        if [[ ! -d "$LOCK_DIR" || -L "$LOCK_DIR" ]]; then
            FAILURE_CLASS="lock-invalid"
            FAILURE_LINE="STATUS: FAILED — lock: ${LOCK_DIR} exists but is not a real directory"
            return 1
        fi
        owner=""
        created=""
        if [[ -f "$LOCK_DIR/pid" && ! -L "$LOCK_DIR/pid" ]]; then
            owner="$(head -1 "$LOCK_DIR/pid" 2>/dev/null || true)"
        fi
        if [[ -f "$LOCK_DIR/created" && ! -L "$LOCK_DIR/created" ]]; then
            created="$(head -1 "$LOCK_DIR/created" 2>/dev/null || true)"
        fi
        [[ "$created" =~ ^[0-9]+$ ]] || created="$(_path_mtime "$LOCK_DIR")"
        now="$(date +%s)"
        age="0"
        if [[ "$created" =~ ^[0-9]+$ && "$now" -ge "$created" ]]; then
            age=$((now - created))
        fi
        if [[ "$age" -ge "$LOCK_STALE_SECONDS" ]] && \
           { ! [[ "$owner" =~ ^[1-9][0-9]*$ ]] || ! kill -0 "$owner" 2>/dev/null; }; then
            stale="${LOCK_DIR}.stale.$$.$now"
            if mv "$LOCK_DIR" "$stale" 2>/dev/null; then
                rm -f "$stale/pid" "$stale/created" 2>/dev/null || true
                if ! rmdir "$stale" 2>/dev/null; then
                    echo "[delegate/deepseek] WARN: quarantined stale lock has unexpected contents: $stale" >&2
                fi
                continue
            fi
        fi
        if [[ "$waited" -ge "$LOCK_WAIT_SECONDS" ]]; then
            FAILURE_CLASS="lock-busy"
            FAILURE_LINE="STATUS: FAILED — lock: another DeepSeek/OpenRouter dispatch is active (waited ${LOCK_WAIT_SECONDS}s)"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if ! printf '%s\n' "$$" > "$LOCK_DIR/pid"; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
        FAILURE_CLASS="lock-owner-write"
        FAILURE_LINE="STATUS: FAILED — lock: could not record lock owner"
        return 1
    fi
    LOCK_HELD="1"
    if ! date +%s > "$LOCK_DIR/created"; then
        FAILURE_CLASS="lock-time-write"
        FAILURE_LINE="STATUS: FAILED — lock: could not record lock creation time"
        return 1
    fi
    return 0
}

_release_lock() {
    local owner=""
    [[ "$LOCK_HELD" == "1" ]] || return 0
    if [[ -n "$LOCK_DIR" && -d "$LOCK_DIR" && ! -L "$LOCK_DIR" &&
          -f "$LOCK_DIR/pid" && ! -L "$LOCK_DIR/pid" ]]; then
        owner="$(head -1 "$LOCK_DIR/pid" 2>/dev/null || true)"
        if [[ "$owner" == "$$" ]]; then
            rm -f "$LOCK_DIR/pid" "$LOCK_DIR/created" || return 1
            rmdir "$LOCK_DIR" || return 1
            LOCK_HELD="0"
            return 0
        fi
    fi
    return 1
}

_curl_request() {
    local method="$1"
    local url="$2"
    local response_file="$3"
    local data_file="${4:-}"

    if ! : > "$CURL_CODE_FILE"; then
        CURL_EXIT="1"
        CURL_HTTP_CODE="000"
        echo "could not reset curl HTTP-code temporary file" >> "$ERR_LOG" 2>/dev/null || true
        return 0
    fi
    (
        unset OPENROUTER_API_KEY http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
        if [[ "$method" == "GET" ]]; then
            exec curl -q --config "$CURL_CONFIG" -sS --max-time "$TIMEOUT" \
                -o "$response_file" -w '%{http_code}' "$url"
        else
            exec curl -q --config "$CURL_CONFIG" -sS --max-time "$TIMEOUT" \
                -o "$response_file" -w '%{http_code}' --data "@${data_file}" "$url"
        fi
    ) > "$CURL_CODE_FILE" 2>> "$ERR_LOG" &
    CURL_PID=$!
    CURL_EXIT="0"
    wait "$CURL_PID" || CURL_EXIT=$?
    CURL_PID=""
    CURL_HTTP_CODE="$(head -c 3 "$CURL_CODE_FILE" 2>/dev/null || true)"
    [[ "$CURL_HTTP_CODE" =~ ^[0-9][0-9][0-9]$ ]] || CURL_HTTP_CODE="000"
}

_selftest_pass() {
    echo "PASS: $1"
}

_selftest_fail() {
    echo "FAIL: $1"
    SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
}

# Independent digest implementation: preserve jq's newline, never use the
# production fingerprint helper to issue a test marker.
_selftest_preset_hash() {
    jq -S -c '.data.designated_version.config' "$1" |
        perl -MDigest::SHA=sha256_hex -0777 -ne 'print sha256_hex($_), "\n"'
}

# Run the actual entry points with a fake curl; no selftest can reach a host.
_selftest_modes() {
    local script test_dir test_home test_bin fixture expected rc name output
    local concurrent="0" key_only="1" first second
    local preset_fixture good_preset preset_filter key_exit="0" preset_http="200" preset_exit="0" expected_trace
    local override_env="" override_file="" override_warning expected_bearer="offline-selftest-key"
    local marker expected_hash readonly_mode marker_failure marker_reason marker_wrong_owner="0"
    script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    test_dir="$SELFTEST_DIR/modes"
    test_home="$test_dir/home"
    test_bin="$test_dir/bin"
    preset_fixture="$test_dir/presets.json"
    good_preset="$test_dir/preset_good.json"
    marker="$test_home/.config/openrouter/deepseek-codex.verified"
    mkdir -p "$test_bin" "$test_home/.config/openrouter" "$test_dir/tmp"
    jq -n --arg model "$DEFAULT_MODEL" --arg allowlist "$ALLOWLIST" '
        {data: {slug: "deepseek-codex", status: "active", designated_version: {config: {
            provider: {only: ($allowlist | split(" ")), data_collection: "deny"},
            model: $model, models: [$model], tools: [], plugins: []
        }}}}
    ' > "$good_preset"
    cp "$good_preset" "$preset_fixture"
    _selftest_preset_hash "$preset_fixture" > "$marker"
    chmod 600 "$marker"
    printf 'offline-selftest-key\n' > "$test_home/.config/openrouter/key"
    chmod 600 "$test_home/.config/openrouter/key"
    # Also inspect shell state immediately before POST, beyond the curl stub's
    # check that the lock directory is absent.
    sed '/^_curl_request POST /i\
[[ "$LOCK_HELD" == "0" && ! -e "$LOCK_DIR" ]] || exit 97
' "$script" > "$test_dir/wrapper.sh"
    cat > "$test_bin/curl" <<'SH'
#!/bin/bash
set -euo pipefail
[[ "${1:-}" == "-q" ]] || exit 90
[[ -z "${OPENROUTER_API_KEY:-}${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${all_proxy:-}" ]] || exit 91
config=""; dest=""; payload=""; url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) config="$2"; shift 2 ;;
        -o) dest="$2"; shift 2 ;;
        --data) payload="${2#@}"; shift 2 ;;
        -w|--max-time) shift 2 ;;
        https://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
mode="$(stat -f '%Lp' "$config" 2>/dev/null || stat -c '%a' "$config")"
[[ "$mode" == 600 ]] || exit 92
if grep -Fqx "header = \"Authorization: Bearer $DS_SELFTEST_EXPECT_BEARER\"" "$config"; then
    printf '1\n' > "$DS_SELFTEST_BEARER"
else
    printf '0\n' > "$DS_SELFTEST_BEARER"
    exit 93
fi
http=200
if [[ "$url" == https://openrouter.ai/api/v1/key ]]; then
    [[ -z "$payload" && -d "$HOME/.claude/delegate-logs/.deepseek.lock" ]] || exit 94
    printf 'GET\n' >> "$DS_SELFTEST_TRACE"
    printf '%s\n' "$config" > "$DS_SELFTEST_CONFIG"
    cp "$DS_SELFTEST_KEY_BODY" "$dest"
    [[ "$DS_SELFTEST_KEY_EXIT" == 0 ]] || exit "$DS_SELFTEST_KEY_EXIT"
elif [[ "$url" == https://openrouter.ai/api/v1/presets/deepseek-codex ]]; then
    [[ -z "$payload" && "$DS_SELFTEST_KEY_ONLY" == 1 && -d "$HOME/.claude/delegate-logs/.deepseek.lock" &&
       "$config" == "$(cat "$DS_SELFTEST_CONFIG")" ]] || exit 98
    printf 'PRESET_GET\n' >> "$DS_SELFTEST_TRACE"
    cp "$DS_SELFTEST_PRESET_BODY" "$dest"
    http="$DS_SELFTEST_PRESET_HTTP"
    [[ "$DS_SELFTEST_PRESET_EXIT" == 0 ]] || exit "$DS_SELFTEST_PRESET_EXIT"
elif [[ "$url" == https://openrouter.ai/api/v1/presets ]]; then
    printf 'LIST_REFUSED\n' >> "$DS_SELFTEST_TRACE"
    exit 99
elif [[ "$url" == https://openrouter.ai/api/v1/chat/completions ]]; then
    printf 'POST\n' >> "$DS_SELFTEST_TRACE"
    [[ "$DS_SELFTEST_KEY_ONLY" == 0 && ! -e "$HOME/.claude/delegate-logs/.deepseek.lock" ]] || exit 95
    jq -e '.model == "deepseek/deepseek-v4.1-flash-1017" and .max_tokens == 65536 and .reasoning.effort == "max" and .provider.data_collection == "deny" and (.provider.only | length) == 5' "$payload" >/dev/null
    if [[ "$DS_SELFTEST_CONCURRENT" == 1 ]]; then
        : > "$DS_SELFTEST_BARRIER/$$"
        for ((i=0; i<50; i++)); do
            peers=("$DS_SELFTEST_BARRIER"/*)
            [[ "${#peers[@]}" -ge 2 ]] && break
            sleep 0.1
        done
        [[ "${#peers[@]}" -ge 2 ]] || exit 96
    fi
    cp "$DS_SELFTEST_GOOD" "$dest"
else
    exit 99
fi
printf '%s' "$http"
SH
    cat > "$test_bin/mktemp" <<'SH'
#!/bin/bash
printf '%s\n' "$*" >> "$DS_SELFTEST_TEMPS"
exec "$DS_SELFTEST_MKTEMP" "$@"
SH
    cat > "$test_bin/stat" <<'SH'
#!/bin/bash
# Simulate foreign ownership only for the marker; the key keeps its real uid.
if [[ "$DS_SELFTEST_MARKER_WRONG_OWNER" == 1 && "${2:-}" == '%u' &&
      "${3:-}" == "$HOME/.config/openrouter/deepseek-codex.verified" ]]; then
    printf '%s\n' "$(( $(id -u) + 1 ))"
else
    exec "$DS_SELFTEST_STAT" "$@"
fi
SH
    chmod +x "$test_bin/curl" "$test_bin/mktemp" "$test_bin/stat"
    # Dynamic scope keeps the fixture and concurrency switches local to this run.
    _selftest_child() (
        cd "$test_dir"
        env -i PATH="$test_bin:$PATH" HOME="$test_home" TMPDIR="$test_dir/tmp" \
            DS_SELFTEST_MKTEMP="$(command -v mktemp)" DS_SELFTEST_TEMPS="$test_dir/temps" \
            DS_SELFTEST_KEY_BODY="$fixture" DS_SELFTEST_KEY_EXIT="$key_exit" DS_SELFTEST_TRACE="$test_dir/trace" \
            DS_SELFTEST_BEARER="$test_dir/bearer" DS_SELFTEST_EXPECT_BEARER="$expected_bearer" \
            DS_SELFTEST_CONFIG="$test_dir/config" DS_SELFTEST_PRESET_BODY="$preset_fixture" \
            DS_SELFTEST_PRESET_HTTP="$preset_http" DS_SELFTEST_PRESET_EXIT="$preset_exit" \
            DS_SELFTEST_STAT="$(command -v stat)" DS_SELFTEST_MARKER_WRONG_OWNER="$marker_wrong_owner" \
            DS_SELFTEST_GOOD="$SELFTEST_DIR/good.json" DS_SELFTEST_KEY_ONLY="$key_only" \
            DS_SELFTEST_CONCURRENT="$concurrent" DS_SELFTEST_BARRIER="$test_dir/barrier" \
            http_proxy=offline https_proxy=offline HTTP_PROXY=offline HTTPS_PROXY=offline ALL_PROXY=offline all_proxy=offline \
            OPENROUTER_API_KEY="$override_env" DEEPSEEK_DELEGATE_KEY_FILE="$override_file" \
            DEEPSEEK_DELEGATE_LEDGER="$test_dir/ledger.tsv" \
            bash "$test_dir/wrapper.sh" "$@"
    )
    for name in key_ok key_null key_high key_exhausted key_warn; do
        fixture="$SELFTEST_DIR/$name.json"
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rc=0
        _selftest_child --key-check > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        output="$(cat "$test_dir/stdout")"
        expected="STATUS: FAILED — "
        expected_trace="GET"
        case "$name" in
            key_ok|key_warn)
                expected="STATUS: OK — key-check: key capped at USD 10 or less with credit remaining; preset deepseek-codex active with verified routing policy"
                expected_trace=$'GET\nPRESET_GET'
                ;;
            key_null) expected="STATUS: FAILED — ceiling: OpenRouter key limit is missing or null" ;;
            key_high) expected="STATUS: FAILED — ceiling: OpenRouter key limit 100 exceeds USD 10" ;;
            key_exhausted) expected="STATUS: FAILED — ceiling: OpenRouter key limit reached" ;;
        esac
        if [[ "$output" == "$expected" && "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 ]] &&
           { [[ "$expected" == "STATUS: OK"* && "$rc" == 0 ]] || [[ "$expected" == "STATUS: FAILED"* && "$rc" != 0 ]]; } &&
           [[ "$(cat "$test_dir/trace")" == "$expected_trace" && ! -e "$test_dir/ledger.tsv" &&
              ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
           ! grep -q 'ask_deepseek_payload' "$test_dir/temps" &&
           { [[ "$name" != key_warn ]] || grep -q 'WARN:.*80%' "$test_dir/stderr"; }; then
            _selftest_pass "--key-check $name: one status line, no completion/payload/ledger, lock cleaned"
        else
            _selftest_fail "--key-check $name: status/no-completion contract (rc=$rc)"
        fi
    done

    fixture="$SELFTEST_DIR/key_ok.json"
    override_warning='[delegate/deepseek] WARN: --key-check ignores OPENROUTER_API_KEY and DEEPSEEK_DELEGATE_KEY_FILE; checking ~/.config/openrouter/key (the Codex-CLI credential)'
    for name in both empty; do
        override_env=""
        override_file=""
        if [[ "$name" == both ]]; then
            override_env="wrong-env-key"
            override_file="$test_dir/override-key"
            printf 'wrong-file-key\n' > "$override_file"
            chmod 600 "$override_file"
        fi
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rm -f "$test_dir/bearer"
        rc=0
        _selftest_child --key-check > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if [[ "$rc" == 0 && "$(cat "$test_dir/stdout")" == "STATUS: OK — key-check: key capped at USD 10 or less with credit remaining; preset deepseek-codex active with verified routing policy" &&
              "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 && "$(cat "$test_dir/bearer")" == 1 &&
              "$(cat "$test_dir/trace")" == $'GET\nPRESET_GET' && ! -e "$test_dir/ledger.tsv" &&
              ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
           ! grep -q 'ask_deepseek_payload' "$test_dir/temps" &&
           { { [[ "$name" == both ]] && [[ "$(grep -Fxc -- "$override_warning" "$test_dir/stderr")" == 1 ]]; } ||
             { [[ "$name" == empty ]] && ! grep -Fq -- 'WARN: --key-check ignores' "$test_dir/stderr"; }; } &&
           grep -Fq 'Backend=openrouter mode=key-check ' "$test_dir/stderr"; then
            _selftest_pass "--key-check $name overrides: default-file bearer, WARN only when non-empty"
        else
            _selftest_fail "--key-check $name overrides: bearer/WARN contract (rc=$rc)"
        fi
    done
    override_env=""
    override_file=""

    fixture="$SELFTEST_DIR/key_ok.json"
    for name in active display_names normalized_names optional_nulls optional_missing models_empty \
        disabled archived absent wrong_slug missing_status http500 non_json transport \
        version_null config_missing config_null config_array provider_missing provider_array \
        only_missing only_empty only_sail only_deepseek only_string only_nonstring \
        collection_missing collection_allow models_other model_other models_string model_false \
        tools_nonempty plugins_nonempty tools_object plugins_object; do
        preset_http="200"
        preset_exit="0"
        preset_filter='.'
        expected="STATUS: FAILED — preset: deepseek-codex routing policy not verified (needs provider.only within the allowlist, data_collection deny, DeepSeek-only model, no tools; Codex-CLI DeepSeek path refused)"
        case "$name" in
            active|display_names|normalized_names|optional_nulls|optional_missing|models_empty)
                expected="STATUS: OK — key-check: key capped at USD 10 or less with credit remaining; preset deepseek-codex active with verified routing policy" ;;
            disabled|archived|absent|wrong_slug|missing_status|http500|non_json|transport)
                expected="STATUS: FAILED — preset: deepseek-codex missing or not active (Codex-CLI DeepSeek path refused)" ;;
        esac
        case "$name" in
            display_names) preset_filter='.data.designated_version.config.provider.only = ["Fireworks","Together","DigitalOcean"]' ;;
            normalized_names) preset_filter='.data.designated_version.config.provider.only = ["Fireworks/turbo","TO-GETHER","Digital Ocean"]' ;;
            optional_nulls) preset_filter='.data.designated_version.config |= (.model = null | .models = null | .tools = null | .plugins = null)' ;;
            optional_missing) preset_filter='.data.designated_version.config |= del(.model, .models, .tools, .plugins)' ;;
            models_empty) preset_filter='.data.designated_version.config.models = []' ;;
            disabled|archived) preset_filter=".data.status = \"$name\"" ;;
            absent) preset_http="404" ;;
            wrong_slug) preset_filter='.data.slug = "other"' ;;
            missing_status) preset_filter='del(.data.status)' ;;
            http500) preset_http="500" ;;
            transport) preset_exit="7" ;;
            version_null) preset_filter='.data.designated_version = null' ;;
            config_missing) preset_filter='del(.data.designated_version.config)' ;;
            config_null) preset_filter='.data.designated_version.config = null' ;;
            config_array) preset_filter='.data.designated_version.config = []' ;;
            provider_missing) preset_filter='del(.data.designated_version.config.provider)' ;;
            provider_array) preset_filter='.data.designated_version.config.provider = []' ;;
            only_missing) preset_filter='del(.data.designated_version.config.provider.only)' ;;
            only_empty) preset_filter='.data.designated_version.config.provider.only = []' ;;
            only_sail) preset_filter='.data.designated_version.config.provider.only += ["sail-research"]' ;;
            only_deepseek) preset_filter='.data.designated_version.config.provider.only += ["deepseek"]' ;;
            only_string) preset_filter='.data.designated_version.config.provider.only = "fireworks"' ;;
            only_nonstring) preset_filter='.data.designated_version.config.provider.only += [7]' ;;
            collection_missing) preset_filter='del(.data.designated_version.config.provider.data_collection)' ;;
            collection_allow) preset_filter='.data.designated_version.config.provider.data_collection = "allow"' ;;
            models_other) preset_filter='.data.designated_version.config.models += ["openai/gpt-4o"]' ;;
            model_other) preset_filter='.data.designated_version.config.model = "openai/gpt-4o"' ;;
            models_string) preset_filter='.data.designated_version.config.models = .data.designated_version.config.model' ;;
            model_false) preset_filter='.data.designated_version.config.model = false' ;;
            tools_nonempty) preset_filter='.data.designated_version.config.tools = [{type: "web_search"}]' ;;
            plugins_nonempty) preset_filter='.data.designated_version.config.plugins = [{id: "web"}]' ;;
            tools_object) preset_filter='.data.designated_version.config.tools = {}' ;;
            plugins_object) preset_filter='.data.designated_version.config.plugins = {}' ;;
        esac
        jq "$preset_filter" "$good_preset" > "$preset_fixture"
        if [[ "$name" == non_json ]]; then
            printf '%s\n' '<html>not JSON</html>' > "$preset_fixture"
        fi
        if [[ "$expected" == "STATUS: OK"* ]]; then
            _selftest_preset_hash "$preset_fixture" > "$marker"
        fi
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rc=0
        _selftest_child --key-check > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if [[ "$(cat "$test_dir/stdout")" == "$expected" && "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 ]] &&
           { [[ "$expected" == "STATUS: OK"* && "$rc" == 0 ]] || [[ "$expected" == "STATUS: FAILED"* && "$rc" != 0 ]]; } &&
           [[ "$(cat "$test_dir/trace")" == $'GET\nPRESET_GET' && ! -e "$test_dir/ledger.tsv" &&
              ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
           ! grep -q 'ask_deepseek_payload' "$test_dir/temps"; then
            _selftest_pass "--key-check preset $name: one status line, no completion/payload/ledger, lock cleaned"
        else
            _selftest_fail "--key-check preset $name: status/no-completion contract (rc=$rc)"
        fi
    done

    cp "$good_preset" "$preset_fixture"
    preset_http="200"
    preset_exit="0"
    expected_hash="$(_selftest_preset_hash "$preset_fixture")"
    marker_failure='STATUS: FAILED — preset: deepseek-codex routing not yet verified for this config (marker ~/.config/openrouter/deepseek-codex.verified missing, unsafe or stale; complete the preset verification in references/legs.md; Codex-CLI DeepSeek path refused)'
    # All read-only cases share the request/artifact/lock contract.
    _selftest_readonly_contract() {
        [[ "$(cat "$test_dir/stdout")" == "$expected" &&
           "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 ]] &&
        { { [[ "$expected" == "STATUS: FAILED"* ]] && [[ "$rc" != 0 ]]; } ||
          { [[ "$expected" != "STATUS: FAILED"* ]] && [[ "$rc" == 0 ]]; }; } &&
        [[ "$(cat "$test_dir/trace")" == "$expected_trace" &&
           "$(cat "$test_dir/bearer")" == 1 && ! -e "$test_dir/ledger.tsv" &&
           ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
        ! grep -q 'ask_deepseek_payload' "$test_dir/temps"
    }
    expected_trace=$'GET\nPRESET_GET'
    for name in missing wrong_hash malformed symlink wrong_owner unsafe empty directory correct readonly first_line; do
        rm -f "$marker"
        printf '%s\n' "$expected_hash" > "$marker"
        chmod 600 "$marker"
        expected="$marker_failure"
        marker_reason=""
        marker_wrong_owner="0"
        case "$name" in
            missing) rm -f "$marker"; marker_reason="missing" ;;
            wrong_hash) printf '%064d\n' 0 > "$marker"; marker_reason="fingerprint mismatch (stale or malformed)" ;;
            malformed) printf 'not-a-64-hex-fingerprint\n%s\n' "$expected_hash" > "$marker"; marker_reason="fingerprint mismatch (stale or malformed)" ;;
            symlink) mv "$marker" "$test_dir/marker-target"; ln -s "$test_dir/marker-target" "$marker"; marker_reason="symlink" ;;
            wrong_owner) marker_wrong_owner="1"; marker_reason="wrong owner" ;;
            unsafe) chmod 644 "$marker"; marker_reason="mode 644 exceeds 0600" ;;
            empty) : > "$marker"; marker_reason="empty" ;;
            directory) rm -f "$marker"; mkdir "$marker"; marker_reason="not a regular file" ;;
            readonly) chmod 400 "$marker" ;;
            first_line) printf 'ignored second line\n' >> "$marker" ;;
        esac
        case "$name" in
            correct|readonly|first_line)
                expected='STATUS: OK — key-check: key capped at USD 10 or less with credit remaining; preset deepseek-codex active with verified routing policy' ;;
        esac
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rm -f "$test_dir/bearer"
        rc=0
        _selftest_child --key-check > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if _selftest_readonly_contract &&
           { { [[ -n "$marker_reason" ]] &&
               [[ "$(grep -Fxc -- "[delegate/deepseek] marker refused: $marker_reason" "$test_dir/stderr")" == 1 &&
                  "$(grep -Fc -- 'marker refused:' "$test_dir/stderr")" == 1 ]]; } ||
             { [[ -z "$marker_reason" ]] && ! grep -Fq -- 'marker refused:' "$test_dir/stderr"; }; } &&
           ! grep -Eq -- '[0-9a-f]{64}|offline-selftest-key' "$test_dir/stderr"; then
            _selftest_pass "--key-check marker $name: reason/status/trace/artifact/lock contract"
        else
            _selftest_fail "--key-check marker $name: reason/status/trace/artifact/lock contract (rc=$rc)"
        fi
        [[ "$name" != directory ]] || rmdir "$marker"
    done
    marker_wrong_owner="0"
    rm -f "$marker"
    for name in no_marker policy_failure overrides; do
        cp "$good_preset" "$preset_fixture"
        expected="$expected_hash"
        if [[ "$name" == policy_failure ]]; then
            jq '.data.designated_version.config.provider.data_collection = "allow"' "$good_preset" > "$preset_fixture"
            expected='STATUS: FAILED — preset: deepseek-codex routing policy not verified (needs provider.only within the allowlist, data_collection deny, DeepSeek-only model, no tools; Codex-CLI DeepSeek path refused)'
        elif [[ "$name" == overrides ]]; then
            override_env="wrong-env-key"
            override_file="$test_dir/missing-override-key"
        fi
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rm -f "$test_dir/bearer" "$test_dir/ledger.tsv"
        rc=0
        _selftest_child --preset-fingerprint > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if _selftest_readonly_contract && [[ ! -e "$marker" ]] &&
           { [[ "$name" != overrides ]] || [[ "$(grep -Fxc -- "$override_warning" "$test_dir/stderr")" == 1 ]]; }; then
            _selftest_pass "--preset-fingerprint $name: independent hash/status/trace/artifact/lock contract"
        else
            _selftest_fail "--preset-fingerprint $name: independent hash/status/trace/artifact/lock contract (rc=$rc)"
        fi
    done
    override_env=""
    override_file=""
    cp "$good_preset" "$preset_fixture"
    for readonly_mode in --key-check --preset-fingerprint; do
        for name in key preset; do
            key_exit="0"
            preset_exit="0"
            expected_trace="GET"
            expected='STATUS: FAILED — ceiling: OpenRouter key endpoint unreachable or timed out'
            if [[ "$name" == key ]]; then
                key_exit="28"
            else
                preset_exit="28"
                expected_trace=$'GET\nPRESET_GET'
                expected='STATUS: FAILED — preset: deepseek-codex missing or not active (Codex-CLI DeepSeek path refused)'
            fi
            : > "$test_dir/trace"
            : > "$test_dir/temps"
            rm -f "$test_dir/bearer" "$test_dir/ledger.tsv"
            rc=0
            _selftest_child "$readonly_mode" > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
            if _selftest_readonly_contract; then
                _selftest_pass "$readonly_mode $name GET curl timeout: one FAILED line, no completion/payload/ledger, lock cleaned"
            else
                _selftest_fail "$readonly_mode $name GET curl timeout: status/trace/artifact/lock contract (rc=$rc)"
            fi
        done
    done
    key_exit="0"
    preset_exit="0"
    for readonly_mode in --key-check --preset-fingerprint; do
        : > "$test_dir/trace"
        : > "$test_dir/temps"
        rm -f "$test_dir/ledger.tsv"
        rc=0
        _selftest_child "$readonly_mode" extra > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if [[ "$rc" != 0 && "$(cat "$test_dir/stdout")" == "STATUS: FAILED — validation: $readonly_mode takes no arguments" &&
              "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 && ! -s "$test_dir/trace" &&
              ! -e "$test_dir/ledger.tsv" && ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
           ! grep -q 'ask_deepseek_payload' "$test_dir/temps"; then
            _selftest_pass "$readonly_mode rejects extra arguments without requests/artifacts"
        else
            _selftest_fail "$readonly_mode extra-argument contract (rc=$rc)"
        fi
    done

    : > "$test_dir/trace"
    : > "$test_dir/temps"
    rm -f "$test_dir/ledger.tsv"
    rc=0
    env -i PATH="$test_bin:$PATH" TMPDIR="$test_dir/tmp" \
        DS_SELFTEST_MKTEMP="$(command -v mktemp)" DS_SELFTEST_TEMPS="$test_dir/temps" \
        bash "$test_dir/wrapper.sh" --key-check > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
    if [[ "$rc" != 0 && "$(cat "$test_dir/stdout")" == "STATUS: FAILED — key: OpenRouter key file not found at /.config/openrouter/key" &&
          "$(wc -l < "$test_dir/stdout" | tr -d ' ')" == 1 && ! -s "$test_dir/stderr" &&
          ! -s "$test_dir/trace" && ! -e "$test_dir/ledger.tsv" &&
          ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
       ! grep -q 'ask_deepseek_payload' "$test_dir/temps"; then
        _selftest_pass "--key-check HOME unset: classified missing key, no shell error or requests"
    else
        _selftest_fail "--key-check HOME unset: missing-key contract (rc=$rc)"
    fi
    key_only="0"
    : > "$test_dir/trace"
    rc=0
    _selftest_child 'offline selftest' "$test_dir/dispatch.md" > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
    if [[ "$rc" == 0 && "$(cat "$test_dir/trace")" == $'GET\nPOST' ]] &&
       grep -Fq 'Backend=openrouter mode=api ' "$test_dir/stderr"; then
        _selftest_pass "key-preflight lock released (LOCK_HELD=0 and directory removed) before completion"
    else
        _selftest_fail "key-preflight lock must be released before completion (rc=$rc)"
    fi

    for name in env file default; do
        override_env=""
        override_file=""
        expected_bearer="offline-selftest-key"
        if [[ "$name" != default ]]; then
            override_file="$test_dir/override-key"
            printf 'override-file-key\n' > "$override_file"
            chmod 600 "$override_file"
            expected_bearer="override-file-key"
        fi
        if [[ "$name" == env ]]; then
            override_env="override-env-key"
            expected_bearer="$override_env"
        fi
        : > "$test_dir/trace"
        rm -f "$test_dir/bearer" "$test_dir/ledger.tsv"
        rc=0
        _selftest_child 'offline precedence' "$test_dir/precedence-$name.md" > "$test_dir/stdout" 2> "$test_dir/stderr" || rc=$?
        if [[ "$rc" == 0 && "$(cat "$test_dir/bearer")" == 1 &&
              "$(cat "$test_dir/trace")" == $'GET\nPOST' &&
              ! -e "$test_home/.claude/delegate-logs/.deepseek.lock" ]] &&
           awk -F '\t' 'NF != 11 {exit 1} NR > 1 && $11 != "OK" {exit 1} END {if (NR != 2) exit 1}' "$test_dir/ledger.tsv"; then
            _selftest_pass "API key precedence $name: expected bearer used for key GET and completion"
        else
            _selftest_fail "API key precedence $name: bearer/dispatch contract (rc=$rc)"
        fi
    done
    override_env=""
    override_file=""
    expected_bearer="offline-selftest-key"

    concurrent="1"
    mkdir -p "$test_dir/barrier"
    rm -f "$test_dir/ledger.tsv"
    _selftest_child 'offline concurrent 1' "$test_dir/first.md" > "$test_dir/first.out" 2> "$test_dir/first.err" &
    first=$!
    _selftest_child 'offline concurrent 2' "$test_dir/second.md" > "$test_dir/second.out" 2> "$test_dir/second.err" &
    second=$!
    rc=0
    wait "$first" || rc=$?
    wait "$second" || rc=$?
    if [[ "$rc" == 0 ]] && awk -F '\t' 'NF != 11 {exit 1} NR > 1 && $11 != "OK" {exit 1} END {if (NR != 3) exit 1}' "$test_dir/ledger.tsv"; then
        _selftest_pass "two concurrent completions succeed with one header and two intact ledger rows"
    else
        _selftest_fail "two concurrent completions and ledger integrity (rc=$rc)"
    fi

    cat > "$test_dir/WriteProbe.pm" <<'PERL'
package WriteProbe;
BEGIN {
    *CORE::GLOBAL::syswrite = sub {
        my ($fh, $row, $length) = @_;
        open(my $log, ">>", $ENV{DS_SELFTEST_WRITES}) or die $!;
        print {$log} "$length\n";
        close($log);
        return CORE::syswrite($fh, $row, $length);
    };
}
1;
PERL
    if (
        LEDGER="$test_dir/single-write.tsv"
        LEDGER_READY="0"
        _prepare_ledger &&
        PERL5LIB="$test_dir" PERL5OPT=-MWriteProbe DS_SELFTEST_WRITES="$test_dir/writes" _append_ledger OK &&
        PERL5LIB="$test_dir" PERL5OPT=-MWriteProbe DS_SELFTEST_WRITES="$test_dir/writes" _append_ledger fixture-failure
    ) && [[ -f "$test_dir/writes" && "$(wc -l < "$test_dir/writes" | tr -d ' ')" == 2 ]] &&
       awk 'NR == FNR {bytes[NR]=$1; next} FNR > 1 && length($0)+1 != bytes[FNR-1] {exit 1} END {if (FNR != 3) exit 1}' \
           "$test_dir/writes" "$test_dir/single-write.tsv"; then
        _selftest_pass "each ledger row uses exactly one syswrite (OK and failure)"
    else
        _selftest_fail "each ledger row must use exactly one syswrite"
    fi
    unset -f _selftest_child _selftest_readonly_contract
}

_run_selftest() {
    local choice_error length empty top_error credits routing good outside key_null key_high key_warn noprov
    SELFTEST_FAILURES="0"
    if ! command -v jq >/dev/null 2>&1; then
        echo "FAIL: jq is required for SELFTEST"
        return 1
    fi
    if ! SELFTEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ask_deepseek_selftest.XXXXXX")"; then
        echo "FAIL: could not create SELFTEST directory"
        return 1
    fi
    choice_error="$SELFTEST_DIR/choice_error.json"
    length="$SELFTEST_DIR/length.json"
    empty="$SELFTEST_DIR/empty.json"
    top_error="$SELFTEST_DIR/top_error.json"
    credits="$SELFTEST_DIR/credits.json"
    routing="$SELFTEST_DIR/routing.json"
    good="$SELFTEST_DIR/good.json"
    outside="$SELFTEST_DIR/outside.json"
    key_null="$SELFTEST_DIR/key_null.json"
    key_high="$SELFTEST_DIR/key_high.json"
    key_warn="$SELFTEST_DIR/key_warn.json"

    cat > "$choice_error" <<'JSON'
{"choices":[{"error":{"message":"provider failed"},"finish_reason":"stop","message":{"content":"ignored"}}]}
JSON
    cat > "$length" <<'JSON'
{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}
JSON
    cat > "$empty" <<'JSON'
{"choices":[{"finish_reason":"stop","message":{"content":""}}]}
JSON
    cat > "$top_error" <<'JSON'
{"error":{"message":"upstream rejected request","metadata":{"raw":"provider error"}}}
JSON
    cat > "$credits" <<'JSON'
{"error":{"message":"Payment required"}}
JSON
    cat > "$routing" <<'JSON'
{"error":{"message":"No allowed providers are available for this request"}}
JSON
    cat > "$good" <<'JSON'
{"id":"gen-good","model":"deepseek/deepseek-v4.1-flash-1017","provider":"Fireworks","choices":[{"finish_reason":"stop","message":{"content":"VERDICT: CORRECT — fixture"}}],"usage":{"prompt_tokens":12,"completion_tokens":34,"completion_tokens_details":{"reasoning_tokens":21},"cost":0.125}}
JSON
    cat > "$outside" <<'JSON'
{"id":"gen-drift","model":"deepseek/deepseek-v4.1-flash-1017","provider":"ForbiddenCloud","choices":[{"finish_reason":"stop","message":{"content":"VERDICT: CORRECT — fixture"}}],"usage":{"prompt_tokens":1,"completion_tokens":2}}
JSON
    cat > "$key_null" <<'JSON'
{"data":{"limit":null,"usage":0,"limit_remaining":null}}
JSON
    cat > "$key_high" <<'JSON'
{"data":{"limit":100,"usage":1,"limit_remaining":99}}
JSON
    cat > "$key_warn" <<'JSON'
{"data":{"limit":10,"usage":9,"limit_remaining":1}}
JSON

    if _assess_response 200 0 "$choice_error"; then
        _selftest_fail "choices[0].error rejected"
    elif [[ "$FAILURE_LINE" == *"provider failed"* ]]; then
        _selftest_pass "choices[0].error rejected and classified"
    else
        _selftest_fail "choices[0].error classification"
    fi

    if _assess_response 200 0 "$length"; then
        _selftest_fail "finish_reason length rejected"
    elif [[ "$FAILURE_CLASS" == "finish-length" ]]; then
        _selftest_pass "finish_reason length rejected"
    else
        _selftest_fail "finish_reason length classification"
    fi

    if _assess_response 200 0 "$empty"; then
        _selftest_fail "empty content rejected"
    elif [[ "$FAILURE_CLASS" == "empty-content" ]]; then
        _selftest_pass "empty content rejected"
    else
        _selftest_fail "empty content classification"
    fi

    if _assess_response 200 0 "$top_error"; then
        _selftest_fail "top-level error rejected"
    elif [[ "$FAILURE_LINE" == *"upstream rejected request"* ]]; then
        _selftest_pass "HTTP-200 top-level error classified"
    else
        _selftest_fail "HTTP-200 top-level error classification"
    fi

    if _assess_response 402 0 "$credits"; then
        _selftest_fail "HTTP 402 rejected"
    elif [[ "$FAILURE_CLASS" == "credits" ]]; then
        _selftest_pass "HTTP 402 credits classification"
    else
        _selftest_fail "HTTP 402 credits classification"
    fi

    if _assess_response 400 0 "$routing"; then
        _selftest_fail "routing error rejected"
    elif [[ "$FAILURE_CLASS" == "routing" && "$FAILURE_LINE" == *"fireworks,together,parasail,coreweave,digitalocean"* ]]; then
        _selftest_pass "routing failure names active allowlist"
    else
        _selftest_fail "routing failure classification"
    fi

    if _assess_response 200 0 "$good" && \
       [[ "$PROMPT_TOKENS" == "12" && "$COMPLETION_TOKENS" == "34" && \
          "$REASONING_TOKENS" == "21" && "$RESPONSE_COST" == "0.125" && \
          "$RESOLVED_PROVIDER" == "Fireworks" && "$GENERATION_ID" == "gen-good" ]]; then
        _selftest_pass "good response accepted and fields extracted"
    else
        _selftest_fail "good response acceptance/extraction"
    fi

    if _assess_response 200 0 "$outside"; then
        _selftest_fail "outside provider rejected"
    elif [[ "$FAILURE_LINE" == "STATUS: FAILED — CONTRACT-DRIFT: routed to 'ForbiddenCloud' outside allowlist" ]]; then
        _selftest_pass "outside provider CONTRACT-DRIFT"
    else
        _selftest_fail "outside provider CONTRACT-DRIFT classification"
    fi

    noprov="$SELFTEST_DIR/noprov.json"
    cat > "$noprov" <<'JSON'
{"id":"gen-noprov","model":"deepseek/deepseek-v4.1-flash-1017","choices":[{"finish_reason":"stop","message":{"content":"VERDICT: CORRECT — fixture"}}],"usage":{"prompt_tokens":1,"completion_tokens":2}}
JSON
    if _assess_response 200 0 "$noprov"; then
        _selftest_fail "missing provider rejected"
    elif [[ "$FAILURE_CLASS" == "provider-missing" ]]; then
        _selftest_pass "missing provider fails closed"
    else
        _selftest_fail "missing provider classification"
    fi

    if _assess_key_response 200 0 "$key_null"; then
        _selftest_fail "null key limit rejected"
    elif [[ "$FAILURE_CLASS" == "ceiling-null-limit" ]]; then
        _selftest_pass "null key limit hard failure"
    else
        _selftest_fail "null key limit classification"
    fi

    if _assess_key_response 200 0 "$key_high"; then
        _selftest_fail "key limit above ceiling rejected"
    elif [[ "$FAILURE_CLASS" == "ceiling-too-high" ]]; then
        _selftest_pass "key limit above ceiling hard failure"
    else
        _selftest_fail "key limit above ceiling classification"
    fi

    if _assess_key_response 200 0 "$key_warn" && [[ -n "$KEY_WARNING" ]]; then
        _selftest_pass "80-percent key usage passes with WARN"
    else
        _selftest_fail "80-percent key usage WARN"
    fi

    cat > "$SELFTEST_DIR/key_ok.json" <<'JSON'
{"data":{"limit":10,"usage":1,"limit_remaining":9}}
JSON
    cat > "$SELFTEST_DIR/key_exhausted.json" <<'JSON'
{"data":{"limit":10,"usage":10,"limit_remaining":0}}
JSON
    _selftest_modes

    if [[ "$SELFTEST_FAILURES" -eq 0 ]]; then
        echo "SELFTEST: PASS"
        return 0
    fi
    echo "SELFTEST: FAIL (${SELFTEST_FAILURES} fixture failures)"
    return 1
}

# Fingerprints bind only baked policy, never caller-controlled environment.
if [[ "${1:-}" == "--fingerprint" ]]; then
    if ! _fingerprint; then
        echo "ERROR: no SHA-256 utility available" >&2
        exit 1
    fi
    exit 0
fi

if [[ "${DEEPSEEK_DELEGATE_SELFTEST:-0}" == "1" ]]; then
    if _run_selftest; then
        exit 0
    fi
    exit 1
fi

# === Inputs ===
QUERY="${1:-}"
if [[ "$QUERY" == "--key-check" || "$QUERY" == "--preset-fingerprint" ]]; then
    KEY_CHECK_ONLY="1"
    if [[ "$QUERY" == "--preset-fingerprint" ]]; then
        PRESET_FINGERPRINT_ONLY="1"
    fi
    if [[ "$#" != "1" ]]; then
        _fail "validation" "validation-key-check" "STATUS: FAILED — validation: $QUERY takes no arguments"
    fi
    if ! KEY_ERR_LOG="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_err.XXXXXX")"; then
        _fail "temp" "temp-err" "STATUS: FAILED — temporary-file creation failed for key-check stderr"
    fi
    ERR_LOG="$KEY_ERR_LOG"
else
    OUTPUT="${2:-${TMPDIR:-/tmp}/claude/deepseek_result_$(date +%s)_$$.md}"
    if ! mkdir -p "$(dirname "$OUTPUT")"; then
        echo "ERROR: cannot create output directory for $OUTPUT" >&2
        exit 1
    fi
    OUTPUT="$(_realpath_m "$OUTPUT")"
    ERR_LOG="${OUTPUT%.md}.err"
    RAW_JSON="${OUTPUT%.md}.json"
    if ! : > "$ERR_LOG"; then
        echo "ERROR: cannot create stderr log: $ERR_LOG" >&2
        exit 1
    fi
fi
if ! chmod 600 "$ERR_LOG"; then
    _fail "output" "output-err-mode" "STATUS: FAILED — output: could not secure stderr log permissions"
fi
if [[ "${DEEPSEEK_DELEGATE_EFFORT:-max}" != "max" ]]; then
    echo "[delegate/deepseek] WARN: DEEPSEEK_DELEGATE_EFFORT ignored; DeepSeek effort is always max" >&2
fi

# Legacy DeepSeek wrapper variables are intentionally ignored so a caller cannot
# silently configure this wrapper through the wrong namespace.
for _legacy in DEEPSEEK_MODEL DEEPSEEK_EFFORT DEEPSEEK_TIMEOUT DEEPSEEK_PROVIDERS \
    DEEPSEEK_MAX_TOKENS DEEPSEEK_ZDR DEEPSEEK_LEDGER DEEPSEEK_KEY_FILE \
    DEEPSEEK_SELFTEST DEEPSEEK_DEPTH; do
    if [[ -n "${!_legacy:-}" ]]; then
        echo "[delegate/deepseek] WARN: \$${_legacy} is IGNORED — use ${_legacy/DEEPSEEK_/DEEPSEEK_DELEGATE_}" >&2
    fi
done

if [[ -z "$QUERY" ]]; then
    _fail "validation" "validation-prompt" "STATUS: FAILED — validation: empty prompt (usage: ask_deepseek.sh \"prompt\"|@file [output.md])"
fi
if [[ "$QUERY" == @* ]]; then
    QUERY_FILE="${QUERY:1}"
    if [[ ! -f "$QUERY_FILE" ]]; then
        _fail "validation" "validation-atfile" "STATUS: FAILED — validation: query file not found: $(_header_field "$QUERY_FILE")"
    fi
    if ! QUERY="$(cat "$QUERY_FILE")"; then
        _fail "validation" "validation-atfile-read" "STATUS: FAILED — validation: query file could not be read: $(_header_field "$QUERY_FILE")"
    fi
    if [[ -z "$QUERY" ]]; then
        _fail "validation" "validation-prompt" "STATUS: FAILED — validation: expanded query file is empty"
    fi
fi

if [[ -z "${HOME:-}" ]]; then
    if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
        _fail "key" "key-missing" "STATUS: FAILED — key: OpenRouter key file not found at ${HOME:-}/.config/openrouter/key"
    fi
    _fail "validation" "validation-home" "STATUS: FAILED — validation: HOME is unset"
fi
if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
    _fail "validation" "validation-timeout" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_TIMEOUT must be integer seconds, got '$(_header_field "$TIMEOUT")'"
fi
if ! [[ "$EFFORT" =~ ^(low|medium|high|xhigh|max)$ ]]; then
    _fail "validation" "validation-effort" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_EFFORT must be ${EFFORT_ENUM}, got '$(_header_field "$EFFORT")'"
fi
if ! [[ "$MAX_TOKENS" =~ ^[1-9][0-9]*$ ]]; then
    _fail "validation" "validation-max-tokens" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_MAX_TOKENS must be a positive integer, got '$(_header_field "$MAX_TOKENS")'"
fi
if [[ "$ZDR" != "0" && "$ZDR" != "1" ]]; then
    _fail "validation" "validation-zdr" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_ZDR must be 0 or 1, got '$(_header_field "$ZDR")'"
fi
if ! [[ "${DEEPSEEK_DELEGATE_DEPTH:-0}" =~ ^[0-9]+$ ]]; then
    _fail "validation" "validation-depth" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_DEPTH must be a non-negative integer"
fi
if [[ "${DEEPSEEK_DELEGATE_DEPTH:-0}" -ge 1 ]]; then
    _fail "validation" "recursion" "STATUS: FAILED — recursion: DEEPSEEK_DELEGATE_DEPTH >= 1; delegate-of-delegate blocked"
fi
if [[ -z "$MODEL" || "$MODEL" == *$'\n'* || "$MODEL" == *$'\r'* ]]; then
    _fail "validation" "validation-model" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_MODEL must be a non-empty single-line slug"
fi
if [[ -z "$LEDGER" ]]; then
    _fail "validation" "validation-ledger" "STATUS: FAILED — validation: DEEPSEEK_DELEGATE_LEDGER is empty"
fi
if ! _validate_providers; then
    _fail "validation" "$FAILURE_CLASS" "$FAILURE_LINE"
fi

for _dep in curl jq perl; do
    if ! command -v "$_dep" >/dev/null 2>&1; then
        _fail "dependency" "dependency-${_dep}" "STATUS: FAILED — dependency: ask_deepseek.sh requires ${_dep}"
    fi
done

# Recursion cannot occur through the API leg, but pin the guard for any future
# child added around curl.
export DEEPSEEK_DELEGATE_DEPTH=1

if [[ "$KEY_CHECK_ONLY" == "0" ]] && ! _prepare_ledger; then
    _fail "ledger" "ledger-unwritable" "STATUS: FAILED — ledger: cannot create or safely use $(_header_field "$LEDGER")"
fi
if ! _acquire_lock; then
    _fail "lock" "$FAILURE_CLASS" "$FAILURE_LINE"
fi

# Resolve the key without ever putting it in curl's argv.
API_KEY=""
if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
    if [[ -n "${OPENROUTER_API_KEY:-}${DEEPSEEK_DELEGATE_KEY_FILE:-}" ]]; then
        echo '[delegate/deepseek] WARN: --key-check ignores OPENROUTER_API_KEY and DEEPSEEK_DELEGATE_KEY_FILE; checking ~/.config/openrouter/key (the Codex-CLI credential)' >&2
    fi
    KEY_FILE="${HOME:-}/.config/openrouter/key"
fi
if [[ "$KEY_CHECK_ONLY" == "0" && -n "${OPENROUTER_API_KEY:-}" ]]; then
    API_KEY="$OPENROUTER_API_KEY"
else
    if [[ -z "$KEY_FILE" ]]; then
        _fail "key" "key-path" "STATUS: FAILED — key: DEEPSEEK_DELEGATE_KEY_FILE is empty"
    fi
    if [[ ! -e "$KEY_FILE" ]]; then
        _fail "key" "key-missing" "STATUS: FAILED — key: OpenRouter key file not found at $(_header_field "$KEY_FILE")"
    fi
    if [[ -L "$KEY_FILE" || ! -f "$KEY_FILE" ]]; then
        _fail "key" "key-not-regular" "STATUS: FAILED — key: key file must be a regular non-symlink file"
    fi
    KEY_UID="$(_file_uid "$KEY_FILE")"
    if [[ "$KEY_UID" != "$(id -u)" ]]; then
        _fail "key" "key-owner" "STATUS: FAILED — key: key file is not owned by the current uid"
    fi
    KEY_MODE="$(_file_mode "$KEY_FILE")"
    if ! [[ "$KEY_MODE" =~ ^[0-7]{3,4}$ ]]; then
        _fail "key" "key-mode" "STATUS: FAILED — key: could not determine key-file permissions"
    fi
    KEY_MODE_VALUE=$((8#$KEY_MODE))
    if (( (KEY_MODE_VALUE & ~ 0600) != 0 )); then
        _fail "key" "key-mode" "STATUS: FAILED — key: key-file permissions must be a subset of 0600 (got ${KEY_MODE})"
    fi
    if [[ ! -s "$KEY_FILE" ]]; then
        _fail "key" "key-empty" "STATUS: FAILED — key: key file is empty"
    fi
    if ! API_KEY="$(cat "$KEY_FILE")"; then
        _fail "key" "key-read" "STATUS: FAILED — key: key file could not be read"
    fi
fi
if [[ -z "$API_KEY" ]]; then
    _fail "key" "key-empty" "STATUS: FAILED — key: OpenRouter API key is empty"
fi
if printf '%s' "$API_KEY" | LC_ALL=C grep -q '[[:cntrl:]"\\]'; then
    _fail "key" "key-format" "STATUS: FAILED — key: OpenRouter API key contains a character unsafe for curl config"
fi
if ! CURL_CONFIG="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_curl.XXXXXX")"; then
    _fail "temp" "temp-config" "STATUS: FAILED — temporary-file creation failed for curl config"
fi
if ! CURL_CODE_FILE="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_http.XXXXXX")"; then
    _fail "temp" "temp-http" "STATUS: FAILED — temporary-file creation failed for HTTP status"
fi
if ! KEY_BODY="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_key.XXXXXX")"; then
    _fail "temp" "temp-key-body" "STATUS: FAILED — temporary-file creation failed for key preflight"
fi
if ! chmod 600 "$CURL_CONFIG" "$CURL_CODE_FILE" "$KEY_BODY"; then
    _fail "temp" "temp-mode" "STATUS: FAILED — temporary-file permissions could not be secured"
fi
if ! {
    printf 'header = "Authorization: Bearer %s"\n' "$API_KEY"
    printf 'header = "Content-Type: application/json"\n'
} > "$CURL_CONFIG"; then
    _fail "temp" "temp-config-write" "STATUS: FAILED — curl config could not be written"
fi
API_KEY=""

if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
    echo "[delegate/deepseek] Backend=openrouter mode=key-check model=$MODEL effort=$EFFORT timeout=${TIMEOUT}s" >&2
else
    echo "[delegate/deepseek] Backend=openrouter mode=api model=$MODEL effort=$EFFORT timeout=${TIMEOUT}s" >&2
fi
echo "[delegate/deepseek] Providers: ${ACTIVE_ALLOWLIST// /,} (data_collection=deny; zdr=${ZDR})" >&2
echo "[delegate/deepseek] Running USD ${CEILING_USD} key-ceiling preflight..." >&2

_curl_request GET "${BASE_URL}/key" "$KEY_BODY"
if ! _assess_key_response "$CURL_HTTP_CODE" "$CURL_EXIT" "$KEY_BODY"; then
    if [[ "$KEY_CHECK_ONLY" == "0" ]]; then
        cp "$KEY_BODY" "$RAW_JSON" 2>/dev/null || true
    fi
    _fail "key-${CURL_HTTP_CODE}" "$FAILURE_CLASS" "$FAILURE_LINE"
fi
if [[ -n "$KEY_WARNING" ]]; then
    echo "$KEY_WARNING" >&2
fi

# Only the Codex-CLI path depends on a preset; API dispatch pins its providers
# in the completion payload. Codex sends no provider field, so the preset must
# carry the policy. Assume request-shaped config and fail closed on other shapes.
# Keep CLI checks under the same lock and verify policy and marker every time.
if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
    _curl_request GET "${BASE_URL}/presets/deepseek-codex" "$KEY_BODY"
    if [[ "$CURL_EXIT" != "0" || "$CURL_HTTP_CODE" != "200" ]] || ! jq -se '
        length == 1 and (.[0].data | type == "object" and
            .slug == "deepseek-codex" and .status == "active")
    ' "$KEY_BODY" >/dev/null 2>&1; then
        _fail "preset" "preset" "STATUS: FAILED — preset: deepseek-codex missing or not active (Codex-CLI DeepSeek path refused)"
    fi
    if ! jq -e --arg allowlist "$ALLOWLIST" --arg model "$DEFAULT_MODEL" '
        .data.designated_version.config
        | type == "object" and
          (.provider | type == "object" and
            (.only | type == "array" and length > 0 and
              all(.[]; type == "string" and
                (ascii_downcase | split("/")[0] | gsub("[^a-z0-9]"; "") as $name
                 | ($allowlist | split(" ") | index($name)) != null))) and
            .data_collection == "deny") and
          (.model == null or .model == $model) and
          (.models == null or (.models | type == "array" and all(.[]; . == $model))) and
          (.tools == null or .tools == []) and
          (.plugins == null or .plugins == [])
    ' "$KEY_BODY" >/dev/null 2>&1; then
        _fail "preset" "preset-policy" "STATUS: FAILED — preset: deepseek-codex routing policy not verified (needs provider.only within the allowlist, data_collection deny, DeepSeek-only model, no tools; Codex-CLI DeepSeek path refused)"
    fi
    if ! PRESET_FINGERPRINT="$(_preset_fingerprint "$KEY_BODY")" ||
       ! [[ "$PRESET_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]]; then
        _fail "preset" "preset-fingerprint" "STATUS: FAILED — preset: deepseek-codex config fingerprint could not be computed"
    fi
    if [[ "$PRESET_FINGERPRINT_ONLY" == "0" ]] && ! _preset_marker_matches "$PRESET_FINGERPRINT"; then
        _fail "preset" "preset-unverified" "STATUS: FAILED — preset: deepseek-codex routing not yet verified for this config (marker ~/.config/openrouter/deepseek-codex.verified missing, unsafe or stale; complete the preset verification in references/legs.md; Codex-CLI DeepSeek path refused)"
    fi
fi

if ! _release_lock; then
    _fail "lock" "lock-release" "STATUS: FAILED — lock: could not release key-preflight lock"
fi
if [[ "$KEY_CHECK_ONLY" == "1" ]]; then
    if [[ "$PRESET_FINGERPRINT_ONLY" == "1" ]]; then
        printf '%s\n' "$PRESET_FINGERPRINT"
        exit 0
    fi
    echo "STATUS: OK — key-check: key capped at USD ${CEILING_USD} or less with credit remaining; preset deepseek-codex active with verified routing policy"
    exit 0
fi

if ! PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_payload.XXXXXX")"; then
    _fail "temp" "temp-payload" "STATUS: FAILED — temporary-file creation failed for payload"
fi
if ! PROVIDERS_JSON="$(printf '%s\n' $ACTIVE_ALLOWLIST | jq -R . | jq -s .)"; then
    _fail "payload" "provider-array-build" "STATUS: FAILED — payload: could not encode the provider allowlist"
fi
if [[ "$ZDR" == "1" ]]; then
    if ! printf '%s' "$QUERY" | jq -Rs --arg model "$MODEL" --arg effort "$EFFORT" \
        --argjson max_tokens "$MAX_TOKENS" --argjson providers "$PROVIDERS_JSON" '
          {
            model: $model,
            messages: [{role:"user", content:.}],
            max_tokens: $max_tokens,
            reasoning: {effort:$effort},
            provider: {
              only:$providers,
              allow_fallbacks:true,
              data_collection:"deny",
              require_parameters:true,
              zdr:true
            }
          }
        ' > "$PAYLOAD_FILE"; then
        _fail "payload" "payload-build" "STATUS: FAILED — payload: could not encode the prompt as JSON"
    fi
else
    if ! printf '%s' "$QUERY" | jq -Rs --arg model "$MODEL" --arg effort "$EFFORT" \
        --argjson max_tokens "$MAX_TOKENS" --argjson providers "$PROVIDERS_JSON" '
          {
            model: $model,
            messages: [{role:"user", content:.}],
            max_tokens: $max_tokens,
            reasoning: {effort:$effort},
            provider: {
              only:$providers,
              allow_fallbacks:true,
              data_collection:"deny",
              require_parameters:true
            }
          }
        ' > "$PAYLOAD_FILE"; then
        _fail "payload" "payload-build" "STATUS: FAILED — payload: could not encode the prompt as JSON"
    fi
fi
if ! chmod 600 "$PAYLOAD_FILE"; then
    _fail "payload" "payload-mode" "STATUS: FAILED — payload: permissions could not be secured"
fi

echo "[delegate/deepseek] Sending query ($(printf %s "$QUERY" | wc -c | tr -d ' ') chars)..." >&2
echo "[delegate/deepseek] Output: $OUTPUT" >&2

# RAW_JSON is the curl destination, so even rejected HTTP/JSON/provider responses
# remain available for forensic review.
_curl_request POST "${BASE_URL}/chat/completions" "$RAW_JSON" "$PAYLOAD_FILE"
if ! _assess_response "$CURL_HTTP_CODE" "$CURL_EXIT" "$RAW_JSON"; then
    {
        echo "HTTP $CURL_HTTP_CODE (curl exit $CURL_EXIT)"
        _body_error_text "$RAW_JSON"
    } >> "$ERR_LOG" 2>/dev/null || true
    _fail "${CURL_HTTP_CODE}" "$FAILURE_CLASS" "$FAILURE_LINE"
fi

if ! BODY_TXT="$(mktemp "${TMPDIR:-/tmp}/ask_deepseek_body.XXXXXX")"; then
    _fail "temp" "temp-body" "STATUS: FAILED — temporary-file creation failed for response body"
fi
if ! chmod 600 "$BODY_TXT"; then
    _fail "temp" "temp-body-mode" "STATUS: FAILED — response-body temporary permissions could not be secured"
fi
if ! jq -r '.choices[0].message.content' "$RAW_JSON" > "$BODY_TXT"; then
    _fail "parse" "content-extract" "STATUS: FAILED — response content could not be extracted after validation"
fi

if ! _append_ledger "OK"; then
    _fail "ledger" "ledger-append" "STATUS: FAILED — ledger: accepted ballot could not be recorded"
fi
LEDGER_OK_LOGGED="1"
if ! _write_header_and_body "0" "" "$BODY_TXT"; then
    _fail "output" "output-write" "STATUS: FAILED — output: accepted ballot could not be written"
fi

echo "[delegate/deepseek] Done. Result: $(wc -c < "$OUTPUT" | tr -d ' ') bytes → $OUTPUT (envelope: $RAW_JSON, stderr: $ERR_LOG)" >&2
echo "$OUTPUT"
exit 0
