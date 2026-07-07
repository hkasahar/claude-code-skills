#!/bin/bash
# preflight.sh — Check that Antigravity CLI, Codex CLI, and Claude CLI are installed and authenticated.
#
# Usage:
#   bash preflight.sh
#
# Exit codes:
#   0 = required CLIs (agy, codex) installed with the expected flags; claude is
#       optional. Auth problems are reported as WARNs only — they do NOT cause
#       exit 1 (a user may intentionally leave a leg unconfigured).
#   1 = required CLI missing or required flag absent

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

# Two-tier probe: tier 1 (binary + flag) is required; tier 2 (auth) is best-effort.
# Returns 0 if tier 1 passes (auth WARNs are not failures).
# probe_args should be the COMPLETE invocation flags including prompt-as-value (e.g.
# `--print "Reply with exactly: X" --print-timeout 50s`), since agy/claude take the
# prompt as the value of --print, not as a trailing positional.
_probe_cli() {
    local name="$1" bin="$2" probe_expect="$3"
    shift 3
    local probe_args=("$@")

    # Tier 1: binary present
    local bin_path
    if ! bin_path="$(command -v "$bin" 2>/dev/null)"; then
        # Try fallback path
        if [[ -x "$HOME/.local/bin/$bin" ]]; then
            bin_path="$HOME/.local/bin/$bin"
        else
            echo "FAIL  $name CLI not found" >&2
            return 1
        fi
    fi
    echo "OK    $name CLI found: $bin_path" >&2

    # Tier 1.5: required flags
    local help_out
    help_out="$("$bin_path" --help 2>&1 || true)"
    for flag in "--print"; do
        if ! grep -q -- "$flag" <<< "$help_out"; then
            echo "FAIL  $name CLI missing required flag '$flag'" >&2
            return 1
        fi
    done

    # Tier 2: auth probe with separate stdout/stderr capture
    local probe_stdout probe_stderr
    probe_stdout="$(mktemp 2>/dev/null || echo "/tmp/preflight_$$_stdout")"
    probe_stderr="$(mktemp 2>/dev/null || echo "/tmp/preflight_$$_stderr")"
    if _timeout 60 "$bin_path" "${probe_args[@]}" >"$probe_stdout" 2>"$probe_stderr" </dev/null; then
        if grep -q "$probe_expect" "$probe_stdout"; then
            echo "OK    $name CLI authenticated" >&2
            rm -f "$probe_stdout" "$probe_stderr"
            return 0
        fi
    fi

    # Auth failure: classify
    local stderr_head
    stderr_head="$(head -10 "$probe_stderr" 2>/dev/null)"
    if grep -qiE "operation not permitted|bind: address" <<< "$stderr_head"; then
        echo "WARN  $name probe blocked by sandbox (TCP bind). Tier 1 OK — run outside sandbox or use dangerouslyDisableSandbox=true to test runtime." >&2
        rm -f "$probe_stdout" "$probe_stderr"
        return 0  # Not a tier-1 failure
    fi
    if grep -qiE "invalid.{0,5}api.{0,5}key|unauthenticated|please log in" <<< "$stderr_head"; then
        echo "WARN  $name CLI not authenticated. Run interactive login or set API key." >&2
        echo "      First stderr line: $(head -1 "$probe_stderr" 2>/dev/null)" >&2
        rm -f "$probe_stdout" "$probe_stderr"
        return 0  # Tier-2 failure is not fatal; user may not have set up headless auth
    fi
    echo "WARN  $name auth probe inconclusive (exit unknown)." >&2
    echo "      First stderr line: $(head -1 "$probe_stderr" 2>/dev/null)" >&2
    rm -f "$probe_stdout" "$probe_stderr"
    return 0
}

STATUS=0

echo "=== Delegate Skill Preflight Check ===" >&2

# Antigravity (required)
_probe_cli "Antigravity" "agy" "AGY_OK" --print "Reply with exactly: AGY_OK" --print-timeout 50s || STATUS=1
echo "" >&2

# Codex (required)
if command -v codex &>/dev/null; then
    echo "OK    Codex CLI found: $(command -v codex)" >&2
    if _timeout 60 codex exec "Reply with exactly: CODEX_OK" </dev/null 2>/dev/null | grep -qi "CODEX_OK"; then
        echo "OK    Codex CLI authenticated" >&2
    else
        echo "WARN  Codex CLI auth probe inconclusive. Run: codex login" >&2
    fi
else
    echo "FAIL  Codex CLI not found. Install: npm install -g @openai/codex" >&2
    STATUS=1
fi
echo "" >&2

# Claude (optional — independent delegate: 3rd voter + agentic worker via ask_claude.sh)
# Probe mirrors the wrapper's auth-relevant env: delegate config dir, token-file
# env auth, no ANTHROPIC_API_KEY and no ANTHROPIC_AUTH_TOKEN (both outrank the
# env OAuth token in the CLI's auth precedence — the wrapper drops them, so the
# probe must too or it validates a credential real dispatches never use),
# throwaway cwd, --tools "" and no session writes.
# haiku for the probe: cheapest way to confirm auth without burning opus quota.
echo "--- Claude (optional, independent delegate for ask_claude.sh) ---" >&2
CLAUDE_CFG="${CLAUDE_DELEGATE_CONFIG_DIR:-$HOME/.claude-delegate}"
if command -v claude &>/dev/null; then
    echo "OK    Claude CLI found: $(command -v claude)" >&2
    _claude_help="$(claude --help 2>&1 || true)"
    _claude_flags_ok=1
    for flag in "--print" "--output-format"; do
        if ! grep -q -- "$flag" <<< "$_claude_help"; then
            echo "WARN  Claude CLI missing flag '$flag' — ask_claude.sh will fail its contract check" >&2
            _claude_flags_ok=0
        fi
    done
    _claude_tok=""
    [[ -f "$CLAUDE_CFG/oauth_token" ]] && _claude_tok="$(tr -d '[:space:]' < "$CLAUDE_CFG/oauth_token")"
    if [[ "$_claude_flags_ok" == "1" && -n "$_claude_tok" ]]; then
        _probe_stdout="$(mktemp 2>/dev/null || echo "/tmp/preflight_claude_$$")"
        _probe_stderr="$(mktemp 2>/dev/null || echo "/tmp/preflight_claude_${$}_err")"
        _probe_dir="$(mktemp -d 2>/dev/null || echo "/tmp/preflight_claude_${$}_dir")"
        mkdir -p "$_probe_dir"
        if ( cd "$_probe_dir" && _timeout 60 env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
                CLAUDE_CONFIG_DIR="$CLAUDE_CFG" CLAUDE_CODE_OAUTH_TOKEN="$_claude_tok" \
                claude --print --model haiku --tools "" --no-session-persistence \
                --output-format json "Reply with exactly: CLAUDE_OK" \
             ) >"$_probe_stdout" 2>"$_probe_stderr" </dev/null; then :; fi
        # claude -p exits 0 even on auth failure (verified 2026-07-07 on 2.1.198):
        # judge success by the JSON envelope, never by the exit code.
        _claude_auth_ok=0
        if command -v jq &>/dev/null && jq -e . "$_probe_stdout" >/dev/null 2>&1; then
            jq -e '(.is_error == false) and ((.result // "") | tostring | contains("CLAUDE_OK"))' \
                "$_probe_stdout" >/dev/null 2>&1 && _claude_auth_ok=1
        else
            grep -q "CLAUDE_OK" "$_probe_stdout" 2>/dev/null \
                && ! grep -q '"is_error":true' "$_probe_stdout" 2>/dev/null \
                && _claude_auth_ok=1
        fi
        if [[ "$_claude_auth_ok" == "1" ]]; then
            echo "OK    Claude delegate authenticated (config: $CLAUDE_CFG, auth: token-file)" >&2
        else
            # Auth errors live in the stdout JSON envelope (exit code is 0) — scan both streams.
            _diag="$(head -c 2000 "$_probe_stdout" 2>/dev/null; echo; head -5 "$_probe_stderr" 2>/dev/null)"
            if grep -qiE "not logged in|invalid bearer token|unauthenticated|invalid.{0,5}api.{0,5}key" <<< "$_diag"; then
                echo "WARN  Claude delegate token invalid or expired." >&2
                echo "      Re-run in a regular terminal (NOT via ! inside Claude Code):" >&2
                echo "        CLAUDE_CONFIG_DIR=$CLAUDE_CFG claude setup-token" >&2
                echo "        umask 077; cat > $CLAUDE_CFG/oauth_token   # paste token, Ctrl-D" >&2
            elif grep -qiE "operation not permitted|bind: address" <<< "$_diag"; then
                echo "WARN  Claude probe blocked by sandbox. Run outside sandbox or use dangerouslyDisableSandbox=true." >&2
            else
                echo "WARN  Claude auth probe inconclusive. stdout: $(head -c 160 "$_probe_stdout" 2>/dev/null) stderr: $(head -1 "$_probe_stderr" 2>/dev/null)" >&2
            fi
        fi
        rm -f "$_probe_stdout" "$_probe_stderr"
        rm -rf "$_probe_dir"
    elif [[ "$_claude_flags_ok" == "1" ]]; then
        echo "WARN  Claude delegate not configured (no token at $CLAUDE_CFG/oauth_token)." >&2
        echo "      One-time setup in a regular terminal (NOT via ! inside Claude Code):" >&2
        echo "        CLAUDE_CONFIG_DIR=$CLAUDE_CFG claude setup-token" >&2
        echo "        umask 077; cat > $CLAUDE_CFG/oauth_token   # paste token, Ctrl-D" >&2
        echo "      ask_claude.sh dispatches will fail until configured." >&2
    fi
else
    echo "WARN  Claude CLI not found. Install Claude Code: https://code.claude.com" >&2
    echo "      ask_claude.sh (3rd voter / agentic worker) will be unavailable." >&2
fi
echo "" >&2

if [ $STATUS -eq 0 ]; then
    echo "All required checks passed. Ready to delegate." >&2
else
    echo "Some required checks failed. Fix above issues before delegating." >&2
fi

exit $STATUS
