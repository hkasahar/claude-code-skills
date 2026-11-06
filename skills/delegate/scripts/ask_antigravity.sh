#!/bin/bash
# ask_antigravity.sh — Dispatch via agy with explicit model provenance, optional rerouting and a shared deadline.
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
            use POSIX qw(:sys_wait_h :signal_h setsid);
            my $secs = shift @ARGV;
            # Block cancellation until both sides of the fork can clean up safely.
            my $blocked = POSIX::SigSet->new(SIGTERM, SIGINT, SIGHUP);
            my $old_mask = POSIX::SigSet->new();
            sigprocmask(SIG_BLOCK, $blocked, $old_mask) or die "sigprocmask: $!";
            my $pid = fork // die "fork: $!";
            if ($pid == 0) {
                setsid() >= 0 or die "setsid: $!";
                $SIG{TERM} = $SIG{INT} = $SIG{HUP} = "DEFAULT";
                sigprocmask(SIG_SETMASK, $old_mask) or die "sigprocmask: $!";
                exec @ARGV; die "exec: $!";
            }
            my $stop = sub {
                my ($code) = @_;
                $SIG{ALRM} = $SIG{TERM} = $SIG{INT} = $SIG{HUP} = "IGNORE";
                kill 9, $pid; # Stop new descendants even if setsid has not run yet.
                kill 9, -$pid;
                waitpid($pid, 0);
                exit $code;
            };
            $SIG{ALRM} = sub { $stop->(124) };
            $SIG{TERM} = sub { $stop->(143) };
            $SIG{INT}  = sub { $stop->(130) };
            $SIG{HUP}  = sub { $stop->(129) };
            sigprocmask(SIG_SETMASK, $old_mask) or die "sigprocmask: $!";
            # SIGKILL cannot be forwarded; agy --print-timeout bounds that case.
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

# Model errors can arrive on either stream; other classifiers retain their stderr contract.
_classify_failure() {
    local err_file="$1"
    local exit_code="$2"
    local timeout="$3"

    if [[ "$exit_code" == "124" ]]; then
        echo "STATUS: FAILED — Antigravity timed out after ${timeout}s"
        return
    fi
    local model_diag
    model_diag="$( { head -20 "$err_file"; head -20 "$OUTPUT"; } 2>/dev/null || true)"
    if [[ ! -s "$err_file" ]] && ! grep -qiE 'invalid model selection|not recognized as a known model|unknown model|model not.{0,10}(found|supported|available)|404' <<< "$model_diag"; then
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
    elif grep -qiE 'invalid model selection|not recognized as a known model|unknown model|model not.{0,10}(found|supported|available)|404' <<< "$model_diag"; then
        echo "STATUS: FAILED — Antigravity model '$MODEL' unavailable — check \`agy models\` and ANTIGRAVITY_MODEL"
    else
        echo "STATUS: FAILED — Antigravity exited $exit_code; first stderr lines: $(head -3 "$err_file" | tr '\n' ' ' | cut -c1-200)"
    fi
}

_usage() {
    echo 'Usage: ask_antigravity.sh [--model <id>] [--reroute auto|refuse|off] [--] "prompt" [output.md]' >&2
    echo '       QUERY may also be @prompt_file.md' >&2
    exit 1
}

# Consume only exact option tokens so existing prompts beginning with -- still work.
MODEL_ARG=""; MODEL_SOURCE=""; REROUTE=off
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model|--reroute)
            [[ $# -ge 2 && -n "$2" ]] || _usage
            case "$2" in --model|--reroute|--) _usage ;; esac
            if [[ "$1" == --model ]]; then
                MODEL_ARG="$2"; MODEL_SOURCE=argv
            else
                case "$2" in auto|refuse|off) ;; *) _usage ;; esac
                REROUTE="$2"
            fi
            shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done
QUERY="${1:-}"
[[ -n "$QUERY" ]] || _usage
OUTPUT="${2:-${TMPDIR:-/tmp}/claude/antigravity_result_$(date +%s)_$$.md}"
TIMEOUT="${ANTIGRAVITY_TIMEOUT:-600}"
if [[ "$MODEL_SOURCE" == argv ]]; then
    MODEL="$MODEL_ARG"
elif [[ -n "${ANTIGRAVITY_MODEL:-}" ]]; then
    MODEL="$ANTIGRAVITY_MODEL"; MODEL_SOURCE=env
else
    MODEL=gemini-4-argon-high; MODEL_SOURCE=default
fi
if [[ "$MODEL" == gemini-3.1-pro || "$MODEL" == gemini-4-argon ]]; then
    if [[ "$MODEL_SOURCE" == argv ]]; then
        LEGACY_SOURCE="--model $MODEL"
    else
        LEGACY_SOURCE="ANTIGRAVITY_MODEL=$MODEL"
    fi
    echo "[delegate/antigravity] WARN: $LEGACY_SOURCE is the legacy informational value; using gemini-4-argon-high" >&2
    MODEL=gemini-4-argon-high
fi
# Canonicalize policy decisions, but preserve display labels on the agy command.
case "$MODEL" in
    'Gemini 4 Argon (High)') CANON=gemini-4-argon-high ;;
    'Gemini 4 Argon (Medium)') CANON=gemini-4-argon-medium ;;
    'Gemini 4 Argon (Low)') CANON=gemini-4-argon-low ;;
    'Gemini 3.1 Pro (High)') CANON=gemini-3.1-pro-high ;;
    'Gemini 3.1 Pro (Low)') CANON=gemini-3.1-pro-low ;;
    'Gemini 3.8 Flash (High)') CANON=gemini-3.8-flash-high ;;
    'Claude Opus 5.5 (High)') CANON=claude-opus-5-5-high ;;
    *) CANON="$MODEL" ;;
esac
REQUESTED="$CANON"; FAMILY=claude
if [[ "$CANON" == gemini-* || "$MODEL" == 'Gemini '* ]]; then
    FAMILY=gemini
fi
VERIFIED=skipped
YOLO="${ANTIGRAVITY_YOLO:-0}"
QUOTA=off; REROUTE_FIELD=none; FIRST_ERR=""

# Resolve before validation so model-policy failures always name a reviewable file.
mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(_realpath_m "$OUTPUT")"
ERR_LOG="${OUTPUT%.md}.err"

_header() {
    # Invalid ids/env values can reach refusal headers; strip braces there too.
    printf '<!-- ask_antigravity.sh | %s | exit=%s | model=%s verified=%s | yolo=%s | requested=%s family=%s reroute=%s quota=%s -->' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$MODEL" "$VERIFIED" "$YOLO" \
        "$REQUESTED" "$FAMILY" "$REROUTE_FIELD" "$QUOTA" | tr -d '{}\r\n'
    printf '\n'
}

_append_first_error() {
    if [[ -n "$FIRST_ERR" ]]; then
        printf '\n--- first attempt stderr (model=%s) ---\n' "$REQUESTED" >> "$ERR_LOG"
        cat "$FIRST_ERR" >> "$ERR_LOG"
        rm -f "$FIRST_ERR"
        FIRST_ERR=""
    fi
}

_fail_before_dispatch() {
    local code="${2:-1}" tmp
    tmp="$(mktemp "${OUTPUT}.tmp.XXXXXX")"
    { _header "$code"; echo ""; echo "$1"; } > "$tmp"
    mv -f "$tmp" "$OUTPUT"
    _append_first_error
    echo "ERROR: $1" >&2
    echo "$OUTPUT"
    exit "$code"
}

if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || (( 10#$TIMEOUT <= 0 )); then
    echo "ERROR: ANTIGRAVITY_TIMEOUT must be a positive integer in seconds, got: $TIMEOUT" >&2
    echo "       Reject: '0', '5m', '600s', decimals, empty. Use a positive integer (e.g. 600)." >&2
    exit 1
fi
if [[ -n "${ANTIGRAVITY_REROUTE:-}" ]]; then
    echo "[delegate/antigravity] WARN: ANTIGRAVITY_REROUTE is ignored; pass --reroute on the command line (environment is inherited by child processes)" >&2
fi
if [[ "$FAMILY" == claude ]]; then
    if [[ "$CANON" != claude-opus-5-5-high ]]; then
        _fail_before_dispatch "STATUS: FAILED — model '$MODEL' is not allowed by policy (non-Gemini models: claude-opus-5-5-high via --model only)"
    elif [[ "$MODEL_SOURCE" != argv ]]; then
        _fail_before_dispatch "STATUS: FAILED — Claude-family model '$MODEL' must be passed as --model on the command line; ANTIGRAVITY_MODEL is inherited by child processes and could turn another caller's Gemini voter into a Claude voter"
    fi
fi
if [[ "${ANTIGRAVITY_VOTE:-0}" == 1 ]]; then
    if [[ "$REROUTE" != off ]]; then
        _fail_before_dispatch "STATUS: FAILED — vote mode forbids --reroute $REROUTE"
    fi
    if ! [[ "$CANON" =~ ^gemini-([0-9]+-argon-(high|medium|low)|[0-9][0-9.]*-pro-(high|low))$ ]]; then
        _fail_before_dispatch "STATUS: FAILED — vote mode requires a Gemini Pro id; got $MODEL"
    fi
    REROUTE=off
fi
if [[ "$REROUTE" == auto || "$REROUTE" == refuse ]]; then
    LOW_QUOTA="${ANTIGRAVITY_LOW_QUOTA-0.20}"
    # Keep thresholds on the display grid so a low fraction cannot print as equal.
    if ! [[ "$LOW_QUOTA" =~ ^([0-9]+([.][0-9]{0,4})?|[.][0-9]{1,4})$ ]] ||
        ! LC_ALL=C awk -v value="$LOW_QUOTA" 'BEGIN {exit !(value > 0 && value <= 1)}'; then
        echo "ERROR: ANTIGRAVITY_LOW_QUOTA must be a decimal in (0,1] with at most 4 decimal places, got: $LOW_QUOTA" >&2
        _usage
    fi
fi

_remaining() { REMAINING=$((DEADLINE - SECONDS)); }
_deadline_failure() {
    _remaining
    _fail_before_dispatch "STATUS: FAILED — deadline: ${REMAINING}s left ${1:-before dispatch} (ANTIGRAVITY_TIMEOUT=$TIMEOUT)" 124
}
_probe_cap() {
    _remaining
    PROBE_CAP=$((REMAINING - RESERVE))
    if (( PROBE_CAP > 30 )); then PROBE_CAP=30; fi
}
# Preserve the decimal text so low values cannot round up to the displayed boundary.
_fraction4() {
    python3 - "$1" <<'PY'
from decimal import Decimal, ROUND_DOWN
import sys

print(format(Decimal(sys.argv[1]).quantize(Decimal("0.0001"), rounding=ROUND_DOWN), "f"))
PY
}
_percent() {
    python3 - "$(_fraction4 "$1")" <<'PY'
from decimal import Decimal
import sys

print(format(Decimal(sys.argv[1]) * 100, ".2f").rstrip("0").rstrip("."))
PY
}

if [[ "$QUERY" == @* ]]; then
    QUERY_FILE="${QUERY:1}"
    if [[ ! -f "$QUERY_FILE" ]]; then
        echo "ERROR: Query file not found: $QUERY_FILE" >&2
        exit 1
    fi
    QUERY=$(cat "$QUERY_FILE")
fi

# Binary lookup: prefer PATH, fall back to ~/.local/bin/agy.
if command -v agy &>/dev/null; then
    AGY_BIN="$(command -v agy)"
elif [[ -x "$HOME/.local/bin/agy" ]]; then
    AGY_BIN="$HOME/.local/bin/agy"
else
    echo "ERROR: agy CLI not found. Install: curl -fsSL https://antigravity.google/cli/install.sh | bash" >&2
    exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# All agy invocations share the converter resource, including help and probes.
_lock="${GEMINI_PDF_LOCK_DIR:-${TMPDIR:-/tmp}/gemini-pdf-agy.lock}"; _lock_pid="$(cat "$_lock/pid" 2>/dev/null || true)"
if [[ -d "$_lock" && "$_lock_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$_lock_pid" 2>/dev/null; then
    echo "[delegate/antigravity] NOTE: a gemini-pdf conversion holds the agy lock ($_lock); concurrent provider calls may contend; inspect the conversion log if this dispatch stalls" >&2
fi

# Keep the pristine help cap outside the probe/dispatch budget for short callers.
HELP_OUT="$(_timeout 20 "$AGY_BIN" --help 2>&1 || true)"
for flag in "--print" "--print-timeout"; do
    if ! grep -q -- "$flag" <<< "$HELP_OUT"; then
        _fail_before_dispatch "STATUS: FAILED — agy CLI missing required flag '$flag'"
    fi
done
if ! grep -qE -- '(^|[[:space:]])--model([[:space:]]|$)' <<< "$HELP_OUT"; then
    VERIFIED=no
    _fail_before_dispatch "STATUS: FAILED — installed agy has no --model flag; ANTIGRAVITY_MODEL cannot be pinned (run: agy update)"
fi
PROBE_RAN=0
if [[ "$REROUTE" != off || "${ANTIGRAVITY_VERIFY_MODEL:-0}" == 1 ]]; then
    DEADLINE=$((SECONDS + 10#$TIMEOUT))
    RESERVE=$((10#$TIMEOUT / 2))
    if (( RESERVE > 30 )); then RESERVE=30; fi
fi

if [[ "$REROUTE" != off ]]; then
    QUOTA=unknown; QUOTA_OUT=""; POOL="$FAMILY"; FRAC=""
    _probe_cap
    # Keep exactly one timeout layer: the helper kills the whole agy group and closes
    # its pipe; an outer Perl timeout would kill that supervisor and orphan agy.
    if (( PROBE_CAP >= 2 )); then
        PROBE_RAN=1
    fi
    if [[ "$PROBE_RAN" == 1 ]] && QUOTA_OUT="$(AGY_BIN="$AGY_BIN" AGY_QUOTA_TIMEOUT="$PROBE_CAP" bash "$SCRIPT_DIR/agy_quota.sh")"; then
        GEMINI_FRAC=""; CLAUDE_FRAC=""
        while IFS=$'\t' read -r pool fraction buckets; do
            case "$pool" in gemini) GEMINI_FRAC="$fraction" ;; claude) CLAUDE_FRAC="$fraction" ;; esac
        done <<< "$QUOTA_OUT"
        GEMINI_FIELD="?"; CLAUDE_FIELD="?"
        if [[ -n "$GEMINI_FRAC" ]]; then GEMINI_FIELD="$(_fraction4 "$GEMINI_FRAC")"; fi
        if [[ -n "$CLAUDE_FRAC" ]]; then CLAUDE_FIELD="$(_fraction4 "$CLAUDE_FRAC")"; fi
        QUOTA="gemini:$GEMINI_FIELD,claude:$CLAUDE_FIELD"
        if [[ "$POOL" == gemini ]]; then FRAC="$GEMINI_FRAC"; else FRAC="$CLAUDE_FRAC"; fi
    fi
    if [[ -z "$FRAC" ]]; then
        echo "[delegate/antigravity] WARN: quota probe failed, skipped, or lacked the $POOL pool; dispatching as pinned ($MODEL)" >&2
    elif LC_ALL=C awk -v fraction="$FRAC" -v threshold="$LOW_QUOTA" 'BEGIN {exit !(fraction < threshold)}'; then
        if [[ "$REROUTE" == refuse ]]; then
            _fail_before_dispatch "STATUS: FAILED — quota-low: $POOL pool at $(_percent "$FRAC")% (< $(_percent "$LOW_QUOTA")%); dispatch refused (--reroute refuse)" 75
        elif [[ "$CANON" != gemini-3.8-flash-high ]]; then
            MODEL=gemini-3.8-flash-high; FAMILY=gemini
            REROUTE_FIELD="quota-low:$POOL:$(_fraction4 "$FRAC")"
            echo "[delegate/antigravity] WARN: rerouting $REQUESTED to $MODEL: $POOL pool at $(_percent "$FRAC")% (< $(_percent "$LOW_QUOTA")% threshold)" >&2
        fi
    fi
fi

_verify_model() {
    [[ "${ANTIGRAVITY_VERIFY_MODEL:-0}" == 1 ]] || return 0
    _probe_cap
    (( PROBE_CAP >= 2 )) || _deadline_failure 'for the identity probe'
    VERIFIED=no
    local probe_out probe_err probe_rc=0 probe_id="" probe_label="" probe_response
    probe_out="$(mktemp "${OUTPUT}.probe.XXXXXX")"
    probe_err="$(mktemp "${OUTPUT}.probe-err.XXXXXX")"
    PROBE_RAN=1
    _timeout "$PROBE_CAP" "$AGY_BIN" --model "$MODEL" -p "/model" --output-format json >"$probe_out" 2>"$probe_err" </dev/null || probe_rc=$?
    if [[ "$probe_rc" == 0 ]]; then
        if command -v jq >/dev/null 2>&1; then
            probe_id="$(jq -er 'select(.status == "SUCCESS") | .command.data.id | select(type == "string")' "$probe_out" 2>/dev/null || true)"
            probe_label="$(jq -er 'select(.status == "SUCCESS") | .command.data.label | select(type == "string")' "$probe_out" 2>/dev/null || true)"
        else
            probe_response="$(python3 -c 'import json, sys; d=json.load(sys.stdin); r=d.get("response", "") if d.get("status") == "SUCCESS" else ""; print(r.splitlines()[0] if isinstance(r, str) and r else "")' <"$probe_out" 2>/dev/null || true)"
            IFS=$'\t' read -r probe_id probe_label <<< "$probe_response"
        fi
    fi
    rm -f "$probe_out" "$probe_err"
    if [[ "$probe_rc" == 124 ]]; then
        _fail_before_dispatch "STATUS: FAILED — model identity probe timed out after ${PROBE_CAP}s" 124
    elif [[ "$probe_rc" != 0 ]]; then
        _fail_before_dispatch "STATUS: FAILED — model identity probe failed: exit $probe_rc"
    elif [[ -z "$probe_id" && -z "$probe_label" ]]; then
        _fail_before_dispatch "STATUS: FAILED — model identity probe failed: unparseable model response"
    elif [[ "$probe_id" != "$MODEL" && "$probe_label" != "$MODEL" ]]; then
        _fail_before_dispatch "STATUS: FAILED — model identity mismatch: requested $MODEL, reported ${probe_id:-?}"
    fi
    VERIFIED=yes
}

_dispatch() {
    echo "[delegate/antigravity] Model: $MODEL (verified=$VERIFIED)" >&2
    # Without a probe, preserve the pristine timeout bytes regardless of setup time.
    DISPATCH_TIMEOUT=$TIMEOUT
    if [[ "$PROBE_RAN" == 1 ]]; then
        _remaining
        (( REMAINING >= 1 )) || _deadline_failure
        DISPATCH_TIMEOUT=$REMAINING
    fi
    local inner_timeout="${DISPATCH_TIMEOUT}s"
    if (( 10#$DISPATCH_TIMEOUT >= 30 )); then inner_timeout="$((10#$DISPATCH_TIMEOUT - 10))s"; fi
    local agy_cmd=("$AGY_BIN" --model "$MODEL" --print "$QUERY" --print-timeout "$inner_timeout")
    if [[ "$YOLO" == 1 ]]; then agy_cmd+=(--dangerously-skip-permissions); fi
    EXIT_CODE=0; HEADER_EXIT=0; FAILURE_LINE=""
    _timeout "$DISPATCH_TIMEOUT" "${agy_cmd[@]}" </dev/null > "$OUTPUT" 2> "$ERR_LOG" || EXIT_CODE=$?
    HEADER_EXIT=$EXIT_CODE
    if [[ "$EXIT_CODE" != 0 ]]; then
        FAILURE_LINE="$(_classify_failure "$ERR_LOG" "$EXIT_CODE" "$DISPATCH_TIMEOUT")"
    elif [[ ! -s "$OUTPUT" ]]; then
        FAILURE_LINE="$(_classify_failure "$ERR_LOG" 0 "$DISPATCH_TIMEOUT")"
        EXIT_CODE=1; HEADER_EXIT=0-empty
        echo "[delegate/antigravity] WARN: agy exited 0 but produced empty output. Classified failure written to $OUTPUT" >&2
    fi
}

_verify_model

echo "[delegate/antigravity] Sending query ($(echo "$QUERY" | wc -c | tr -d ' ') chars)..." >&2
echo "[delegate/antigravity] Output: $OUTPUT" >&2
_dispatch
if [[ "$PROBE_RAN" == 1 ]]; then _remaining; else REMAINING=0; fi
if [[ "$EXIT_CODE" != 0 && "$REROUTE" == auto && "$FAMILY" == claude &&
      "$FAILURE_LINE" == 'STATUS: FAILED — Antigravity quota exhausted or rate-limited' ]] && (( REMAINING >= 120 )); then
    FIRST_ERR="$(mktemp "${ERR_LOG}.first.XXXXXX")"
    mv -f "$ERR_LOG" "$FIRST_ERR"
    MODEL=gemini-3.8-flash-high; FAMILY=gemini; VERIFIED=skipped
    REROUTE_FIELD=quota-failure:claude
    echo "[delegate/antigravity] WARN: $REQUESTED exhausted the Claude pool; retrying once on $MODEL" >&2
    _verify_model
    _dispatch
fi
# Classify only the final stderr, then preserve the first failure for forensics.
_append_first_error
TMP="$(mktemp "${OUTPUT}.tmp.XXXXXX")"
{
    _header "$HEADER_EXIT"
    echo ""
    if [[ -n "$FAILURE_LINE" ]]; then echo "$FAILURE_LINE"; echo ""; fi
    cat "$OUTPUT"
} > "$TMP"
mv -f "$TMP" "$OUTPUT"
if [[ "$EXIT_CODE" != 0 ]]; then
    echo "ERROR: $FAILURE_LINE (see $ERR_LOG)" >&2
else
    RESULT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
    echo "[delegate/antigravity] Done. Result: $RESULT_SIZE bytes → $OUTPUT (stderr: $ERR_LOG)" >&2
fi
echo "$OUTPUT"
exit "$EXIT_CODE"
