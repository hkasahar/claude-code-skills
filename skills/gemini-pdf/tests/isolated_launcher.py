#!/usr/bin/env python3
"""Run reviewed synthetic tests with an empty environment and local-only tools."""
from pathlib import Path
import os
import shutil
import shlex
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
# Resolve local utilities from fixed conventional locations, never caller PATH.
SEARCH_PATH = os.pathsep.join(('/usr/bin', '/bin', '/usr/local/bin', '/opt/homebrew/bin', '/Library/TeX/texbin'))
LOCAL_TOOLS = '''bash sh perl jq pdfinfo pdftotext pdfseparate pdfunite pdflatex
awk sed grep cut sort head tail find date wc tr cat cp mv rm mkdir rmdir mktemp
basename dirname sleep pgrep shasum touch chmod diff tee env'''.split()
BLOCKED_TOOLS = '''agy antigravity gemini codex claude grok deepseek curl wget
node npm npx pip pip3 uv git ssh scp rsync docker mineru huggingface-cli markitdown make'''.split()

SOCKET_GUARD = r'''
import os
import socket
from pathlib import Path
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo
_ALLOWED = {'127.0.0.1', '::1', 'localhost'}
def _record(message):
    with Path(os.environ['GEMINI_PDF_OFFLINE_NETWORK_LOG']).open('a') as stream:
        stream.write(message + '\n')
def _address(address):
    if not isinstance(address, tuple) or address[0] not in _ALLOWED:
        _record('BLOCKED ' + repr(address))
        raise RuntimeError('offline tests refuse nonloopback sockets')
    _record('LOOPBACK ' + repr(address))
def _connect(self, address):
    _address(address)
    return _original_connect(self, address)
def _connect_ex(self, address):
    _address(address)
    return _original_connect_ex(self, address)
def _getaddrinfo(host, *args, **kwargs):
    if host not in _ALLOWED:
        _record('BLOCKED DNS ' + repr(host))
        raise RuntimeError('offline tests refuse external DNS')
    return _original_getaddrinfo(host, *args, **kwargs)
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _getaddrinfo
'''


def main():
    unit_mode = len(sys.argv) > 1 and sys.argv[1] == '--unit'
    root = Path(tempfile.mkdtemp(prefix='gemini-pdf-offline-'))
    print('ISOLATED SCRATCH: ' + str(root), flush=True)
    copy = root / 'package'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    local_bin = root / 'bin'
    local_bin.mkdir()
    for name in LOCAL_TOOLS:
        target = shutil.which(name, path=SEARCH_PATH)
        if target is None:
            if name == 'pdflatex' and unit_mode:
                continue
            raise RuntimeError('required local test tool missing: ' + name)
        (local_bin / name).symlink_to(Path(target).resolve())
    interpreter = local_bin / 'python3'
    interpreter.write_text('#!/bin/bash\nexec ' + shlex.quote(sys.executable) + ' \"$@\"\n')
    interpreter.chmod(0o755)
    guard = root / 'socket-guard'
    guard.mkdir()
    (guard / 'sitecustomize.py').write_text(SOCKET_GUARD)
    block = local_bin / 'blocked-provider'
    block.write_text('#!/bin/bash\nprintf "%s\\n" "BLOCKED ${0##*/}" >> "${GEMINI_PDF_OFFLINE_BLOCK_LOG:?}"\nexit 97\n')
    block.chmod(0o755)
    for name in BLOCKED_TOOLS:
        (local_bin / name).symlink_to(block)
    for directory in ('home', 'tmp', 'config', 'cache', 'data', 'reference', 'ledger', 'texmf-var', 'texmf-config', 'texmf-home'):
        (root / directory).mkdir()
    extra_modules = os.environ.get('OFFLINE_TEST_PYTHONPATH', '')
    if extra_modules and not all(Path(part).is_dir() for part in extra_modules.split(os.pathsep)):
        raise RuntimeError('OFFLINE_TEST_PYTHONPATH must contain explicit dependency directories')
    module_path = str(guard) + (os.pathsep + extra_modules if extra_modules else '')
    env = {
        'PATH': str(local_bin), 'HOME': str(root / 'home'), 'TMPDIR': str(root / 'tmp'),
        'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_CACHE_HOME': str(root / 'cache'),
        'XDG_DATA_HOME': str(root / 'data'), 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1', 'PYTHONUTF8': '1', 'PYTHONPATH': module_path,
        'LANG': 'C', 'LC_ALL': 'C', 'GEMINI_PDF_OFFLINE_ISOLATED': '1',
        'GEMINI_PDF_OFFLINE_BLOCK_LOG': str(root / 'blocked-external.tsv'),
        'GEMINI_PDF_OFFLINE_NETWORK_LOG': str(root / 'network.tsv'),
        'OFFLINE_TEST_PYTHONPATH': extra_modules,
        'GEMINI_PDF_MATHPIX_LEDGER': str(root / 'ledger' / 'mathpix.tsv'),
        'TEXMFVAR': str(root / 'texmf-var'), 'TEXMFCONFIG': str(root / 'texmf-config'),
        'TEXMFHOME': str(root / 'texmf-home'),
    }
    for name in BLOCKED_TOOLS:
        if Path(shutil.which(name, path=env['PATH'])).resolve() != block.resolve():
            raise RuntimeError('provider stub did not resolve: ' + name)
    # Prove the child guard is loaded before any test process runs.
    probe = subprocess.run([str(local_bin / 'python3'), '-c',
                            'import socket,sitecustomize; assert socket.socket.connect is sitecustomize._connect'],
                           env=env, cwd=root, check=False)
    if probe.returncode:
        return probe.returncode
    # Compilation and shell parsing touch only scratch files and do not import them.
    import json
    import py_compile
    for index, source in enumerate(sorted(copy.rglob('*.py'))):
        py_compile.compile(str(source), cfile=str(root / 'cache' / (str(index) + '.pyc')), doraise=True)
    for source in sorted(copy.rglob('*.sh')):
        subprocess.run([str(local_bin / 'bash'), '-n', str(source)], env=env, cwd=root, check=True)
    for source in sorted(copy.rglob('*.json')):
        json.loads(source.read_text())
    tests = copy / 'tests'
    if unit_mode:
        if sys.argv[2:]:
            if not all(name.startswith('test_') and name.replace('_', '').isalnum() for name in sys.argv[2:]):
                raise RuntimeError('unit selectors must be test module names')
            command = [str(local_bin / 'python3'), '-m', 'unittest', '-v', *sys.argv[2:]]
        else:
            command = [str(local_bin / 'python3'), '-m', 'unittest', 'discover', '-s', str(tests), '-p', 'test_*.py', '-v']
    else:
        command = [str(local_bin / 'python3'), str(tests / 'offline_suite.py'), *sys.argv[1:]]
    result = subprocess.run(command, cwd=tests if unit_mode else root, env=env)
    blocked = root / 'blocked-external.tsv'
    network = root / 'network.tsv'
    if (blocked.exists() and blocked.stat().st_size) or (network.exists() and 'BLOCKED ' in network.read_text()):
        print('FAIL: unexpected external command/socket attempt; inspect scratch logs', file=sys.stderr)
        return 1
    print('ISOLATION: no external provider/HTTP attempts; temporary HOME and restricted PATH', flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
