#!/bin/bash
# Offline credit-policy checks; copied wrappers can only reach recording fakes.
set -euo pipefail
TESTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPTS="${CODEX_CREDIT_TEST_SCRIPTS:-$(cd "$TESTS/../scripts" && pwd)}"
. "$TESTS/lib.sh"
RUN="$(mktemp -d "${TMPDIR:-/tmp}/codex-credit-tests.XXXXXX")"
RUN="$(cd "$RUN" && pwd)"
ORIGINAL_PATH="$PATH"
export REAL_PYTHON3="$(command -v python3)"

setup() {
    local variable
    while IFS= read -r variable; do
        case "$variable" in CODEX_*|ANTIGRAVITY_*|FAKE_*|INCLUDE_*|DEEPSEEK_*|GROK_*|CLAUDE_DELEGATE_*|BATCH_*|DELEGATE_DIR) unset "$variable" ;; esac
    done < <(compgen -v)
    unset OPENROUTER_API_KEY AGY_BIN AGY_QUOTA_TIMEOUT GEMINI_PDF_LOCK_DIR BASH_ENV ENV
    export CASE HOME="$CASE/home" TMPDIR="$CASE/tmp" PATH="$CASE/bin:$TESTS/fakes:$ORIGINAL_PATH"
    export CODEX_TIMEOUT=600 CODEX_CAPACITY_BACKOFF=0
    export FAKE_CODEX_STATE="$CASE/codex" FAKE_DEEPSEEK_STATE="$CASE/deepseek"
    export FAKE_AGY_LEDGER="$CASE/agy.tsv" FAKE_AGY_PRINT_TIMEOUT_LOG="$CASE/timeouts.tsv"
    mkdir -p "$HOME" "$TMPDIR" "$CASE/bin" "$FAKE_CODEX_STATE" "$FAKE_DEEPSEEK_STATE" "$CASE/scripts"
    : > "$FAKE_AGY_LEDGER"
    : > "$FAKE_AGY_PRINT_TIMEOUT_LOG"
    cp "$SOURCE_SCRIPTS/"*.sh "$CASE/scripts/"
    SCRIPTS="$CASE/scripts"
    # Old positional parsers must not create stray outputs in the worktree.
    mkdir -p "$CASE/work"
    cd "$CASE/work"
    QUERY=$'Synthetic credit check\nLiteral shell text: $HOME `uname` and quoted words.'
    printf '%s' "$QUERY" > "$CASE/prompt.txt"
    cat > "$CASE/bin/codex" <<'FAKE'
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ['FAKE_CODEX_STATE'])
state.mkdir(parents=True, exist_ok=True)
number = len(list(state.glob('call-*.json'))) + 1
args = sys.argv[1:]
env_keys = ('OPENROUTER_API_KEY', 'DEEPSEEK_DELEGATE_KEY_FILE', 'DEEPSEEK_DELEGATE_TIMEOUT', 'DEEPSEEK_DELEGATE_MODEL', 'DEEPSEEK_DELEGATE_EFFORT')
record = {'argv': args, 'env': {key: os.environ.get(key) for key in env_keys}, 'cwd': os.getcwd()}
(state / ('call-%d.json' % number)).write_text(json.dumps(record))
mode_file = state / ('mode-%d' % number)
mode = mode_file.read_text().strip() if mode_file.exists() else os.getenv('FAKE_CODEX_MODE', 'account-curly')
print('synthetic Codex stderr', file=sys.stderr)
rollout_file = state / ('rollout-%d' % number)
rollout_mode = rollout_file.read_text().strip() if rollout_file.exists() else os.getenv('FAKE_CODEX_ROLLOUT', 'zero')
uuid = '01a19f30-4c78-76e1-b55c-%012d' % os.getpid()
if rollout_mode != 'no-session':
    print('session id: ' + ('not-a-uuid' if rollout_mode == 'malformed' else uuid), file=sys.stderr)
    if rollout_mode != 'missing':
        sessions = Path(os.getenv('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions/2026/11/04'
        sessions.mkdir(parents=True, exist_ok=True)
        item = {'type': 'response_item', 'payload': {'type': 'function_call' if rollout_mode in ('call', 'quoted') else 'message'}}
        (sessions / ('rollout-2026-11-04T00-00-00-' + uuid + '.jsonl')).write_text(json.dumps(item) + '\n')
        if rollout_mode == 'quoted':
            quoted_uuid = '02' + uuid[2:]
            (sessions / ('rollout-2026-11-04T00-00-00-' + quoted_uuid + '.jsonl')).write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'message'}}) + '\n')
            print('The transcript quotes a different run:\nsession id: ' + quoted_uuid, file=sys.stderr)
messages = {
    'account-curly': 'ERROR: You’ve hit your usage limit.',
    'account-ascii': "ERROR: You've hit your usage limit.",
    'model': 'ERROR: You’ve hit your usage limit for gpt-6.1-sol. Switch to another model now, or try again later.',
    'quoted': 'The transcript quotes ERROR: You’ve hit your usage limit.',
    'indented': '  > ERROR: You’ve hit your usage limit.',
    'capacity': 'ERROR: Selected model is at capacity',
}
if mode == 'success':
    target = Path(args[args.index('--output-last-message') + 1])
    target.write_text('STATUS: DONE — fake codex\nSynthetic harness result.\n')
    sys.exit(0)
if mode == 'empty-success':
    sys.exit(0)
if mode == 'exit124':
    sys.exit(124)
print(messages.get(mode, 'ERROR: synthetic generic failure'), file=sys.stderr)
sys.exit(int(os.getenv('FAKE_CODEX_RC', '1')))
FAKE
    cat > "$SCRIPTS/ask_deepseek.sh" <<'FAKE'
#!/bin/bash
set -euo pipefail
python3 - "$@" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

args = sys.argv[1:]
state = Path(os.environ['FAKE_DEEPSEEK_STATE'])
state.mkdir(parents=True, exist_ok=True)
keycheck = args == ['--key-check']
kind = 'keycheck' if keycheck else 'dispatch'
env_keys = ('OPENROUTER_API_KEY', 'DEEPSEEK_DELEGATE_KEY_FILE', 'DEEPSEEK_DELEGATE_TIMEOUT', 'DEEPSEEK_DELEGATE_MODEL', 'DEEPSEEK_DELEGATE_EFFORT')
record = {'argv': args, 'env': {key: os.environ.get(key) for key in env_keys}}
if not keycheck:
    if len(args) != 2 or not args[0].startswith('@'):
        raise SystemExit('fake DeepSeek requires @file and output')
    prompt_file = Path(args[0][1:])
    record['prompt'] = prompt_file.read_text()
    record['mode'] = stat.S_IMODE(prompt_file.stat().st_mode)
(state / ('%s-%d.json' % (kind, os.getpid()))).write_text(json.dumps(record))
if keycheck:
    if os.getenv('FAKE_DEEPSEEK_KEY_STDERR'):
        print(os.environ['FAKE_DEEPSEEK_KEY_STDERR'], file=sys.stderr)
    code = int(os.getenv('FAKE_DEEPSEEK_KEY_RC', '0'))
    status = 'STATUS: FAILED — synthetic key rejected' if code else 'STATUS: OK — key-check: synthetic capped key'
    print(os.getenv('FAKE_DEEPSEEK_KEY_STATUS', status))
    sys.exit(code)
if os.getenv('FAKE_DEEPSEEK_NO_OUTPUT') == '1':
    print('synthetic DeepSeek launch failure', file=sys.stderr)
    sys.exit(int(os.getenv('FAKE_DEEPSEEK_RC', '9')))
out = Path(args[1])
body = os.getenv('FAKE_DEEPSEEK_BODY', 'STATUS: DONE — fake DeepSeek')
resolved = os.getenv('FAKE_DEEPSEEK_RESOLVED', 'deepseek/deepseek-v4.1-flash-1017+Fireworks')
out.write_text('<!-- ask_deepseek.sh | synthetic | resolved=' + resolved + ' -->\n\n' + body + '\n' + record['prompt'] + '\n')
out.with_suffix('.json').write_text('{"fake":true}\n')
out.with_suffix('.err').write_text('synthetic DeepSeek stderr\n')
print(str(out))
sys.exit(int(os.getenv('FAKE_DEEPSEEK_RC', '0')))
PY
FAKE
    chmod +x "$CASE/bin/codex" "$SCRIPTS/ask_deepseek.sh"
    assert_eq "$(command -v codex)" "$CASE/bin/codex" 'Codex must use the offline stub'
    command -v jq >/dev/null || fail 'credit tests require jq for rollout checks'
}

invoke() {
    RC=0
    bash "$SCRIPTS/ask_codex.sh" "$@" > "$CASE/stdout" 2> "$CASE/stderr" || RC=$?
}

assert_calls() {
    local kind="$1" expected="$2" directory="${3:-$FAKE_CODEX_STATE}" count
    count="$(find "$directory" -name "$kind-*.json" -type f | wc -l | tr -d ' ')"
    assert_eq "$count" "$expected" "$kind call count"
}

assert_no_deepseek() {
    assert_calls dispatch 0 "$FAKE_DEEPSEEK_STATE"
    assert_calls keycheck 0 "$FAKE_DEEPSEEK_STATE"
}

assert_status() {
    assert_eq "$(sed -n '3p' "$CASE/out.md")" "$1" 'envelope line 3'
}

assert_envelope() {
    local output="$1" body="$2"
    assert_eq "$(sed -n '2p' "$output")" '' 'envelope blank line 2'
    assert_eq "$(sed -n '3p' "$output")" "$body" 'envelope body starts on line 3'
    assert_grep_count "$output" '^<!-- ask_codex.sh ' 1
    assert_grep_count "$output" '^<!-- ask_deepseek.sh ' 0
}

account_limit() {
    export FAKE_CODEX_MODE="$1"
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 75
    assert_status "STATUS: FAILED — usage-limit (account): Codex credit exhausted (model 'gpt-6.1-sol'). Fall back per /delegate (--credit-fallback)."
    assert_calls call 1
    assert_no_deepseek
}

account_no_flag_after_tool_calls() {
    export FAKE_CODEX_ROLLOUT=call
    cat > "$CASE/bin/jq" <<'FAKE'
#!/bin/bash
touch "$CASE/rollout-read"
exit 98
FAKE
    chmod +x "$CASE/bin/jq"
    account_limit account-curly
    assert_absent "$CASE/rollout-read"
}

ordinary_success() {
    export FAKE_CODEX_MODE=success
    printf 'stale Codex stderr\n' > "$CASE/out.codex.err"
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_status 'STATUS: DONE — fake codex'
    assert_calls call 1
    assert_no_deepseek
    assert_absent "$CASE/out.codex.err"
}

model_limit() {
    export FAKE_CODEX_MODE=model
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 75
    assert_status "STATUS: FAILED — usage-limit (model): Codex usage limit reached for model 'gpt-6.1-sol'. Switch Codex model per /delegate."
    assert_calls call 1
    assert_no_deepseek
}

quoted_limit() {
    export FAKE_CODEX_MODE="$1"
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 1
    assert_grep_count "$CASE/out.md" 'usage-limit' 0
    assert_grep "$CASE/stderr" 'ERROR: Codex exited with code 1 '
    assert_calls call 1
    assert_no_deepseek
}

assert_api_result() {
    local output="$1" expected_prompt="$2" header
    header="$(head -1 "$output")"
    [[ "$header" == *'| exit=0 | model=deepseek/deepseek-v4.1-flash-1017 | effort=max | fallback=codex-credit:deepseek-api requested=gpt-6.1-sol/xhigh provider=Fireworks -->' ]] || fail "DeepSeek API header assertion: <$header>"
    assert_envelope "$output" 'STATUS: DONE — fake DeepSeek'
    python3 - "$FAKE_DEEPSEEK_STATE" "$output" "$expected_prompt" <<'PY'
import json
import sys
from pathlib import Path
state, output, prompt = sys.argv[1:]
expected = str(Path(output).with_suffix('')) + '.deepseek.md'
records = [json.loads(path.read_text()) for path in Path(state).glob('dispatch-*.json')]
records = [record for record in records if record['argv'][1] == expected]
assert len(records) == 1, 'exactly one API dispatch must target ' + expected
record = records[0]
assert record['argv'][0].startswith('@'), 'API prompt must use @file'
assert record['prompt'] == prompt, 'API prompt content differs'
assert record['mode'] == 0o600, 'API prompt file must be mode 0600'
assert record['env']['DEEPSEEK_DELEGATE_MODEL'] == 'deepseek/deepseek-v4.1-flash-1017', 'API model must be the admitted Flash slug'
assert record['env']['DEEPSEEK_DELEGATE_EFFORT'] == 'max', 'API effort must be max'
assert 1 <= int(record['env']['DEEPSEEK_DELEGATE_TIMEOUT']) <= 590, 'API timeout must reserve 10 seconds'
assert Path(expected).with_suffix('.json').is_file(), 'API JSON sidecar missing'
assert Path(expected).with_suffix('.err').is_file(), 'API stderr sidecar missing'
PY
}

api_fallback() {
    export DEEPSEEK_DELEGATE_MODEL=deepseek/deepseek-v4-pro DEEPSEEK_DELEGATE_EFFORT=xhigh
    invoke --credit-fallback deepseek-api "@$CASE/prompt.txt" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
    assert_calls keycheck 0 "$FAKE_DEEPSEEK_STATE"
    assert_api_result "$CASE/out.md" "$QUERY"
    [[ -f "$CASE/out.codex.err" ]] || fail 'missing failed Codex stderr sidecar'
    assert_grep "$CASE/out.codex.err" '^ERROR: You’ve hit your usage limit[.]$'
    assert_grep_count "$CASE/out.err" '^ERROR: You’ve hit your usage limit[.]$' 0
}

api_stale_sidecars() {
    local suffix
    for suffix in md json err; do
        printf 'stale DeepSeek %s\n' "$suffix" > "$CASE/out.deepseek.$suffix"
    done
    export FAKE_DEEPSEEK_NO_OUTPUT=1 FAKE_DEEPSEEK_RC=9
    invoke --credit-fallback deepseek-api "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 9
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
    assert_grep "$CASE/out.codex.err" '^ERROR: You’ve hit your usage limit[.]$'
    assert_grep_count "$CASE/out.err" '^ERROR: You’ve hit your usage limit[.]$' 0
    assert_grep "$CASE/out.err" '^synthetic DeepSeek launch failure$'
    for suffix in md json err; do
        assert_absent "$CASE/out.deepseek.$suffix"
    done
    assert_grep_count "$CASE/out.md" 'stale DeepSeek' 0
    assert_status "STATUS: FAILED — DeepSeek API exited 9 (see $CASE/out.err)"
    assert_grep_count "$CASE/out.md" '^STATUS:' 1
}

api_success_without_body() {
    local header
    export FAKE_DEEPSEEK_NO_OUTPUT=1 FAKE_DEEPSEEK_RC=0
    invoke --credit-fallback deepseek-api "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 1
    assert_status "STATUS: FAILED — DeepSeek API returned no body (see $CASE/out.err)"
    assert_grep_count "$CASE/out.md" '^STATUS:' 1
    assert_grep "$CASE/out.md" 'exit=1 .*fallback=codex-credit:deepseek-api'
    header="$(head -1 "$CASE/out.md")"
    [[ "$header" == *'| model=deepseek/deepseek-v4.1-flash-1017 | effort=max | fallback=codex-credit:deepseek-api requested=gpt-6.1-sol/xhigh provider=unknown -->' ]] || fail "DeepSeek API fallback header assertion: <$header>"
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
}

api_resolved_without_provider() {
    local header
    export FAKE_DEEPSEEK_RESOLVED=deepseek/other-model
    invoke --credit-fallback deepseek-api "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_envelope "$CASE/out.md" 'STATUS: DONE — fake DeepSeek'
    header="$(head -1 "$CASE/out.md")"
    [[ "$header" == *'| model=deepseek/deepseek-v4.1-flash-1017 | effort=max | fallback=codex-credit:deepseek-api requested=gpt-6.1-sol/xhigh provider=unknown -->' ]] || fail "DeepSeek API no-plus header assertion: <$header>"
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
}

api_timeout_without_body() {
    local api_timeout
    export FAKE_DEEPSEEK_NO_OUTPUT=1 FAKE_DEEPSEEK_RC=124
    invoke --credit-fallback deepseek-api "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 124
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
    api_timeout="$(python3 - "$FAKE_DEEPSEEK_STATE" <<'PY'
import json
import sys
from pathlib import Path
record, = Path(sys.argv[1]).glob('dispatch-*.json')
print(json.loads(record.read_text())['env']['DEEPSEEK_DELEGATE_TIMEOUT'])
PY
)"
    assert_status "STATUS: FAILED — DeepSeek API timed out (inner limit ${api_timeout}s; end-to-end deadline ${CODEX_TIMEOUT}s)"
    assert_grep_count "$CASE/out.md" '^STATUS:' 1
}

api_failure_body() {
    export FAKE_DEEPSEEK_RC="$1" FAKE_DEEPSEEK_BODY='STATUS: FAILED — synthetic API failure'
    invoke --credit-fallback deepseek-api "$QUERY" "$CASE/out.md"
    assert_rc "$RC" "$1"
    assert_envelope "$CASE/out.md" "$FAKE_DEEPSEEK_BODY"
    assert_grep_count "$CASE/out.md" '^STATUS:' 1
    assert_grep "$CASE/out.md" "exit=$1 .*fallback=codex-credit:deepseek-api"
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
}

deadline_guard() {
    export CODEX_TIMEOUT="$2"
    invoke --credit-fallback "$1" "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 124
    assert_grep "$CASE/out.md" '^STATUS: FAILED — deadline:'
    assert_calls call 1
    assert_no_deepseek
}

assert_harness() {
    local call_number="$1" fallback="$2" requested="$3" header
    assert_calls keycheck 1 "$FAKE_DEEPSEEK_STATE"
    assert_calls dispatch 0 "$FAKE_DEEPSEEK_STATE"
    python3 - "$FAKE_CODEX_STATE/call-$call_number.json" "$FAKE_DEEPSEEK_STATE" "$QUERY" <<'PY'
import json
import sys
from pathlib import Path
record = json.loads(Path(sys.argv[1]).read_text())
args = record['argv']
assert args[args.index('--model') + 1] == 'deepseek/deepseek-v4.1-flash-1017@preset/deepseek-codex', 'DeepSeek harness model assertion'
configs = [args[i + 1] for i, value in enumerate(args[:-1]) if value == '-c']
table = r'model_providers.openrouter={name="OpenRouter",base_url="https://openrouter.ai/api/v1",wire_api="responses",auth={command="/bin/sh",args=["-c","cat \"$HOME/.config/openrouter/key\""]}}'
assert configs.count('model_provider=openrouter') == 1, 'OpenRouter provider override missing'
assert configs.count(table) == 1, 'OpenRouter providers table must match the contract exactly: ' + repr(configs)
assert [value for value in configs if value.startswith('model_reasoning_effort=')] == ['model_reasoning_effort=max'], 'DeepSeek effort must be max'
assert sys.argv[3] in args, 'harness prompt changed'
assert record['env']['OPENROUTER_API_KEY'] is None, 'harness inherited OPENROUTER_API_KEY'
assert record['env']['DEEPSEEK_DELEGATE_KEY_FILE'] is None, 'harness inherited DEEPSEEK_DELEGATE_KEY_FILE'
for path in Path(sys.argv[2]).glob('keycheck-*.json'):
    keycheck = json.loads(path.read_text())
    assert keycheck['argv'] == ['--key-check'], 'key check argv changed'
    assert keycheck['env']['OPENROUTER_API_KEY'] is None, 'key check inherited OPENROUTER_API_KEY'
    assert keycheck['env']['DEEPSEEK_DELEGATE_KEY_FILE'] is None, 'key check inherited DEEPSEEK_DELEGATE_KEY_FILE'
    assert keycheck['env']['DEEPSEEK_DELEGATE_TIMEOUT'] == '15', 'key check timeout must be 15 seconds'
first = Path(sys.argv[1]).with_name('call-1.json')
assert json.loads(first.read_text())['cwd'] == record['cwd'], 'fallback changed cwd'
PY
    header="$(head -1 "$CASE/out.md")"
    [[ "$header" == *"| effort=max | fallback=codex-credit:$fallback requested=$requested -->" ]] || fail "DeepSeek harness header assertion: <$header>"
    [[ "$header" == *'| model=deepseek/deepseek-v4.1-flash-1017@preset/deepseek-codex | effort=max | fallback=codex-credit:'* ]] || fail "DeepSeek served-model header assertion: <$header>"
    [[ "$header" != *'provider='* ]] || fail "DeepSeek harness must not claim a serving provider: <$header>"
    assert_envelope "$CASE/out.md" 'STATUS: DONE — fake codex'
}

harness_fallback() {
    export OPENROUTER_API_KEY=synthetic-env-key DEEPSEEK_DELEGATE_KEY_FILE="$CASE/alternate-key"
    printf 'success\n' > "$FAKE_CODEX_STATE/mode-2"
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_calls call 2
    assert_harness 2 deepseek gpt-6.1-sol/xhigh
    [[ -f "$CASE/out.codex.err" ]] || fail 'missing failed Codex stderr sidecar'
    assert_grep "$CASE/out.codex.err" '^ERROR: You’ve hit your usage limit[.]$'
}

keycheck_warn() {
    local call_number=2 fallback=deepseek requested=gpt-6.1-sol/xhigh
    export FAKE_DEEPSEEK_KEY_STDERR='[delegate/deepseek] WARN: key usage at 85% of limit'
    if [[ "$1" == direct ]]; then
        export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017 CODEX_EFFORT=xhigh FAKE_CODEX_MODE=success
        call_number=1; fallback=direct; requested="$CODEX_MODEL/xhigh"
        invoke "$QUERY" "$CASE/out.md"
    else
        printf 'success\n' > "$FAKE_CODEX_STATE/mode-2"
        invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    fi
    assert_rc "$RC" 0
    assert_eq "$(grep -Fxc "$FAKE_DEEPSEEK_KEY_STDERR" "$CASE/stderr" || true)" 1 'caller key-check warning count'
    assert_eq "$(grep -Fxc "$FAKE_DEEPSEEK_KEY_STDERR" "$CASE/out.err" || true)" 1 'published key-check warning count'
    python3 - "$CASE/out.err" "$FAKE_DEEPSEEK_KEY_STDERR" <<'PY'
from pathlib import Path
import sys
stderr = Path(sys.argv[1]).read_text()
assert stderr.index(sys.argv[2]) < stderr.index('synthetic Codex stderr'), 'key-check warning must precede harness stderr'
PY
    assert_calls call "$call_number"
    assert_harness "$call_number" "$fallback" "$requested"
}

harness_outcome() {
    local mode="$1" code="$2" status
    printf '%s\n' "$mode" > "$FAKE_CODEX_STATE/mode-2"
    [[ "$mode" == failure ]] && export FAKE_CODEX_RC="$code"
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" "$code"
    if [[ "$code" == 124 ]]; then
        status="STATUS: FAILED — DeepSeek harness timed out after ${CODEX_TIMEOUT}s (end-to-end deadline)"
    else
        status="STATUS: FAILED — DeepSeek harness exited $code (see $CASE/out.err)"
    fi
    assert_status "$status"
    assert_grep_count "$CASE/out.md" '^STATUS:' 1
    assert_grep "$CASE/out.md" "exit=$code .*fallback=codex-credit:deepseek"
    assert_grep_count "$CASE/out.md" '^STATUS: FAILED — usage-limit' 0
    assert_grep_count "$CASE/stderr" 'retrying' 0
    assert_calls call 2
    assert_calls keycheck 1 "$FAKE_DEEPSEEK_STATE"
    assert_calls dispatch 0 "$FAKE_DEEPSEEK_STATE"
    if [[ "$mode" == account-curly ]]; then
        assert_grep "$CASE/out.err" '^ERROR: You’ve hit your usage limit[.]$'
    fi
}

keycheck_failure() {
    local calls=1 args=(--credit-fallback deepseek)
    if [[ "${1:-}" == direct ]]; then
        export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017
        calls=0; args=()
    fi
    export FAKE_DEEPSEEK_KEY_RC=9
    invoke "${args[@]+"${args[@]}"}" "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 9
    assert_status 'STATUS: FAILED — deepseek key-check: STATUS: FAILED — synthetic key rejected'
    assert_calls call "$calls"
    assert_calls keycheck 1 "$FAKE_DEEPSEEK_STATE"
    assert_calls dispatch 0 "$FAKE_DEEPSEEK_STATE"
}


keycheck_missing_success_status() {
    local calls=1 args=(--credit-fallback deepseek)
    if [[ "${1:-}" == direct ]]; then
        export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017
        calls=0; args=()
    fi
    export FAKE_DEEPSEEK_KEY_STATUS='synthetic unclassified success'
    invoke "${args[@]+"${args[@]}"}" "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 1
    assert_status 'STATUS: FAILED — deepseek key-check: no STATUS line (exit 1)'
    assert_calls call "$calls"
    assert_calls keycheck 1 "$FAKE_DEEPSEEK_STATE"
    assert_calls dispatch 0 "$FAKE_DEEPSEEK_STATE"
}

keycheck_failure_warn() {
    export FAKE_DEEPSEEK_KEY_STDERR='[delegate/deepseek] WARN: key usage at 85% of limit'
    keycheck_failure "$1"
    assert_eq "$(grep -Fxc "$FAKE_DEEPSEEK_KEY_STDERR" "$CASE/stderr" || true)" 1 'failed key-check caller warning count'
    assert_eq "$(grep -Fxc "$FAKE_DEEPSEEK_KEY_STDERR" "$CASE/out.err" || true)" 1 'failed key-check published warning count'
}

midrun_limit() {
    export FAKE_CODEX_ROLLOUT="$1"
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 75
    assert_status "STATUS: FAILED — usage-limit (account, mid-run): Codex credit ran out after tool calls; partial work may exist in $(pwd). Re-dispatch in a fresh worktree with CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017."
    assert_calls call 1
    assert_no_deepseek
}

session_provenance() {
    # An unsafe fallback would otherwise succeed, making the refusal observable.
    printf 'success\n' > "$FAKE_CODEX_STATE/mode-2"
    midrun_limit "$1"
}

capacity_then_account() {
    printf 'capacity\n' > "$FAKE_CODEX_STATE/mode-1"
    printf 'account-curly\n' > "$FAKE_CODEX_STATE/mode-2"
    printf 'success\n' > "$FAKE_CODEX_STATE/mode-3"
    printf '%s\n' "$1" > "$FAKE_CODEX_STATE/rollout-1"
    printf 'zero\n' > "$FAKE_CODEX_STATE/rollout-2"
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    if [[ "$1" == zero ]]; then
        assert_rc "$RC" 0
        assert_calls call 3
        assert_harness 3 deepseek gpt-6.1-sol/xhigh
        assert_grep_count "$CASE/stderr" 'at capacity — retrying \(1/1\)' 1
        return
    fi
    assert_rc "$RC" 75
    assert_status "STATUS: FAILED — usage-limit (account, mid-run): Codex credit ran out after tool calls; partial work may exist in $(pwd). Re-dispatch in a fresh worktree with CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017."
    assert_calls call 2
    assert_grep_count "$CASE/stderr" 'at capacity — retrying \(1/1\)' 1
    assert_no_deepseek
}

forbidden_fallback() {
    export CODEX_EFFORT="$1" FAKE_CODEX_MODE=success
    invoke --credit-fallback "$2" "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 2
    assert_grep "$CASE/stderr" '^Usage error: --credit-fallback is forbidden for max and ultra dispatches$'
    assert_calls call 0
    assert_no_deepseek
    assert_absent "$CASE/out.md"
}

invalid_fallback_form() {
    export FAKE_CODEX_MODE=success
    invoke --credit-fallback=deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 2
    assert_grep "$CASE/stderr" '^Usage: ask_codex.sh \[--credit-fallback off\|deepseek\|deepseek-api\] \[--\] <prompt\|@file> \[output\]$'
    assert_calls call 0
    assert_no_deepseek
    assert_absent "$CASE/out.md"
}

direct_selection() {
    export CODEX_MODEL="$1" CODEX_EFFORT="${2:-xhigh}" FAKE_CODEX_MODE=success
    export OPENROUTER_API_KEY=synthetic-env-key DEEPSEEK_DELEGATE_KEY_FILE="$CASE/alternate-key"
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_calls call 1
    assert_harness 1 direct "$CODEX_MODEL/$CODEX_EFFORT"
    assert_eq "$(grep -Fxc "[delegate/codex] WARN: DeepSeek ignores CODEX_EFFORT=$CODEX_EFFORT; using max" "$CASE/stderr" || true)" 1 'explicit effort warning count'
}

forbidden_direct_selection() {
    local mode="$3" args=()
    export CODEX_MODEL="$1" CODEX_EFFORT="$2" FAKE_CODEX_MODE=success
    [[ "$mode" != none ]] && args=(--credit-fallback "$mode")
    invoke "${args[@]+"${args[@]}"}" "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 2
    assert_eq "$(cat "$CASE/stderr")" "Usage error: DeepSeek is forbidden for max and ultra dispatches (CODEX_MODEL=$CODEX_MODEL)" 'direct refusal stderr'
    assert_calls call 0
    assert_no_deepseek
    assert_absent "$CASE/out.md"
}

direct_selection_default_effort() {
    export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017 FAKE_CODEX_MODE=success
    unset CODEX_EFFORT
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 0
    assert_calls call 1
    assert_grep_count "$CASE/stderr" 'WARN.*(effort|EFFORT)' 0
    assert_harness 1 direct "$CODEX_MODEL/max"
}

unadmitted_selection() {
    export CODEX_MODEL=deepseek/deepseek-v4-pro
    invoke "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 2
    assert_status "STATUS: FAILED — unadmitted DeepSeek model 'deepseek/deepseek-v4-pro'"
    assert_calls call 0
    assert_no_deepseek
}

capacity_unchanged() {
    export FAKE_CODEX_MODE=capacity
    invoke --credit-fallback deepseek "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 1
    assert_status "STATUS: FAILED — capacity: model 'gpt-6.1-sol' at capacity (server). Retry or fall back per /delegate."
    assert_calls call 2
    assert_grep_count "$CASE/stderr" 'at capacity — retrying \(1/1\)' 1
    assert_grep "$CASE/stderr" "at capacity after 2 attempt\(s\)"
    assert_no_deepseek
}

environment_not_optin() {
    export CODEX_CREDIT_FALLBACK=deepseek
    account_limit account-curly
    assert_grep "$CASE/stderr" 'WARN.*CODEX_CREDIT_FALLBACK'
}

both_refusal() {
    local code=0
    export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017 INCLUDE_GROK=0
    export FAKE_AGY_PRINT_REPLY=$'VERDICT: CORRECT\n' ANTIGRAVITY_LOW_QUOTA=abc
    assert_eq "$(command -v agy)" "$TESTS/fakes/agy" 'Antigravity must use the offline fake'
    bash "$SCRIPTS/ask_both.sh" "$QUERY" "$CASE/out.md" '' "$CASE/agy.md" > "$CASE/stdout" 2> "$CASE/stderr" || code=$?
    assert_rc "$code" 2
    assert_grep "$CASE/stderr" '^ERROR: review Codex roles never fall back to DeepSeek \(CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017\)$'
    assert_calls call 0
    assert_eq "$(wc -l < "$FAKE_AGY_LEDGER" | tr -d ' ')" 0 'no Antigravity leg may start'
    assert_no_deepseek
}

concurrent_api() {
    local first second code1=0 code2=0
    mkdir -p "$CASE/codex-first" "$CASE/codex-second"
    FAKE_CODEX_STATE="$CASE/codex-first" bash "$SCRIPTS/ask_codex.sh" --credit-fallback deepseek-api "$QUERY first" "$CASE/first.md" > "$CASE/first.stdout" 2> "$CASE/first.stderr" &
    first=$!
    FAKE_CODEX_STATE="$CASE/codex-second" bash "$SCRIPTS/ask_codex.sh" --credit-fallback deepseek-api "$QUERY second" "$CASE/second.md" > "$CASE/second.stdout" 2> "$CASE/second.stderr" &
    second=$!
    wait "$first" || code1=$?
    wait "$second" || code2=$?
    assert_rc "$code1" 0
    assert_rc "$code2" 0
    assert_calls call 1 "$CASE/codex-first"
    assert_calls call 1 "$CASE/codex-second"
    assert_calls dispatch 2 "$FAKE_DEEPSEEK_STATE"
    assert_api_result "$CASE/first.md" "$QUERY first"
    assert_api_result "$CASE/second.md" "$QUERY second"
}

batch_credit() {
    local code=0
    printf '%s\t@%s\n' "$CASE/task1.md" "$CASE/prompt.txt" > "$CASE/manifest.tsv"
    bash "$SCRIPTS/ask_codex_batch.sh" --credit-fallback deepseek-api "$CASE/manifest.tsv" > "$CASE/stdout" 2> "$CASE/stderr" || code=$?
    assert_rc "$code" 0
    assert_grep "$CASE/stdout" '^BATCH: 1/1 succeeded \| task1:DONE\[deepseek\]$'
    assert_calls call 1
    assert_calls dispatch 1 "$FAKE_DEEPSEEK_STATE"
    assert_api_result "$CASE/task1.md" "$QUERY"
}

batch_invalid_fallback_form() {
    local code=0
    bash "$SCRIPTS/ask_codex_batch.sh" --credit-fallback=deepseek > "$CASE/stdout" 2> "$CASE/stderr" || code=$?
    assert_rc "$code" 2
    assert_eq "$(cat "$CASE/stderr")" 'Usage: ask_codex_batch.sh [--credit-fallback off|deepseek|deepseek-api] manifest.tsv' 'batch usage'
    assert_calls call 0
    assert_no_deepseek
}

batch_forbidden_fallback() {
    local code=0
    export CODEX_EFFORT="$1"
    printf '%s\t@%s\n' "$CASE/task1.md" "$CASE/prompt.txt" > "$CASE/manifest.tsv"
    bash "$SCRIPTS/ask_codex_batch.sh" --credit-fallback "$2" "$CASE/manifest.tsv" > "$CASE/stdout" 2> "$CASE/stderr" || code=$?
    assert_rc "$code" 2
    assert_eq "$(cat "$CASE/stderr")" 'Usage error: --credit-fallback is forbidden for max and ultra dispatches' 'batch refusal stderr'
    assert_calls call 0
    assert_no_deepseek
    assert_absent "$CASE/task1.md"
}

batch_stale_output() {
    local code=0
    export CODEX_MODEL=deepseek/deepseek-v4.1-flash-1017 CODEX_EFFORT=max FAKE_CODEX_MODE=success
    printf '%s\t@%s\n' "$CASE/task1.md" "$CASE/prompt.txt" > "$CASE/manifest.tsv"
    printf '<!-- stale | fallback=codex-credit:deepseek -->\n\nSTATUS: DONE — stale\n' > "$CASE/task1.md"
    printf 'wrapper-owned sidecar\n' > "$CASE/task1.err"
    bash "$SCRIPTS/ask_codex_batch.sh" "$CASE/manifest.tsv" > "$CASE/stdout" 2> "$CASE/stderr" || code=$?
    assert_rc "$code" 1
    assert_eq "$(cat "$CASE/stdout")" 'BATCH: 0/1 succeeded | task1:RUNTIME_FAILED' 'stale-output batch summary'
    assert_absent "$CASE/task1.md"
    assert_eq "$(cat "$CASE/task1.err")" 'wrapper-owned sidecar' 'batch must leave sidecars to ask_codex.sh'
    assert_calls call 0
    assert_no_deepseek
}

mutation_account_anchor() {
    local code=0
    python3 - "$SCRIPTS/ask_codex.sh" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text()
patterns = ('^ERROR: You.{1,3}ve hit your usage limit[.]', '^ERROR: Your workspace is out of credits', '^ERROR: You hit your spend cap', '^ERROR: You.{1,3}re out of credits')
for pattern in patterns:
    assert pattern in source, 'account classifier anchor changed: ' + pattern
    source = source.replace(pattern, pattern[1:])
path.write_text(source)
PY
    (quoted_limit indented) > "$CASE/mutant.stdout" 2> "$CASE/mutant.stderr" || code=$?
    assert_rc "$code" 1
    assert_grep "$CASE/mutant.stderr" '^exit code: expected <1>, got <75>$'
    assert_status "STATUS: FAILED — usage-limit (account): Codex credit exhausted (model 'gpt-6.1-sol'). Fall back per /delegate (--credit-fallback)."
}

unknown_mode() {
    invoke --credit-fallback unknown "$QUERY" "$CASE/out.md"
    assert_rc "$RC" 2
    assert_calls call 0
    assert_no_deepseek
}

total=0; failed=0
run_case() {
    local name="$1" code detail; shift
    total=$((total + 1))
    CASE="$RUN/$name"; mkdir -p "$CASE"
    set +e
    (set -e; setup; "$@") > "$CASE/check.stdout" 2> "$CASE/check.stderr"
    code=$?
    set -e
    if [[ "$code" == 0 ]]; then
        echo "PASS $name"
    else
        detail="$(tail -1 "$CASE/check.stderr")"
        echo "FAIL $name: ${detail:-case exited $code} (artifacts: $CASE)"
        failed=$((failed + 1))
    fi
}

run_case account-curly account_limit account-curly
run_case account-ascii account_limit account-ascii
run_case account-no-flag-after-tool-calls account_no_flag_after_tool_calls
run_case ordinary-success ordinary_success
run_case model-usage-limit model_limit
run_case quoted-account-message quoted_limit quoted
run_case indented-account-message quoted_limit indented
run_case deepseek-api-fallback api_fallback
run_case deepseek-api-stale-sidecars api_stale_sidecars
run_case deepseek-api-success-no-body api_success_without_body
run_case deepseek-api-resolved-no-plus api_resolved_without_provider
run_case deepseek-api-timeout-no-body api_timeout_without_body
run_case deepseek-api-failure-body api_failure_body 9
run_case deepseek-api-timeout-body api_failure_body 124
run_case deepseek-api-deadline deadline_guard deepseek-api 40
run_case deepseek-harness-deadline deadline_guard deepseek 80
run_case deepseek-harness-fallback harness_fallback
run_case fallback-keycheck-warn keycheck_warn fallback
run_case direct-keycheck-warn keycheck_warn direct
run_case harness-exit3 harness_outcome failure 3
run_case harness-empty-success harness_outcome empty-success 1
run_case harness-timeout harness_outcome exit124 124
run_case harness-usage-limit harness_outcome account-curly 1
run_case deepseek-keycheck-failure keycheck_failure
run_case deepseek-keycheck-missing-status keycheck_missing_success_status
run_case direct-keycheck-failure keycheck_failure direct
run_case direct-keycheck-missing-status keycheck_missing_success_status direct
run_case fallback-keycheck-failure-warn keycheck_failure_warn fallback
run_case direct-keycheck-failure-warn keycheck_failure_warn direct
run_case midrun-tool-call midrun_limit call
run_case midrun-missing-rollout midrun_limit missing
run_case midrun-quoted-session session_provenance quoted
run_case midrun-malformed-session session_provenance malformed
run_case capacity-then-account-with-calls capacity_then_account call
run_case capacity-then-account-unknown-session capacity_then_account malformed
run_case capacity-then-account-clean capacity_then_account zero
run_case forbidden-max-deepseek forbidden_fallback max deepseek
run_case forbidden-ultra-deepseek forbidden_fallback ultra deepseek
run_case forbidden-max-deepseek-api forbidden_fallback max deepseek-api
run_case forbidden-ultra-deepseek-api forbidden_fallback ultra deepseek-api
run_case invalid-credit-flag-form invalid_fallback_form
run_case direct-deepseek direct_selection deepseek/deepseek-v4.1-flash-1017
run_case direct-deepseek-preset direct_selection deepseek/deepseek-v4.1-flash-1017@preset/deepseek-codex
run_case direct-deepseek-high direct_selection deepseek/deepseek-v4.1-flash-1017 high
run_case direct-deepseek-default-effort direct_selection_default_effort
for model in deepseek/deepseek-v4.1-flash-1017 deepseek/deepseek-v4.1-flash-1017@preset/deepseek-codex deepseek/deepseek-v4-pro; do
    for effort in max ultra; do
        for mode in none deepseek deepseek-api; do
            run_case "direct-forbidden-${model##*/}-$effort-$mode" forbidden_direct_selection "$model" "$effort" "$mode"
        done
    done
done
run_case unadmitted-deepseek unadmitted_selection
run_case capacity-unchanged capacity_unchanged
run_case environment-not-optin environment_not_optin
run_case both-refuses-deepseek both_refusal
run_case concurrent-api-fallbacks concurrent_api
run_case batch-credit-label batch_credit
run_case batch-invalid-credit-flag-form batch_invalid_fallback_form
run_case batch-stale-output batch_stale_output
for effort in max ultra; do
    for mode in deepseek deepseek-api; do
        run_case "batch-forbidden-$effort-$mode" batch_forbidden_fallback "$effort" "$mode"
    done
done
run_case mutation-account-anchor mutation_account_anchor
run_case unknown-credit-mode unknown_mode

if [[ "$failed" == 0 && "$total" -gt 0 ]]; then echo "SUITE: PASS $total/$total"; exit 0; fi
echo "SUITE: FAIL $failed/$total"
exit 1
