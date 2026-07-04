#!/bin/bash
# preflight.sh — Check that Antigravity CLI, Codex CLI, and Claude CLI are installed and authenticated.
#
# Usage:
#   bash preflight.sh
#
# Exit codes:
#   0 = all required CLIs (agy, codex) available and authenticated; claude is optional
#   1 = required CLI missing or auth FAIL (sandbox WARNs do not cause exit 1)

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

# Claude (optional — 3rd voter for majority-of-3 votes)
echo "--- Claude (optional, 3rd voter for majority-of-3 votes) ---" >&2
if command -v claude &>/dev/null; then
    echo "OK    Claude CLI found: $(command -v claude)" >&2
    _probe_stdout="$(mktemp 2>/dev/null || echo "/tmp/preflight_claude_$$")"
    _probe_stderr="$(mktemp 2>/dev/null || echo "/tmp/preflight_claude_${$}_err")"
    if _timeout 60 claude --print --model opus --tools "" --bare "Reply with exactly: CLAUDE_OK" >"$_probe_stdout" 2>"$_probe_stderr" </dev/null && grep -q "CLAUDE_OK" "$_probe_stdout"; then
        echo "OK    Claude CLI authenticated" >&2
    else
        if grep -qiE "invalid.{0,5}api.{0,5}key|unauthenticated" "$_probe_stderr" 2>/dev/null; then
            echo "WARN  Claude headless auth not configured." >&2
            echo "      Run: claude setup-token  OR  export ANTHROPIC_API_KEY=..." >&2
            echo "      3rd-voter dispatches will fail until configured." >&2
        else
            echo "WARN  Claude auth probe inconclusive: $(head -1 "$_probe_stderr" 2>/dev/null)" >&2
        fi
    fi
    rm -f "$_probe_stdout" "$_probe_stderr"
else
    echo "WARN  Claude CLI not found. Install Claude Code from https://claude.com/claude-code" >&2
    echo "      3rd-voter functionality (INCLUDE_CLAUDE=1 ask_both.sh) will be unavailable." >&2
fi
echo "" >&2

if [ $STATUS -eq 0 ]; then
    echo "All required checks passed. Ready to delegate." >&2
else
    echo "Some required checks failed. Fix above issues before delegating." >&2
fi

exit $STATUS
