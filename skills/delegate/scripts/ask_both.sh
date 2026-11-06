#!/bin/bash
# Parallel independent review: Codex + Gemini by default; Grok/Claude are explicit.
# Fixed slots: QUERY CODEX_OUT GROK_OUT GEMINI_OUT [CLAUDE_OUT]. Empty Grok slot when off.
set -euo pipefail
_fail() { echo "ERROR: $*" >&2; exit 2; }
for dependency in perl ps; do
    command -v "$dependency" >/dev/null 2>&1 || _fail "required local dependency missing: $dependency"
done
[[ -z "${DEEPSEEK_RESERVE_FOR:-}" ]] || _fail 'DEEPSEEK_RESERVE_FOR is unsupported in this public package'
[[ "${DEEPSEEK_SHADOW:-0}" == 0 ]] || _fail 'DEEPSEEK_SHADOW is unsupported in this public package'
[[ "${CODEX_MODEL:-}" != deepseek/* ]] || _fail "review Codex roles never fall back to DeepSeek (CODEX_MODEL=$CODEX_MODEL)"
INCLUDE_GROK="${INCLUDE_GROK:-0}"; INCLUDE_CLAUDE="${INCLUDE_CLAUDE:-0}"
[[ "$INCLUDE_GROK" == 0 || "$INCLUDE_GROK" == 1 ]] || _fail 'INCLUDE_GROK must be 0 or 1'
[[ "$INCLUDE_CLAUDE" == 0 || "$INCLUDE_CLAUDE" == 1 ]] || _fail 'INCLUDE_CLAUDE must be 0 or 1'
[[ $# -le 5 ]] || _fail 'Usage: ask_both.sh QUERY [CODEX_OUT] [GROK_OUT] [GEMINI_OUT] [CLAUDE_OUT]'
[[ -n "${1:-}" ]] || _fail 'Usage: ask_both.sh QUERY [CODEX_OUT] [GROK_OUT] [GEMINI_OUT] [CLAUDE_OUT]'
# Exactly two output arguments is also the old (Gemini, Codex) interface.
[[ $# != 3 ]] || _fail 'ambiguous two-output call; use QUERY CODEX_OUT "" GEMINI_OUT (or all Grok slots)'
[[ "$INCLUDE_GROK" == 1 || -z "${3:-}" ]] || _fail 'GROK_OUT must be empty when INCLUDE_GROK=0'
[[ "$INCLUDE_CLAUDE" == 1 || -z "${5:-}" ]] || _fail 'CLAUDE_OUT requires INCLUDE_CLAUDE=1'
[[ "$INCLUDE_GROK" != 1 || "${GROK_DELEGATE_BACKEND:-cli}" != api ]] || _fail 'metered Grok API backend is not allowed in a review round'
E_MODEL="${ANTIGRAVITY_MODEL:-gemini-4-argon-high}"
if [[ ! "$E_MODEL" =~ ^gemini-([0-9]+-argon-(high|medium|low)|[0-9][0-9.]*-pro-(high|low))$ &&
      "$E_MODEL" != gemini-4-argon && "$E_MODEL" != gemini-3.1-pro &&
      "$E_MODEL" != 'Gemini 4 Argon (High)' && "$E_MODEL" != 'Gemini 4 Argon (Medium)' &&
      "$E_MODEL" != 'Gemini 4 Argon (Low)' && "$E_MODEL" != 'Gemini 3.1 Pro (High)' &&
      "$E_MODEL" != 'Gemini 3.1 Pro (Low)' ]]; then
    _fail "Gemini review requires a supported Gemini Pro model (ANTIGRAVITY_MODEL=$E_MODEL)"
fi
unset ANTIGRAVITY_MODEL ANTIGRAVITY_VOTE
SCRIPT_DIR="${DELEGATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
QUERY="$1"
if [[ "$QUERY" == @* ]]; then
    [[ -f "${QUERY:1}" ]] || _fail "query file not found: ${QUERY:1}"
    QUERY="$(cat "${QUERY:1}")"
fi
[[ -n "$QUERY" ]] || _fail 'empty query'
CODEX_OUT="${2:-${TMPDIR:-/tmp}/delegate/codex_cross_$(date +%s)_$$.md}"
GROK_OUT="${3:-${TMPDIR:-/tmp}/delegate/grok_cross_$(date +%s)_$$.md}"
ANTIGRAVITY_OUT="${4:-${TMPDIR:-/tmp}/delegate/gemini_cross_$(date +%s)_$$.md}"
CLAUDE_OUT="${5:-${TMPDIR:-/tmp}/delegate/claude_cross_$(date +%s)_$$.md}"
NAMES=(Codex); SCRIPTS=(ask_codex.sh); FILES=("$CODEX_OUT")
if [[ "$INCLUDE_GROK" == 1 ]]; then NAMES+=(Grok); SCRIPTS+=(ask_grok.sh); FILES+=("$GROK_OUT"); fi
NAMES+=(Gemini); SCRIPTS+=(ask_antigravity.sh); FILES+=("$ANTIGRAVITY_OUT")
if [[ "$INCLUDE_CLAUDE" == 1 ]]; then NAMES+=(Claude); SCRIPTS+=(ask_claude.sh); FILES+=("$CLAUDE_OUT"); fi
for i in "${!FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/${SCRIPTS[$i]}" ]] || _fail "missing requested wrapper: $SCRIPT_DIR/${SCRIPTS[$i]}"
    [[ ! -L "${FILES[$i]}" ]] || _fail "output must not be a symlink: ${FILES[$i]}"
    mkdir -p "$(dirname "${FILES[$i]}")"
    FILES[$i]="$(cd "$(dirname "${FILES[$i]}")" && pwd -P)/$(basename "${FILES[$i]}")"
    for ((j=0; j<i; j++)); do
        if [[ "${FILES[$i]}" == "${FILES[$j]}" || "${FILES[$i]}" -ef "${FILES[$j]}" ]]; then
            _fail "two review legs share the same output path: ${FILES[$i]}"
        fi
    done
done
# Refusals above precede dispatch and stale-file removal.
for file in "${FILES[@]}"; do rm -f "$file"; done
export CODEX_TIMEOUT="${CODEX_TIMEOUT:-3600}" ANTIGRAVITY_TIMEOUT="${ANTIGRAVITY_TIMEOUT:-1800}"
export ANTIGRAVITY_VERIFY_MODEL="${ANTIGRAVITY_VERIFY_MODEL:-1}"
export GROK_DELEGATE_TIMEOUT="${GROK_DELEGATE_TIMEOUT:-1800}"
PIDS=(); EXITS=(); REMAINING=${#FILES[@]}; FAILED=0
# Each wrapper owns a process group, so cancellation includes its CLI descendants.
_group() { exec perl -MPOSIX -e 'POSIX::setsid() >= 0 or die "setsid: $!"; exec @ARGV; die "exec: $!"' "$@"; }
_stop() {
    # A CLI timeout supervisor may create another group; capture descendants
    # before terminating parents so reparenting cannot hide a metered child.
    perl -e '
        my %targets=map { $_ => 1 } @ARGV;
        my @tree;
        open(my $ps, "-|", "ps", "-A", "-o", "pid=", "-o", "ppid=") or die "ps: $!";
        while (<$ps>) { push @tree, [$1,$2] if /^\s*(\d+)\s+(\d+)/ }
        close $ps;
        my $changed=1;
        while ($changed) {
            $changed=0;
            for my $edge (@tree) {
                if ($targets{$edge->[1]} && !$targets{$edge->[0]}) {
                    $targets{$edge->[0]}=1; $changed=1;
                }
            }
        }
        my @ids=keys %targets;
        kill "TERM", -$_ for @ids;
        kill "TERM", @ids;
        select undef, undef, undef, 0.2;
        kill "KILL", -$_ for @ids;
        kill "KILL", @ids;
    ' "${PIDS[@]+"${PIDS[@]}"}" 2>/dev/null || true
    local pid
    for pid in "${PIDS[@]+"${PIDS[@]}"}"; do wait "$pid" 2>/dev/null || true; done
}
trap '_stop; exit 130' INT
trap '_stop; exit 143' TERM HUP
for i in "${!FILES[@]}"; do
    echo "[delegate/both] ${NAMES[$i]} -> ${FILES[$i]}" >&2
    case "${NAMES[$i]}" in
        Codex) CODEX_MODEL="${CODEX_MODEL:-gpt-6-astra}" CODEX_EFFORT="${CODEX_EFFORT:-max}" \
            _group bash "$SCRIPT_DIR/ask_codex.sh" --credit-fallback off "$QUERY" "${FILES[$i]}" >/dev/null & ;;
        Grok) GROK_DELEGATE_AGENTIC=0 GROK_DELEGATE_WORKDIR= GROK_DELEGATE_BACKEND=cli \
            _group bash "$SCRIPT_DIR/ask_grok.sh" "$QUERY" "${FILES[$i]}" >/dev/null & ;;
        Gemini) ANTIGRAVITY_VOTE=1 ANTIGRAVITY_MODEL="$E_MODEL" ANTIGRAVITY_YOLO=0 \
            _group bash "$SCRIPT_DIR/ask_antigravity.sh" "$QUERY" "${FILES[$i]}" >/dev/null & ;;
        Claude) CLAUDE_DELEGATE_AGENTIC=0 CLAUDE_DELEGATE_RESUME= CLAUDE_DELEGATE_ALLOW_API_KEY=0 \
            _group bash "$SCRIPT_DIR/ask_claude.sh" "$QUERY" "${FILES[$i]}" >/dev/null & ;;
    esac
    PIDS[$i]=$!; EXITS[$i]=pending
done
_verdict_of() {
    local line
    line="$(awk 'NR>1 && NF {print; exit}' "$1" 2>/dev/null || true)"
    if [[ "$line" =~ ^VERDICT:[[:space:]](CORRECT|ERROR|GAP)($|[[:space:]]) ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo UNPARSEABLE
    fi
}
# Poll every requested leg; failure must not hide behind a longer sibling.
while (( REMAINING > 0 )); do
    for i in "${!PIDS[@]}"; do
        [[ "${EXITS[$i]}" == pending ]] || continue
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            code=0; wait "${PIDS[$i]}" || code=$?
            if [[ "$code" == 0 && "$(_verdict_of "${FILES[$i]}")" == UNPARSEABLE ]]; then code=1; fi
            EXITS[$i]="$code"; REMAINING=$((REMAINING-1))
            if [[ "$code" != 0 ]]; then FAILED=1; break; fi
        fi
    done
    [[ "$FAILED" == 0 ]] || break
    (( REMAINING == 0 )) || sleep 0.1
done
if [[ "$FAILED" == 1 ]]; then
    _stop
    for i in "${!FILES[@]}"; do
        if [[ "${EXITS[$i]}" == pending ]]; then
            EXITS[$i]=143
            printf '<!-- ask_both.sh | exit=143 | role=%s -->\n\nSTATUS: FAILED — cancelled because a requested review leg failed\n' "${NAMES[$i]}" > "${FILES[$i]}"
        elif [[ ! -s "${FILES[$i]}" ]]; then
            printf '<!-- ask_both.sh | exit=%s | role=%s -->\n\nSTATUS: FAILED — requested review leg failed without output\n' "${EXITS[$i]}" "${NAMES[$i]}" > "${FILES[$i]}"
        fi
    done
fi
trap - INT TERM HUP
for i in "${!FILES[@]}"; do
    label="$(_verdict_of "${FILES[$i]}")"
    if [[ "${NAMES[$i]}" == Claude ]]; then
        echo "[delegate/both] Claude=$label exit=${EXITS[$i]} (optional investigation aid; never counted as independent)" >&2
    else
        echo "[delegate/both] ${NAMES[$i]}=$label exit=${EXITS[$i]} (advisory)" >&2
    fi
    printf '%s\n' "${FILES[$i]}"
done
echo '[delegate/both] Read every report; resolve each concern from evidence. Agreement is not a correctness certificate.' >&2
[[ "$FAILED" == 0 ]]
