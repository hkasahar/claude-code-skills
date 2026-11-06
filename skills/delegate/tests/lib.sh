#!/bin/bash
# Assertions exit the scenario subshell, so later checks cannot mask a failure.
fail() { echo "$*" >&2; exit 1; }
assert_eq() { [[ "$1" == "$2" ]] || fail "${3:-values differ}: expected <$2>, got <$1>"; }
assert_grep() { command grep -qE -- "$2" "$1" || fail "$1 does not match: $2"; }
assert_grep_count() {
    local count
    count="$(command grep -cE -- "$2" "$1" || true)"
    assert_eq "$count" "$3" "$1 matching line count ($2)"
}
assert_absent() { [[ ! -e "$1" ]] || fail "unexpected file: $1"; }
assert_rc() { assert_eq "$1" "$2" "exit code"; }
_alarm() {
    perl -e '
        use POSIX ":sys_wait_h";
        my $seconds = shift @ARGV;
        my $pid = fork // die "fork: $!";
        if (!$pid) { exec @ARGV; die "exec: $!" }
        $SIG{ALRM} = sub { kill 9, $pid; waitpid($pid, 0); exit 124 };
        alarm $seconds; waitpid($pid, 0); exit ($? >> 8);
    ' "$@"
}
