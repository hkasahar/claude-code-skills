#!/usr/bin/env python3
"""Run exported tests in scratch with no inherited credentials or live CLI path."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOOLS = 'bash sh perl jq awk grep sed tr wc cat head tail stat id date mktemp mkdir rmdir rm mv cp find chmod sleep dirname basename touch ln shasum sha256sum openssl kill ps pgrep pkill xargs tee sort uniq cmp diff cut printf seq true false env readlink realpath timeout gtimeout pdfinfo pdftotext pdfseparate pdfunite pdflatex'.split()
BLOCKED = 'codex agy grok claude curl wget ssh scp sftp npx npm pip pip3 uv git'.split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('delegate', 'pdf', 'all'), default='all')
    parser.add_argument('--extra-python-path', type=Path, help='Explicit existing read-only test dependency directory; ambient PYTHONPATH is ignored.')
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix='skills-offline-'))
    repo = scratch / 'repo'
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv', 'node_modules', '*.pyc'))
    tools = scratch / 'tools'
    tools.mkdir()
    for name in LOCAL_TOOLS:
        executable = shutil.which(name)
        if executable:
            (tools / name).symlink_to(executable)
    for name in ('python3', 'python'):
        (tools / name).symlink_to(sys.executable)
    for name in BLOCKED:
        target = tools / name
        target.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + '" >> "$OFFLINE_BLOCKED_LOG"\necho "Blocked unexpected external command: ' + name + '" >&2\nexit 97\n')
        target.chmod(0o755)
    home = scratch / 'home'
    tmp = scratch / 'tmp'
    for path in (home, tmp, scratch / 'config', scratch / 'cache', scratch / 'reference', scratch / 'codex', scratch / 'claude', scratch / 'gemini'):
        path.mkdir()
    environment = {
        'PATH': str(tools), 'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C', 'LC_ALL': 'C',
        'XDG_CONFIG_HOME': str(scratch / 'config'), 'XDG_CACHE_HOME': str(scratch / 'cache'),
        'CODEX_HOME': str(scratch / 'codex'), 'CLAUDE_CONFIG_DIR': str(scratch / 'claude'),
        'GEMINI_CONFIG_DIR': str(scratch / 'gemini'), 'REFERENCE_DIR': str(scratch / 'reference'),
        'GEMINI_PDF_REFERENCE_DIR': '', 'GEMINI_PDF_MATHPIX_LEDGER': str(scratch / 'mathpix.tsv'),
        'DEEPSEEK_DELEGATE_LEDGER': str(scratch / 'deepseek.tsv'),
        'OFFLINE_BLOCKED_LOG': str(scratch / 'blocked.log'), 'PYTHONDONTWRITEBYTECODE': '1',
        'REAL_PYTHON3': sys.executable, 'OFFLINE_TEST_ROOT': str(scratch),
    }
    if args.extra_python_path:
        dependency_path = args.extra_python_path.absolute()
        if not dependency_path.is_dir():
            parser.error('The explicit test dependency directory does not exist.')
        environment['PYTHONPATH'] = str(dependency_path)
        environment['OFFLINE_TEST_PYTHONPATH'] = str(dependency_path)
    for name in BLOCKED:
        assert shutil.which(name, path=environment['PATH']) == str(tools / name)
    commands = []
    if args.suite in ('delegate', 'all'):
        commands += [(['python3', 'tests/test_public_contract.py'], 'skills/delegate'),
                     (['bash', 'tests/test_codex_credit.sh'], 'skills/delegate')]
    if args.suite in ('pdf', 'all'):
        commands += [(['bash', 'tests/run_offline_suite.sh', '--unit'], 'skills/gemini-pdf'),
                     (['bash', 'tests/run_offline_suite.sh'], 'skills/gemini-pdf')]
    results = []
    for index, (command, folder) in enumerate(commands, 1):
        print('RUN ' + folder + ': ' + ' '.join(command), flush=True)
        process = subprocess.Popen(command, cwd=repo / folder, env=environment, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=550)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
            exit_code = 124
        log = scratch / ('test-' + str(index) + '.log')
        log.write_text(output)
        results.append({'command': command, 'folder': folder, 'exit': exit_code, 'log': str(log)})
        print(output[-6000:], end='', flush=True)
        blocked_log = scratch / 'blocked.log'
        if blocked_log.exists() and blocked_log.stat().st_size:
            results[-1]['exit'] = exit_code = 97
            print('FAIL: unexpected blocked external command; inspect scratch call record.', flush=True)
        if exit_code:
            print('STOP: failed verification; evidence retained at ' + str(scratch), flush=True)
            break
    (scratch / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print('Evidence: ' + str(scratch), flush=True)
    return int(len(results) != len(commands) or any(row['exit'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
