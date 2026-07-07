#!/bin/bash
# ask_claude.sh — Dispatch to an INDEPENDENT Claude Code CLI from inside Claude Code.
# OAuth (subscription) auth by default via a dedicated config dir + token file.
#
# ── WHY NO --bare ─────────────────────────────────────────────────────────────
# Verified 2026-07-07 on Claude Code 2.1.198: under `--bare`, Anthropic auth is
# "strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain
# are never read)". Empirically, --bare + CLAUDE_CODE_OAUTH_TOKEN returns
# "Not logged in" without an API call ever being made; WITHOUT --bare the env
# token IS sent (fake token -> 401 Invalid bearer token) and takes precedence
# over keychain credentials. So this wrapper does NOT use --bare; it replicates
# the hygiene explicitly:
#   * fresh CLAUDE_CONFIG_DIR (no hooks, plugins, auto-memory, user CLAUDE.md)
#   * throwaway mktemp cwd in voter mode (no project CLAUDE.md / settings)
#   * --tools "" in voter mode — zero tool schemas, pure judgment
#   * --no-session-persistence in voter mode
# Do NOT "fix" this back to --bare without re-verifying the CLI's auth semantics.
#
# ── Independence axes ─────────────────────────────────────────────────────────
#   1. Config:  dedicated CLAUDE_CONFIG_DIR (default ~/.claude-delegate) — own
#               auth, settings, session store. Optionally a second subscription.
#   2. Process: ALL CLAUDECODE*/CLAUDE_CODE_* harness vars are unset in the child
#               (the orchestrator ambiently exports CLAUDE_CODE_SESSION_ID,
#               CLAUDE_CODE_EFFORT_LEVEL, CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING,
#               ... — measured 2026-07-07). CLAUDE_DELEGATE_DEPTH blocks
#               delegate-of-delegate recursion.
#   3. Auth:    explicit env token. Priority: token file > inherited
#               CLAUDE_CODE_OAUTH_TOKEN (WARN) > ANTHROPIC_API_KEY (OPT-IN ONLY
#               via CLAUDE_DELEGATE_ALLOW_API_KEY=1 — the orchestrator exports a
#               metered API key ambiently, so an unguarded fallback would
#               silently bill the API). ANTHROPIC_API_KEY is unset whenever an
#               OAuth token is selected.
#   4. Cwd:     voter runs in a throwaway mktemp dir; agentic runs in an
#               explicit, MANDATORY CLAUDE_DELEGATE_WORKDIR. NOTE: workdir
#               scoping applies to file tools only — an allowed Bash tool is NOT
#               jailed to the workdir.
#
# Modes:
#   VOTER (default)                      Pure judgment, zero tools. For
#                                        majority-of-3 votes, proof verification.
#   AGENTIC (CLAUDE_DELEGATE_AGENTIC=1)  Full agent loop with pre-approved tools
#                                        in CLAUDE_DELEGATE_WORKDIR. Symmetric
#                                        with the Codex leg. Prompts are
#                                        trusted-code territory.
#
# Usage:
#   bash ask_claude.sh "your prompt here" [output_file]
#   bash ask_claude.sh @prompt_file.md [output_file]
#   CLAUDE_DELEGATE_AGENTIC=1 CLAUDE_DELEGATE_WORKDIR=~/proj/wt \
#     bash ask_claude.sh @task.md out.md
#   CLAUDE_DELEGATE_AGENTIC=1 CLAUDE_DELEGATE_WORKDIR=~/proj/wt \
#     CLAUDE_DELEGATE_RESUME=<session_id> bash ask_claude.sh "follow-up" out2.md
#
# One-time setup (run in a regular terminal, NOT via `!` inside Claude Code —
# the token must never enter argv, shell history, or a session transcript):
#   CLAUDE_CONFIG_DIR=~/.claude-delegate claude setup-token
#   umask 077
#   cat > ~/.claude-delegate/oauth_token    # paste token, Enter, Ctrl-D
#
# Environment variables — wrapper knobs live in the CLAUDE_DELEGATE_* namespace.
# Legacy CLAUDE_MODEL/CLAUDE_EFFORT/CLAUDE_TIMEOUT/CLAUDE_AGENTIC/... are IGNORED:
# the harness ambiently exports CLAUDE_EFFORT (and more) into every Bash child,
# so un-namespaced knobs would silently take the orchestrator's values.
#   CLAUDE_DELEGATE_CONFIG_DIR     default ~/.claude-delegate
#   CLAUDE_DELEGATE_MODEL          default opus
#   CLAUDE_DELEGATE_EFFORT         default max (applied only if CLI has --effort)
#   CLAUDE_DELEGATE_TIMEOUT        default 570 seconds — deliberately under the
#                                  Bash tool's 600 s default cap so the wrapper's
#                                  own timeout classification fires BEFORE a
#                                  harness kill (which would leave no envelope).
#                                  Raise it only together with BASH_MAX_TIMEOUT_MS
#                                  or run_in_background on the calling side.
#   CLAUDE_DELEGATE_AGENTIC        default 0
#   CLAUDE_DELEGATE_WORKDIR        REQUIRED in agentic mode (no $PWD fallback)
#   CLAUDE_DELEGATE_ALLOWED_TOOLS  default Read,Grep,Glob,Edit,Write,Bash (agentic)
#   CLAUDE_DELEGATE_MAX_TURNS      default 1 voter / 40 agentic — applied via a
#                                  runtime parser probe (the flag exists on
#                                  2.1.198 but is hidden from --help); WARNs if
#                                  a future CLI drops it. CLAUDE_DELEGATE_TIMEOUT
#                                  remains the hard guardrail.
#   CLAUDE_DELEGATE_RESUME         session ID; agentic-only (hard error in voter)
#   CLAUDE_DELEGATE_ADD_DIRS       colon-separated --add-dir paths (agentic)
#   CLAUDE_DELEGATE_ALLOW_API_KEY  default 0 — set 1 to permit metered API fallback
#
# Read discipline: head -3 output.md; cat only on FAILED/ERROR/GAP.
# Raw JSON envelope preserved at ${output%.md}.json — never cat it on success.
# Provenance header records mode/model/effort/auth/session/cost_usd/turns.

set -euo pipefail

# Portable timeout: GNU timeout -> gtimeout (Homebrew) -> perl fallback
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

# Portable octal file mode
_perm() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null || echo ""; }

SETUP_HINT="One-time setup (regular terminal, NOT via ! inside Claude Code): CLAUDE_CONFIG_DIR=<config_dir> claude setup-token; then: umask 077; cat > <config_dir>/oauth_token  (paste token, Ctrl-D)"

# Classify failure from stderr AND the stdout JSON envelope.
# Verified 2026-07-07: claude -p exits 0 on auth failure; the error lives
# ONLY in the JSON envelope (is_error:true, result:"Not logged in...").
_classify_failure() {
    local err_file="$1" exit_code="$2" timeout="$3" out_file="${4:-}"
    if [[ "$exit_code" == "124" ]]; then
        echo "STATUS: FAILED — Claude timed out after ${timeout}s"; return
    fi
    local diag_text=""
    [[ -s "$err_file" ]] && diag_text="$(head -20 "$err_file" 2>/dev/null)"
    if [[ -n "$out_file" && -s "$out_file" ]]; then
        diag_text="${diag_text}
$(head -c 2000 "$out_file" 2>/dev/null)"
    fi
    if [[ -z "$diag_text" ]]; then
        echo "STATUS: FAILED — Claude exited $exit_code with empty stderr/stdout"; return
    fi
    if grep -qiE "not logged in|invalid bearer token|invalid.{0,5}api.{0,5}key|unauthenticated|api key.{0,10}(missing|not set)|credential" <<< "$diag_text"; then
        echo "STATUS: FAILED — Delegate auth not configured or token invalid. ${SETUP_HINT//<config_dir>/$CONFIG_DIR}"
    elif grep -qiE "quota|rate.?limit|429|too many requests|usage limit" <<< "$diag_text"; then
        echo "STATUS: FAILED — Claude quota exhausted or rate-limited"
    elif grep -qiE "unknown model|model not.{0,10}(found|supported|available)|issue with the selected model" <<< "$diag_text"; then
        echo "STATUS: FAILED — Claude model unavailable: $MODEL"
    elif grep -qiE "operation not permitted|bind: address" <<< "$diag_text"; then
        echo "STATUS: FAILED — Sandbox restriction blocked claude"
    else
        local s; s="$(echo "$diag_text" | head -3 | tr '\n' ' ' | cut -c1-200)"
        echo "STATUS: FAILED — Claude exited $exit_code; first lines: ${s}"
    fi
}

# === Inputs (CLAUDE_DELEGATE_* namespace only — see header) ===
QUERY="${1:-}"
OUTPUT="${2:-${TMPDIR:-/tmp}/claude/claude_result_$(date +%s)_$$.md}"
TIMEOUT="${CLAUDE_DELEGATE_TIMEOUT:-570}"
MODEL="${CLAUDE_DELEGATE_MODEL:-opus}"
EFFORT="${CLAUDE_DELEGATE_EFFORT:-max}"
CONFIG_DIR="${CLAUDE_DELEGATE_CONFIG_DIR:-$HOME/.claude-delegate}"
AGENTIC="${CLAUDE_DELEGATE_AGENTIC:-0}"

if [[ -z "$QUERY" ]]; then
    echo "Usage: ask_claude.sh \"prompt\" [output.md]" >&2
    echo "       ask_claude.sh @prompt_file.md [output.md]" >&2
    exit 1
fi
if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: CLAUDE_DELEGATE_TIMEOUT must be integer seconds, got: $TIMEOUT" >&2
    exit 1
fi

# === Recursion guard (axis 2) ===
DEPTH="${CLAUDE_DELEGATE_DEPTH:-0}"
if [[ "$DEPTH" -ge 1 ]]; then
    echo "ERROR: ask_claude.sh called at delegate depth $DEPTH. Nested Claude-of-Claude delegation is disabled." >&2
    exit 1
fi

OUTPUT="$(_realpath_m "$OUTPUT")"
mkdir -p "$(dirname "$OUTPUT")"
ERR_LOG="${OUTPUT%.md}.err"
RAW_JSON="${OUTPUT%.md}.json"
# Truncate immediately: a dispatch killed mid-flight must never leave a PREVIOUS
# run's result readable as fresh at the same path (silent vote corruption).
: > "$OUTPUT"
rm -f -- "$RAW_JSON" "$ERR_LOG"

if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found. Install Claude Code: https://code.claude.com" >&2
    exit 1
fi

# === CLI contract check with feature detection ===
HELP_OUT="$(claude --help 2>&1 || true)"
for flag in "--print" "--model" "--output-format"; do
    if ! grep -q -- "$flag" <<< "$HELP_OUT"; then
        echo "ERROR: claude CLI missing required flag '$flag'. Installed: $(claude --version 2>&1 | head -1)" >&2
        exit 1
    fi
done
_has_flag() { grep -q -- "$1" <<< "$HELP_OUT"; }

# @file prompt (resolve BEFORE any cd)
if [[ "$QUERY" == @* ]]; then
    QUERY_FILE="${QUERY:1}"
    [[ -f "$QUERY_FILE" ]] || { echo "ERROR: Query file not found: $QUERY_FILE" >&2; exit 1; }
    QUERY=$(cat "$QUERY_FILE")
fi

# === Environment isolation (axes 1-3) ===
# Capture the inherited OAuth token BEFORE sanitizing, then strip every
# CLAUDECODE*/CLAUDE_CODE_* harness var so the child sees none of the
# orchestrator's session/effort/feature toggles (leakage measured 2026-07-07).
INHERITED_OAUTH="${CLAUDE_CODE_OAUTH_TOKEN:-}"
# grep -E (not sed \| alternation — BSD sed lacks it and would silently no-op)
while IFS= read -r _name; do
    unset "$_name" 2>/dev/null || true
done < <(env | grep -E '^(CLAUDECODE|CLAUDE_CODE_)[A-Za-z0-9_]*=' | cut -d= -f1 | sort -u)
# ANTHROPIC_AUTH_TOKEN outranks BOTH ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN
# in the documented auth precedence — an ambient one would silently hijack every
# dispatch. The delegate authenticates only via its own token file / explicit
# fallbacks, so drop it unconditionally.
unset ANTHROPIC_AUTH_TOKEN 2>/dev/null || true

export CLAUDE_CONFIG_DIR="$CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR" 2>/dev/null || true
export CLAUDE_DELEGATE_DEPTH=$(( DEPTH + 1 ))

if [[ -f "$CONFIG_DIR/CLAUDE.md" ]]; then
    echo "[delegate/claude] WARN: $CONFIG_DIR/CLAUDE.md exists — it will be injected into the delegate's context, breaking voter context-freedom. Remove it." >&2
fi

# Auth selection (axis 3). Priority: token file > inherited env OAuth > API key (opt-in).
TOKEN_FILE="$CONFIG_DIR/oauth_token"
AUTH_SRC=""
if [[ -f "$TOKEN_FILE" ]]; then
    # Self-heal permissions: long-lived credential must not be group/world-readable.
    _p="$(_perm "$TOKEN_FILE")"
    case "$_p" in
        *00|"") : ;;
        *) chmod 600 "$TOKEN_FILE" 2>/dev/null || true
           echo "[delegate/claude] WARN: $TOKEN_FILE was mode $_p — tightened to 600" >&2 ;;
    esac
    _tok="$(tr -d '[:space:]' < "$TOKEN_FILE")"
    if [[ -z "$_tok" ]]; then
        # Empty file = botched setup. Fail loud; never fall through to other auth.
        {
            echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=1 | mode=n/a | model=$MODEL | auth=none -->"
            echo ""
            echo "STATUS: FAILED — $TOKEN_FILE exists but is empty. ${SETUP_HINT//<config_dir>/$CONFIG_DIR}"
        } > "$OUTPUT"
        echo "ERROR: empty oauth_token file at $TOKEN_FILE" >&2
        echo "$OUTPUT"
        exit 1
    fi
    export CLAUDE_CODE_OAUTH_TOKEN="$_tok"
    unset ANTHROPIC_API_KEY 2>/dev/null || true   # subscription auth, never metered API
    AUTH_SRC="token-file"
elif [[ -n "$INHERITED_OAUTH" ]]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$INHERITED_OAUTH"
    unset ANTHROPIC_API_KEY 2>/dev/null || true   # OAuth-vs-API-key env precedence unverified — remove ambiguity
    echo "[delegate/claude] WARN: using inherited CLAUDE_CODE_OAUTH_TOKEN (the orchestrator's token) — auth independence degraded" >&2
    AUTH_SRC="env-oauth"
elif [[ -n "${ANTHROPIC_API_KEY:-}" && "${CLAUDE_DELEGATE_ALLOW_API_KEY:-0}" == "1" ]]; then
    # Opt-in only: the orchestrator exports ANTHROPIC_API_KEY ambiently, so
    # an unguarded fallback would silently bill the metered API on every
    # missing-token dispatch.
    echo "[delegate/claude] WARN: falling back to ANTHROPIC_API_KEY — this call bills the METERED API, not a subscription (CLAUDE_DELEGATE_ALLOW_API_KEY=1)" >&2
    AUTH_SRC="api-key"
else
    {
        echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=1 | mode=n/a | model=$MODEL | auth=none -->"
        echo ""
        echo "STATUS: FAILED — No delegate auth configured. ${SETUP_HINT//<config_dir>/$CONFIG_DIR} (Metered ANTHROPIC_API_KEY fallback requires CLAUDE_DELEGATE_ALLOW_API_KEY=1.)"
    } > "$OUTPUT"
    echo "ERROR: no delegate auth configured (see $CONFIG_DIR)" >&2
    echo "$OUTPUT"
    exit 1
fi

# === Mode-specific command construction (axis 4) ===
CLAUDE_CMD=( claude --print --model "$MODEL" --output-format json )
EFFORT_USED="n/a"
if _has_flag "--effort"; then
    CLAUDE_CMD+=( --effort "$EFFORT" )
    EFFORT_USED="$EFFORT"
fi

CLEANUP_DIR=""
BODY_TMP=""
# set-e-safe cleanup ([[ -z ]] || form — never returns non-zero on empty vars)
_finish() {
    [[ -z "$CLEANUP_DIR" ]] || rm -rf -- "$CLEANUP_DIR"
    [[ -z "$BODY_TMP"    ]] || rm -f -- "$BODY_TMP" "${BODY_TMP}.meta"
}
trap _finish EXIT

if [[ "$AGENTIC" == "1" ]]; then
    MODE="agentic"
    WORKDIR="${CLAUDE_DELEGATE_WORKDIR:-}"
    if [[ -z "$WORKDIR" ]]; then
        echo "ERROR: CLAUDE_DELEGATE_WORKDIR is required in agentic mode (no \$PWD fallback — an auto-accepting agent must not land in the orchestrator's cwd). Use a git worktree for parallel dispatches." >&2
        exit 1
    fi
    [[ -d "$WORKDIR" ]] || { echo "ERROR: CLAUDE_DELEGATE_WORKDIR does not exist: $WORKDIR" >&2; exit 1; }
    MAX_TURNS="${CLAUDE_DELEGATE_MAX_TURNS:-40}"
    ALLOWED="${CLAUDE_DELEGATE_ALLOWED_TOOLS:-Read,Grep,Glob,Edit,Write,Bash}"
    CLAUDE_CMD+=( --allowedTools "$ALLOWED" )
    _has_flag "--permission-mode" && CLAUDE_CMD+=( --permission-mode acceptEdits )
    if [[ -n "${CLAUDE_DELEGATE_ADD_DIRS:-}" ]]; then
        IFS=':' read -ra _dirs <<< "$CLAUDE_DELEGATE_ADD_DIRS"
        for d in "${_dirs[@]}"; do [[ -n "$d" ]] && CLAUDE_CMD+=( --add-dir "$d" ); done
    fi
    [[ -n "${CLAUDE_DELEGATE_RESUME:-}" ]] && CLAUDE_CMD+=( --resume "$CLAUDE_DELEGATE_RESUME" )
else
    MODE="voter"
    if [[ -n "${CLAUDE_DELEGATE_RESUME:-}" ]]; then
        echo "ERROR: CLAUDE_DELEGATE_RESUME is agentic-only. Voter cwds are throwaway mktemp dirs and session lookup is cwd-scoped, so voter sessions can never be resumed." >&2
        exit 1
    fi
    # Fail closed: --tools "" (zero tool schemas) and --no-session-persistence are
    # the voter's core isolation controls. If a future CLI drops either flag, stop
    # rather than silently degrade to a tool-capable, session-writing voter.
    for _req in "--tools" "--no-session-persistence"; do
        if ! _has_flag "$_req"; then
            echo "ERROR: installed claude CLI lacks '$_req' — voter mode fails closed. Update ask_claude.sh for the new CLI before dispatching votes. Installed: $(claude --version 2>&1 | head -1)" >&2
            exit 1
        fi
    done
    CLAUDE_CMD+=( --tools "" --no-session-persistence )
    _has_flag "--permission-mode" && CLAUDE_CMD+=( --permission-mode dontAsk )
    # --tools "" governs BUILT-IN tools only; MCP tools are unaffected. The fresh
    # config dir + throwaway cwd load no MCP servers anyway, but pin it:
    # --strict-mcp-config with no --mcp-config means zero MCP servers.
    _has_flag "--strict-mcp-config" && CLAUDE_CMD+=( --strict-mcp-config )
    # Throwaway cwd: no project CLAUDE.md/settings pickup, nothing to read even if
    # a tool call slipped through.
    WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/claude_voter_XXXXXX")"
    CLEANUP_DIR="$WORKDIR"
    MAX_TURNS="${CLAUDE_DELEGATE_MAX_TURNS:-1}"
fi

# --max-turns exists on 2.1.198 but is HIDDEN from --help, so probe the parser
# directly: a supported flag missing its argument reports "argument missing";
# an unsupported flag reports "unknown option". Capture first — the probe exits
# non-zero by design and a pipeline would trip pipefail.
_mt_probe="$(claude --max-turns 2>&1 || true)"
if grep -q "argument missing" <<< "$_mt_probe"; then
    CLAUDE_CMD+=( --max-turns "$MAX_TURNS" )
elif [[ -n "${CLAUDE_DELEGATE_MAX_TURNS:-}" ]]; then
    echo "[delegate/claude] WARN: CLAUDE_DELEGATE_MAX_TURNS=$CLAUDE_DELEGATE_MAX_TURNS ignored — installed CLI does not accept --max-turns. CLAUDE_DELEGATE_TIMEOUT is the guardrail." >&2
fi

echo "[delegate/claude] Mode=$MODE model=$MODEL effort=$EFFORT_USED auth=$AUTH_SRC config=$CONFIG_DIR" >&2
echo "[delegate/claude] Sending query ($(printf %s "$QUERY" | wc -c | tr -d ' ') chars) from $WORKDIR" >&2
echo "[delegate/claude] Output: $OUTPUT" >&2

EXIT_CODE=0
(
    cd "$WORKDIR"
    _timeout "$TIMEOUT" "${CLAUDE_CMD[@]}" "$QUERY" </dev/null
) > "$RAW_JSON" 2>"$ERR_LOG" || EXIT_CODE=$?

# === Envelope evaluation (exit code 0 does NOT mean success) ===
PARSE="fail"          # ok | fail
RAW_PASSTHROUGH=0
SESSION_ID="unknown"; COST="n/a"; TURNS="n/a"
BODY_TMP="$(mktemp 2>/dev/null || echo "${OUTPUT}.body")"

if [[ "$EXIT_CODE" -eq 0 && -s "$RAW_JSON" ]]; then
    if command -v jq &>/dev/null && jq -e . "$RAW_JSON" >/dev/null 2>&1; then
        IS_ERROR="$(jq -r '.is_error // false' "$RAW_JSON")"
        SUBTYPE="$(jq -r '.subtype // ""' "$RAW_JSON")"
        SESSION_ID="$(jq -r '.session_id // "unknown"' "$RAW_JSON")"
        COST="$(jq -r '.total_cost_usd // "n/a"' "$RAW_JSON")"
        TURNS="$(jq -r '.num_turns // "n/a"' "$RAW_JSON")"
        jq -r '.result // empty' "$RAW_JSON" > "$BODY_TMP"
        # Empty-body check is on the PARSED result, not the output file size
        # (a provenance header alone would defeat a file-size check).
        if [[ "$IS_ERROR" == "false" && "$SUBTYPE" == "success" && -s "$BODY_TMP" ]]; then
            PARSE="ok"
        fi
    elif command -v python3 &>/dev/null && python3 - "$RAW_JSON" "$BODY_TMP" > "${BODY_TMP}.meta" 2>/dev/null << 'PYEOF'
import json, sys
raw, bodyf = sys.argv[1], sys.argv[2]
with open(raw) as f:
    d = json.load(f)
ok = (not d.get("is_error", False)) and d.get("subtype") == "success"
body = d.get("result") or ""
with open(bodyf, "w") as f:
    f.write(body)
print("\t".join([
    "ok" if (ok and body.strip()) else "err",
    str(d.get("session_id", "unknown")),
    str(d.get("total_cost_usd", "n/a")),
    str(d.get("num_turns", "n/a")),
]))
PYEOF
    then
        IFS=$'\t' read -r _state SESSION_ID COST TURNS < "${BODY_TMP}.meta" || _state="err"
        rm -f "${BODY_TMP}.meta"
        [[ "$_state" == "ok" && -s "$BODY_TMP" ]] && PARSE="ok"
    else
        rm -f "${BODY_TMP}.meta"
        # No jq/python3 (or unparseable output): dependency-free fail-closed check.
        # Success requires a success marker AND no error marker; anything else fails.
        # Whitespace-tolerant: pretty-printed envelopes ("is_error": true) must parse too.
        if ! grep -qE '"is_error"[[:space:]]*:[[:space:]]*true' "$RAW_JSON" \
           && grep -qE '"subtype"[[:space:]]*:[[:space:]]*"success"' "$RAW_JSON"; then
            PARSE="ok"
            RAW_PASSTHROUGH=1
            cp "$RAW_JSON" "$BODY_TMP"
        fi
    fi
fi

if [[ "$PARSE" == "ok" ]]; then
    {
        echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=0 | mode=$MODE | model=$MODEL | effort=$EFFORT_USED | auth=$AUTH_SRC | session=$SESSION_ID | cost_usd=$COST | turns=$TURNS$( [[ "$RAW_PASSTHROUGH" == "1" ]] && printf ' | raw=1' ) -->"
        echo ""
        cat "$BODY_TMP"
    } > "$OUTPUT"
    rm -f "$BODY_TMP"
    echo "[delegate/claude] Done. $(wc -c < "$OUTPUT" | tr -d ' ') bytes -> $OUTPUT (raw json: $RAW_JSON)" >&2
    echo "$OUTPUT"
    exit 0
else
    # Failure: CLI error exit, timeout, error envelope, or empty result.
    DIAG_FILE="$RAW_JSON"
    [[ -s "$BODY_TMP" ]] && DIAG_FILE="$BODY_TMP"
    FAILURE_LINE="$(_classify_failure "$ERR_LOG" "$EXIT_CODE" "$TIMEOUT" "$DIAG_FILE")"
    if [[ "$EXIT_CODE" -eq 0 && ! -s "$RAW_JSON" ]]; then
        FAILURE_LINE="STATUS: FAILED — Claude exited 0 but produced no output"
    elif [[ "$EXIT_CODE" -eq 0 && "$FAILURE_LINE" == *"first lines:"* && ! -s "$BODY_TMP" ]]; then
        FAILURE_LINE="STATUS: FAILED — Claude returned a success exit but an empty result body (envelope at $RAW_JSON)"
    fi
    {
        echo "<!-- ask_claude.sh | $(date -u +%Y-%m-%dT%H:%M:%SZ) | exit=$EXIT_CODE | mode=$MODE | model=$MODEL | effort=$EFFORT_USED | auth=$AUTH_SRC | session=$SESSION_ID -->"
        echo ""
        echo "$FAILURE_LINE"
        echo ""
        [[ -s "$RAW_JSON" ]] && cat "$RAW_JSON" || true
    } > "$OUTPUT"
    rm -f "$BODY_TMP"
    echo "ERROR: $FAILURE_LINE (see $ERR_LOG)" >&2
    echo "$OUTPUT"
    # Preserve the real failure code; floor at 1 so envelope-level failures
    # (CLI exit 0) still exit non-zero for callers.
    [[ "$EXIT_CODE" -gt 0 ]] && exit "$EXIT_CODE"
    exit 1
fi
