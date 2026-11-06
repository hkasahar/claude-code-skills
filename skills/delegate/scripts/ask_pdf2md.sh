#!/bin/bash
# ask_pdf2md.sh — Delegate PDF conversion and write a compact status envelope without reading the resulting Markdown.
# See ../references/legs.md for modes, dependencies, costs and limitations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PDF2MD_CONVERTER:-}" ]]; then CONVERTER="$PDF2MD_CONVERTER"
elif [[ -f "$SCRIPT_DIR/../../gemini-pdf/scripts/pdf_to_markdown.sh" ]]; then CONVERTER="$SCRIPT_DIR/../../gemini-pdf/scripts/pdf_to_markdown.sh"
else CONVERTER="$HOME/.claude/skills/gemini-pdf/scripts/pdf_to_markdown.sh"; fi
# Run only in the background: wait must be interruptible by the wrapper traps.
_timeout() {
    local secs="$1"; shift
    exec perl -e '
        use POSIX ();
        use Errno qw(EINTR);
        my $secs = shift @ARGV;
        my $pgid_file = shift @ARGV;
        $SIG{TERM} = $SIG{INT} = "DEFAULT";
        pipe(my $ready_read, my $ready_write) or die "pipe: $!";
        pipe(my $go_read, my $go_write) or die "pipe: $!";
        my $pid = fork();
        die "fork: $!" unless defined $pid;
        if ($pid == 0) {
            close $ready_read; close $go_write;
            POSIX::setsid() != -1 or die "setsid: $!";
            syswrite($ready_write, "R") == 1 or die "ready: $!";
            close $ready_write;
            my $go;
            exit 1 unless sysread($go_read, $go, 1) == 1 && $go eq "G";
            close $go_read;
            exec @ARGV or die "exec: $!";
        }
        close $ready_write; close $go_read;
        my $ready;
        sysread($ready_read, $ready, 1) == 1 or die "child not ready";
        close $ready_read;
        # Dispatch only after the wrapper can identify an established group.
        # If cleanup stops us earlier, EOF on the gate makes the child exit.
        open(my $pgid, ">", $pgid_file) or die "pgid: $!";
        print {$pgid} "$pid\n" or die "pgid write: $!";
        close $pgid or die "pgid close: $!";
        my $timed_out = 0;
        $SIG{ALRM} = sub {
            $timed_out = 1;
            kill "TERM", -$pid;
            sleep 2;
            kill "KILL", -$pid;
        };
        alarm $secs;
        syswrite($go_write, "G") == 1 or die "dispatch: $!";
        close $go_write;
        my $r;
        do { $r = waitpid($pid, 0); } while ($r == -1 && $! == EINTR);
        alarm 0;
        exit 124 if $timed_out;
        my $st = $?;
        exit(128 + ($st & 127)) if ($st & 127);
        exit($st >> 8);
    ' "$secs" "$PGID_FILE" "$@"
}
_stop_converter() {
    # Stop publication/dispatch first; the gate prevents a late untracked child.
    local watchdog="$WATCHDOG_PID" pgid=""
    [[ "$watchdog" != pending ]] || watchdog="${!:-}"
    if [[ -n "$watchdog" ]]; then
        kill -TERM "$watchdog" 2>/dev/null || true
        wait "$watchdog" 2>/dev/null || true
        WATCHDOG_PID=""
    fi
    if [[ -s "$PGID_FILE" ]]; then
        read -r pgid < "$PGID_FILE" || true
        if [[ "$pgid" =~ ^[1-9][0-9]*$ ]]; then
            perl -e '
                my $pgid = shift;
                kill "TERM", -$pgid;
                for (1..50) {
                    last unless kill 0, -$pgid;
                    select undef, undef, undef, 0.1;
                }
                kill "KILL", -$pgid;
            ' "$pgid" || true
        fi
    fi
}
_cleanup() {
    local rc=$?
    set +e
    trap - EXIT
    trap "" INT TERM
    _stop_converter
    rm -f ${ENVELOPE_TMP:+"$ENVELOPE_TMP"}
    ENVELOPE_TMP=""
    if [[ -n "$INTERRUPTED" ]]; then
        EXIT="$rc"; ELAPSED=$((SECONDS - START))
        STATUS_LINE="STATUS: FAILED — pdf2md interrupted ($INTERRUPTED); converter group killed"
        _write_envelope
        echo "[delegate/pdf2md] $STATUS_LINE (${ELAPSED}s) → $STATUS_FILE" >&2
        printf '%s\n%s\n' "$STATUS_LINE" "$STATUS_FILE"
    fi
    rm -f "$STAMP" "$PGID_FILE" ${TMP_OUT:+"$TMP_OUT"} ${ENVELOPE_TMP:+"$ENVELOPE_TMP"}
    exit "$rc"
}
_interrupted() { INTERRUPTED="$1"; exit "$2"; }
_usage_error() { echo "ERROR: $*" >&2; echo 'Usage: ask_pdf2md.sh INPUT.pdf [OUTPUT.md] [converter flags...]' >&2; exit 2; }
_integer() { [[ "$2" =~ ^[0-9]+$ ]] || _usage_error "$1 must be an integer"; }
_jq() { [[ ( "$1" == "$AUDIT_JSON" && "$AUDIT_FRESH" == 1 ) || ( "$1" == "$QUALITY_JSON" && "$QUALITY_FRESH" == 1 ) ]] && command -v jq >/dev/null 2>&1 && jq -r "$2 // empty" "$1" 2>/dev/null || true; }
_classify_failure() {
    local diag; diag="$( { head -40 "$LOG"; tail -40 "$LOG"; } 2>/dev/null)"
    if [[ "$EXIT" == 124 ]]; then echo "STATUS: FAILED — pdf2md watchdog fired after ${TIMEOUT}s (converter killed; partial files may exist)"
    elif grep -qiE 'operation not permitted|bind: address' <<< "$diag"; then echo 'STATUS: FAILED — Sandbox blocked agy TCP bind. Re-run with dangerouslyDisableSandbox=true'
    elif grep -qiE 'unauthenticated|please log in|not.{0,5}authenticated|auth.{0,10}required' <<< "$diag"; then echo 'STATUS: FAILED — Antigravity auth not configured. Run `agy` interactively to log in'
    elif grep -qiE 'quota|rate.?limit|429|too many requests' <<< "$diag"; then echo 'STATUS: FAILED — Antigravity quota exhausted or rate-limited'
    elif grep -qiE 'invalid model selection|not recognized as a known model|unknown model|model not.{0,10}(found|supported|available)|404' <<< "$diag"; then echo 'STATUS: FAILED — model unavailable: check `agy models` against GEMINI_PDF_MODEL/GEMINI_PDF_AUDIT_MODEL'
    elif grep -qiE 'agy.{0,20}not found' <<< "$diag"; then echo 'STATUS: FAILED — agy CLI missing (native + audit path); install agy'
    elif grep -qiE 'waiting for agy lock' <<< "$diag"; then echo 'STATUS: FAILED — agy lock wait exceeded (another conversion running?); see LOG'
    else echo "STATUS: FAILED — converter exited $EXIT; last log lines: $(tail -3 "$LOG" | tr '\n' ' ')"; fi
}
_detail_status() {
    # Shorten the reason first so the status still includes the spend and totals.
    local room=$((160 - ${#1} - ${#3})) detail="$2"
    if (( ${#detail} > room && room >= 3 )); then detail="${detail:0:$((room - 3))}..."; fi
    printf '%s%s%s\n' "$1" "$detail" "$3"
}
_vault_line() {
    [[ -n "$BIB_KEY" ]] || { echo 'VAULT: not requested (no --bib-key)'; return; }
    local ref="${GEMINI_PDF_REFERENCE_DIR:-${REFERENCE_DIR:-}}" dest note=""
    [[ -n "$ref" ]] || { echo 'VAULT: not configured (explicit reference destination required)'; return; }
    dest="$ref/md/$BIB_KEY.md"
    [[ ! -f "$ref/pdf/md/$BIB_KEY.md" ]] || note=" WARN: $ref/pdf/md/$BIB_KEY.md exists and shadows this conversion (pdf/md/ takes precedence)."
    if [[ -f "$dest" ]] && cmp -s "$dest" "$FINAL_MD"; then echo "VAULT: synced --bib-key $BIB_KEY → $dest; refresh your separately installed reference index if needed.$note"
    elif [[ -f "$dest" ]]; then echo "VAULT: existing $dest kept (differs; pass --force-sync to replace).$note"
    else echo "VAULT: $dest not present after run — see LOG for the sync error.$note"; fi
}
_write_envelope() {
    ENVELOPE_TMP="$(mktemp "${STATUS_FILE}.tmp.XXXXXX")"
    {
        echo "<!-- ask_pdf2md.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT | model=${MODEL_R:-?} | audit_model=${AUDIT_MODEL_R:-?} | mathpix_pages=${MPX_USED:-?}/${MPX_CAP:-?} | elapsed=${ELAPSED}s -->"
        echo ''; echo "$STATUS_LINE"
        if [[ "$EXIT" == 2 && "$VERDICT" == failed ]]; then
            echo "OUTPUT: $FINAL_MD (not trustworthy; OUTPUT.md not written)${PREVIOUS:+; previous output preserved at $PREVIOUS}"
        elif [[ -f "$FINAL_MD" && "$OUTPUT_FRESH" == 1 ]]; then echo "OUTPUT: $FINAL_MD ($(wc -c < "$FINAL_MD" | tr -d ' ') bytes; quality ${QUALITY:-?}/100)"
        elif [[ -f "$FINAL_MD" ]]; then echo "OUTPUT: $FINAL_MD (STALE — predates this run)"
        else echo 'OUTPUT: not produced'; fi
        echo "${AUDIT_LINE:-AUDIT: unavailable (see LOG)}"
        echo "RESIDUAL: $RESIDUAL_LINE"; echo "MATHPIX: $MATHPIX_LINE"
        echo "SIDECARS: $AUDIT_JSON $QUALITY_JSON ${OUTPUT%.md}.meta.json"
        echo "LOG: $LOG"; _vault_line
    } > "$ENVELOPE_TMP"
    mv -f "$ENVELOPE_TMP" "$STATUS_FILE"
    ENVELOPE_TMP=""
}

INPUT="${1:-}"; [[ -n "$INPUT" ]] || _usage_error 'input required'
[[ -f "$INPUT" ]] || _usage_error "input not found: $INPUT"
[[ -f "$CONVERTER" ]] || _usage_error "converter not found: $CONVERTER"
shift
if [[ $# -gt 0 && "$1" != -* ]]; then OUTPUT="$1"; shift
else OUTPUT="${TMPDIR:-/tmp}/claude/pdf2md/$(basename "${INPUT%.pdf}").md"; fi
PASS=("$@"); BIB_KEY=""
export GEMINI_PDF_DEADLINE="${GEMINI_PDF_DEADLINE:-5400}"
# Flags win over env before watchdog sizing, but remain intact for the converter.
for ((i=0; i<${#PASS[@]}; i++)); do
    case "${PASS[$i]}" in
        --deadline|--mathpix-page-cap)
            (( i+1 < ${#PASS[@]} )) || _usage_error "${PASS[$i]} requires an integer"
            value="${PASS[$((i+1))]}"; _integer "${PASS[$i]}" "$value"
            if [[ "${PASS[$i]}" == --deadline ]]; then export GEMINI_PDF_DEADLINE="$((10#$value))"
            else export GEMINI_PDF_MATHPIX_PAGE_CAP="$((10#$value))"; fi ;;
        --bib-key) if (( i+1 < ${#PASS[@]} )); then BIB_KEY="$(basename "${PASS[$((i+1))]}")"; fi ;;
    esac
done
_integer GEMINI_PDF_DEADLINE "$GEMINI_PDF_DEADLINE"; export GEMINI_PDF_DEADLINE="$((10#$GEMINI_PDF_DEADLINE))"
_integer GEMINI_PDF_MATHPIX_PAGE_CAP "${GEMINI_PDF_MATHPIX_PAGE_CAP:-20}"
TIMEOUT="${PDF2MD_TIMEOUT:-$((GEMINI_PDF_DEADLINE + 3600))}"
_integer PDF2MD_TIMEOUT "$TIMEOUT"; TIMEOUT="$((10#$TIMEOUT))"
(( TIMEOUT >= GEMINI_PDF_DEADLINE + 300 )) || _usage_error "PDF2MD_TIMEOUT ($TIMEOUT) must be >= GEMINI_PDF_DEADLINE+300"
mkdir -p "$(dirname "$OUTPUT")"; OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"
STATUS_FILE="${OUTPUT%.md}.status.md"; LOG="${OUTPUT%.md}.pdf2md.log"
# Sidecars belong to the requested stem even if stdout names its .failed.md.
AUDIT_JSON="${OUTPUT%.md}.audit.json"; QUALITY_JSON="${OUTPUT%.md}.quality.json"
STAMP="$(mktemp)"; TMP_OUT=""; PGID_FILE="$STAMP.pgid"
WATCHDOG_PID=""; INTERRUPTED=""; ENVELOPE_TMP=""
START=$SECONDS; ELAPSED=0; EXIT=0; FINAL_MD="$OUTPUT"; AUDIT_LINE=""; VERDICT=""; QUALITY=""; PREVIOUS=""
OUTPUT_FRESH=0; AUDIT_FRESH=0; QUALITY_FRESH=0
MPX_CAP="${GEMINI_PDF_MATHPIX_PAGE_CAP:-20}"; RESIDUAL_LINE="see $AUDIT_JSON"; MATHPIX_LINE="see $AUDIT_JSON"
trap _cleanup EXIT
trap '_interrupted SIGINT 130' INT
trap '_interrupted SIGTERM 143' TERM
echo "[delegate/pdf2md] CONFIDENTIALITY: external dispatch to Google (agy) and, for failing pages, Mathpix: $(basename "$INPUT"). Referee manuscripts, unpublished drafts, reference letters, and student work need explicit clearance before conversion." >&2
if ! python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); s.close()' 2>/dev/null; then
    EXIT=1; STATUS_LINE='STATUS: FAILED — Sandbox blocked TCP bind (agy needs it). Re-run with dangerouslyDisableSandbox=true'
    _write_envelope; printf '%s\n%s\n' "$STATUS_LINE" "$STATUS_FILE"; exit 1
fi
command -v agy >/dev/null 2>&1 || [[ -x "$HOME/.local/bin/agy" ]] || echo '[delegate/pdf2md] NOTE: agy not found — converter will fall back per its own rules (audit impossible)' >&2
[[ "$MPX_CAP" == 0 || ( -n "${MATHPIX_APP_ID:-}" && -n "${MATHPIX_APP_KEY:-}" ) ]] || echo '[delegate/pdf2md] NOTE: no Mathpix credentials — the repair leg cannot escalate; a chunk still failing after repair ships marked (PARTIAL — residual pp. …)' >&2
LOCK="${GEMINI_PDF_LOCK_DIR:-${TMPDIR:-/tmp}/gemini-pdf-agy.lock}"; OWNER="$(cat "$LOCK/pid" 2>/dev/null || true)"
if [[ -d "$LOCK" && "$OWNER" =~ ^[1-9][0-9]*$ ]] && kill -0 "$OWNER" 2>/dev/null; then echo "[delegate/pdf2md] NOTE: another agy run holds $LOCK — this dispatch queues behind it" >&2; fi
echo "[delegate/pdf2md] $INPUT → $OUTPUT (deadline ${GEMINI_PDF_DEADLINE}s, watchdog ${TIMEOUT}s, log $LOG)" >&2
TMP_OUT="$(mktemp)"
WATCHDOG_PID=pending
_timeout "$TIMEOUT" bash "$CONVERTER" "$INPUT" "$OUTPUT" ${PASS[@]+"${PASS[@]}"} </dev/null >"$TMP_OUT" 2>"$LOG" &
WATCHDOG_PID=$!
wait "$WATCHDOG_PID" || EXIT=$?
WATCHDOG_PID=""
ELAPSED=$((SECONDS - START))
LAST="$(grep -v '^[[:space:]]*$' "$TMP_OUT" | tail -1 || true)"; [[ -z "$LAST" || ! -f "$LAST" ]] || FINAL_MD="$LAST"
# Bash 3.2 compares -nt at whole-second precision; immediate outputs need nanoseconds.
read -r OUTPUT_FRESH AUDIT_FRESH QUALITY_FRESH < <(python3 - "$STAMP" "$FINAL_MD" "$AUDIT_JSON" "$QUALITY_JSON" <<'PY'
import os, sys
start = os.stat(sys.argv[1]).st_mtime_ns
def fresh(path):
    try:
        return int(os.stat(path).st_mtime_ns > start)
    except OSError:
        return 0
print(*(fresh(path) for path in sys.argv[2:]))
PY
)
VERDICT="$(_jq "$AUDIT_JSON" '.status')"; AUDIT_LINE="$(_jq "$AUDIT_JSON" '.summary_line')"
[[ -n "$AUDIT_LINE" ]] || AUDIT_LINE="$(grep -E '^(\[gemini-pdf\] )?AUDIT: ' "$LOG" | tail -1 | sed 's/^\[gemini-pdf\] //' || true)"
[[ -n "$VERDICT" ]] || VERDICT="$(sed -nE 's/.*(^| )status=([^ ]+).*/\2/p' <<< "$AUDIT_LINE")"
MODEL_R="$(_jq "$AUDIT_JSON" '.models.convert')"; AUDIT_MODEL_R="$(_jq "$AUDIT_JSON" '.models.audit')"
MPX_USED="$(_jq "$AUDIT_JSON" '.mathpix.pages_used')"; MPX_CAP="$(_jq "$AUDIT_JSON" '.mathpix.cap_pages')"; MPX_CAP="${MPX_CAP:-${GEMINI_PDF_MATHPIX_PAGE_CAP:-20}}"
MPX_USD="$(_jq "$AUDIT_JSON" '.mathpix.est_usd')"; MPX_LEDGER="$(_jq "$AUDIT_JSON" '.mathpix.ledger_path')"
QUALITY="$(_jq "$QUALITY_JSON" '.overall_score')"; DEGR="$(_jq "$AUDIT_JSON" '[.degradations[]?] | join(", ")')"
RANGES="$(_jq "$AUDIT_JSON" '[.residual[]?.pages] | join(", ")')"
CHUNKS="$(_jq "$AUDIT_JSON" '.totals.chunks')"; VERIFIED="$(_jq "$AUDIT_JSON" '.totals.verified')"
UNAUDITED="$(_jq "$AUDIT_JSON" '.totals.unaudited')"; UNVERIFIED="$(_jq "$AUDIT_JSON" '.totals.unverified')"
FAILED="$(_jq "$AUDIT_JSON" '.totals.failed')"; REPAIRED="$(_jq "$AUDIT_JSON" '.totals.repaired')"
AUDIT_ELAPSED="$(_jq "$AUDIT_JSON" '.deadline.elapsed_s')"; DEADLINE_HIT="$(_jq "$AUDIT_JSON" '.deadline.hit | tostring')"
if [[ -n "$VERDICT" && -n "$(_jq "$AUDIT_JSON" '.status')" ]]; then
    RESIDUAL_LINE="$(_jq "$AUDIT_JSON" '[.residual[]? | "pp. \(.pages) (chunk \(.chunk): \(.reason))"] | join("; ")')"; RESIDUAL_LINE="${RESIDUAL_LINE:-none}"
    MPX_RANGES="$(_jq "$AUDIT_JSON" '[.chunks[]? | select(.final_source == "mathpix") | "\(.first_page)-\(.last_page)"] | join(", ")')"
    MATHPIX_LINE="${MPX_USED:-?} pages${MPX_RANGES:+ (pp. $MPX_RANGES)}, \$${MPX_USD:-?} est.; ledger ${MPX_LEDGER:-${GEMINI_PDF_MATHPIX_LEDGER:-$HOME/.claude/delegate-logs/mathpix_ledger.tsv}} (cap $MPX_CAP; 0 disables)"
fi
if [[ "$MPX_CAP" == 0 ]]; then MPX='mathpix disabled (cap=0)'
elif [[ -n "$MPX_USED" ]]; then MPX="mathpix $MPX_USED/$MPX_CAP pages \$${MPX_USD:-?}"
else MPX="mathpix ?/$MPX_CAP pages (see ledger)"; fi
case "$EXIT:$VERDICT" in
    0:verified) STATUS_LINE="STATUS: DONE — verified: chunks=${CHUNKS:-?} repaired=${REPAIRED:-?}; $MPX; elapsed ${AUDIT_ELAPSED:-$ELAPSED}s" ;;
    0:disabled) STATUS_LINE="STATUS: DONE — unaudited (audit disabled): quality ${QUALITY:-?}/100; $MPX" ;;
    0:degraded|3:degraded)
        PREFIX='STATUS: PARTIAL — degraded ('; [[ "$EXIT" != 3 ]] || PREFIX='STATUS: FAILED — strict (exit 3): degraded ('
        STATUS_LINE="$(_detail_status "$PREFIX" "${DEGR:-unspecified}" "): chunks=${CHUNKS:-?} verified=${VERIFIED:-?} unaudited=${UNAUDITED:-?}; $MPX")" ;;
    0:partial|4:partial)
        PREFIX='STATUS: PARTIAL — residual pp. '; [[ "$EXIT" != 4 ]] || PREFIX='STATUS: FAILED — strict (exit 4): residual pp. '
        STATUS_LINE="$(_detail_status "$PREFIX" "${RANGES:-?}" ": chunks=${CHUNKS:-?} verified=${VERIFIED:-?} unverified=${UNVERIFIED:-?}; $MPX")" ;;
    2:failed)
        [[ "$FINAL_MD" != "$OUTPUT" ]] || FINAL_MD="${OUTPUT%.md}.failed.md"
        PREVIOUS="$(sed -n 's/.*WARN: previous output preserved at //p' "$LOG" | tail -1)"
        STATUS_LINE="STATUS: FAILED — audit failed: chunks=${CHUNKS:-?} failed=${FAILED:-?}; $MPX" ;;
    0:*) STATUS_LINE="STATUS: PARTIAL — no AUDIT summary (converter predates the audit route?): quality ${QUALITY:-?}/100 — see LOG" ;;
    *) STATUS_LINE="$(_classify_failure)" ;;
esac
# Keep every status to one compact line, including multi-line error diagnostics.
STATUS_LINE="$(printf '%s' "$STATUS_LINE" | tr '\r\n' '  ')"
(( ${#STATUS_LINE} <= 160 )) || STATUS_LINE="${STATUS_LINE:0:157}..."
_write_envelope
echo "[delegate/pdf2md] $STATUS_LINE (${ELAPSED}s) → $STATUS_FILE" >&2
printf '%s\n%s\n' "$STATUS_LINE" "$STATUS_FILE"
exit "$EXIT"
