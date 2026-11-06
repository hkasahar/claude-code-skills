#!/bin/bash
# Binary/flag checks by default. --live additionally invokes authenticated CLIs.
set -euo pipefail
MODE="${1:---offline}"
[[ $# -le 1 && ( "$MODE" == --offline || "$MODE" == --live ) ]] || {
    echo 'Usage: preflight.sh [--offline|--live]' >&2; exit 2;
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS=0
_timeout() {
    perl -e '
        use POSIX qw(setsid);
        my $seconds=shift; my $pid=fork // die "fork: $!";
        if (!$pid) { setsid() >= 0 or die "setsid: $!"; exec @ARGV; die "exec: $!" }
        $SIG{ALRM}=sub { kill "TERM", -$pid; kill "KILL", -$pid; waitpid($pid,0); exit 124 };
        alarm $seconds; waitpid($pid,0); exit ($? & 127 ? 128+($? & 127) : $? >> 8);
    ' "$@"
}
_check() {
    local name="$1" bin="$2" required="$3" found help flag; shift 3
    found="$(command -v "$bin" 2>/dev/null || true)"
    [[ -n "$found" || ! -x "$HOME/.local/bin/$bin" ]] || found="$HOME/.local/bin/$bin"
    if [[ -z "$found" ]]; then
        if [[ "$required" == 1 ]]; then echo "FAIL  $name CLI missing" >&2; STATUS=1
        else echo "NOTE  $name optional CLI missing" >&2; fi
        return 0
    fi
    if [[ "$bin" == codex ]]; then help="$(_timeout 20 "$found" exec --help 2>&1 || true)"
    else help="$(_timeout 20 "$found" --help 2>&1 || true)"; fi
    for flag in "$@"; do
        if ! grep -qF -- "$flag" <<< "$help"; then
            if [[ "$required" == 1 ]]; then echo "FAIL  $name missing flag $flag" >&2; STATUS=1
            else echo "WARN  $name optional CLI missing flag $flag" >&2; fi
            return 0
        fi
    done
    echo "OK    $name binary/flags: $found" >&2
}
echo "Delegate preflight ($MODE)" >&2
_check Codex codex 1 --model --output-last-message --skip-git-repo-check
_check Antigravity agy 1 --print --model --print-timeout
_check Grok grok 0 --prompt-file --deny --no-subagents --disable-web-search
_check Claude claude 0 --print --tools --no-session-persistence
# DeepSeek has no required CLI; do not inspect keys or call HTTP in offline mode.
for dep in perl python3 ps; do
    if ! command -v "$dep" >/dev/null 2>&1; then echo "FAIL  local dependency missing: $dep" >&2; STATUS=1; fi
done
for dep in curl jq; do
    command -v "$dep" >/dev/null 2>&1 || echo "NOTE  optional API dependency missing: $dep" >&2
done
# Marketplace and manual layouts use sibling skills; explicit overrides take priority.
if [[ -n "${GEMINI_PDF_SCRIPT:-}" ]]; then CONVERTER="$GEMINI_PDF_SCRIPT"
elif [[ -f "$SCRIPT_DIR/../../gemini-pdf/scripts/pdf_to_markdown.sh" ]]; then CONVERTER="$SCRIPT_DIR/../../gemini-pdf/scripts/pdf_to_markdown.sh"
else CONVERTER="$HOME/.claude/skills/gemini-pdf/scripts/pdf_to_markdown.sh"; fi
if [[ -f "$CONVERTER" ]]; then echo "OK    optional PDF converter: $CONVERTER" >&2
else echo "NOTE  optional PDF converter missing: $CONVERTER" >&2; fi
if [[ "$MODE" == --live ]]; then
    echo 'LIVE: authenticated provider probes follow; they may use quota or incur charges.' >&2
    if [[ "$STATUS" != 0 ]]; then echo 'FAIL  refusing live probes because required local checks failed' >&2; exit "$STATUS"; fi
    RUN="$(mktemp -d "${TMPDIR:-/tmp}/delegate-preflight.XXXXXX")"
    trap 'rm -rf "$RUN"' EXIT
    # Small explicit probes, with no credit fallback or silent model reroute.
    if CODEX_TIMEOUT=60 CODEX_CAPACITY_RETRIES=0 bash "$SCRIPT_DIR/ask_codex.sh" --credit-fallback off 'Reply with exactly: PREFLIGHT_OK' "$RUN/codex.md" >/dev/null &&
       grep -q PREFLIGHT_OK "$RUN/codex.md"; then echo 'OK    Codex live response' >&2
    else echo 'WARN  Codex live probe failed; inspect login/model/capacity before dispatch' >&2; fi
    if ANTIGRAVITY_TIMEOUT=60 bash "$SCRIPT_DIR/ask_antigravity.sh" --reroute off 'Reply with exactly: PREFLIGHT_OK' "$RUN/agy.md" >/dev/null &&
       grep -q PREFLIGHT_OK "$RUN/agy.md"; then echo 'OK    Antigravity live response' >&2
    else echo 'WARN  Antigravity live probe failed; inspect login/model/capacity before dispatch' >&2; fi
fi
exit "$STATUS"
