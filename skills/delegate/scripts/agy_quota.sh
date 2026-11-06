#!/bin/bash
# agy_quota.sh — Read agy usage pools; this invokes the provider CLI and is never an offline preflight.
# See ../references/legs.md for modes, dependencies, costs and limitations.
set -euo pipefail

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
            alarm $secs;
            waitpid($pid, 0);
            exit ($? >> 8);
        ' "$secs" "$@"
    fi
}

_fail() { echo "[delegate/agy-quota] $*" >&2; exit 3; }
[[ $# == 0 ]] || _fail 'usage: agy_quota.sh (no arguments)'
CAP="${AGY_QUOTA_TIMEOUT:-30}"
[[ "$CAP" =~ ^[0-9]+$ ]] && (( 10#$CAP > 0 )) || _fail 'invalid AGY_QUOTA_TIMEOUT'
if [[ -z "${AGY_BIN:-}" ]]; then
    if command -v agy &>/dev/null; then
        AGY_BIN="$(command -v agy)"
    elif [[ -x "$HOME/.local/bin/agy" ]]; then
        AGY_BIN="$HOME/.local/bin/agy"
    else
        _fail 'agy CLI not found'
    fi
fi

RC=0
RAW="$(_timeout "$CAP" "$AGY_BIN" -p "/usage" --output-format json </dev/null 2>/dev/null)" || RC=$?
[[ "$RC" != 124 ]] || _fail "quota probe timed out after ${CAP}s"
[[ "$RC" == 0 ]] || _fail "quota probe exited $RC"

# Buffer the complete parse so malformed data can never emit a partial success.
if command -v jq >/dev/null 2>&1; then
    PARSED="$(jq -ers '
        if length != 1 or .[0].status != "SUCCESS" then error("status") else .[0] end
        | [.command.data.groups[] | . as $group | .buckets[]
           | . as $bucket
           | (if (.id | startswith("gemini-")) then "gemini"
              elif (.id | startswith("3p-")) then "claude"
              elif ($group.name | test("gemini"; "i")) then "gemini"
              elif ($group.name | test("claude"; "i")) then "claude"
              else empty end) as $pool
           | if (.remaining_fraction | type) != "number" or
                .remaining_fraction < 0 or .remaining_fraction > 1
             then error("fraction")
             else {pool: $pool, id: .id, fraction: .remaining_fraction} end] as $buckets
        | if ($buckets | length) == 0 then error("no mapped buckets") else
            ("gemini", "claude") as $pool
            | [$buckets[] | select(.pool == $pool)]
            | select(length > 0)
            | [$pool, (map(.fraction) | min | tostring),
               (map(.id + "=" + (.fraction | tostring)) | join(","))] | @tsv
          end
    ' <<< "$RAW" 2>/dev/null)" || _fail 'invalid quota response (JSON, status, or buckets)'
else
    PARSED="$(python3 -c '
import json
import sys
from decimal import Decimal

d = json.load(sys.stdin, parse_float=Decimal, parse_int=Decimal)
if d.get("status") != "SUCCESS":
    raise ValueError("status")
pools = {"gemini": [], "claude": []}
for group in d["command"]["data"]["groups"]:
    for bucket in group["buckets"]:
        ident = bucket["id"]
        if ident.startswith("gemini-"):
            pool = "gemini"
        elif ident.startswith("3p-"):
            pool = "claude"
        else:
            name = group.get("name", "").lower()
            pool = "gemini" if "gemini" in name else "claude" if "claude" in name else None
        if pool is None:
            continue
        fraction = bucket["remaining_fraction"]
        if not isinstance(fraction, Decimal) or not fraction.is_finite() or not 0 <= fraction <= 1:
            raise ValueError("fraction")
        pools[pool].append((ident, fraction))
lines = []
for pool, buckets in pools.items():
    if buckets:
        detail = ",".join(f"{ident}={value}" for ident, value in buckets)
        lines.append(f"{pool}\t{min(value for _, value in buckets)}\t{detail}")
if not lines:
    raise ValueError("no mapped buckets")
print("\n".join(lines))
' <<< "$RAW" 2>/dev/null)" || _fail 'invalid quota response (JSON, status, or buckets)'
fi
[[ -n "$PARSED" ]] || _fail 'no mapped quota buckets'
printf '%s\n' "$PARSED"
