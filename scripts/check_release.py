#!/usr/bin/env python3
"""Check intended public files or the actual Git index for packaging/leakage errors.

This is a bounded release check, not a guarantee of anonymity or historical erasure.
User-specific markers and manual audit reports belong outside the public repository.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.claude', '.claude-delegate', 'private', 'wp0', 'wp1'}
FORBIDDEN_NAMES = {'auth.json', 'oauth_token', 'settings.local.json', '.env', '.DS_Store'}
PATTERNS = {
    'private-key material': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential-shaped literal': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-|ant-api03-)?[A-Za-z0-9_-]{30,})\b'),
    'personal home path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+|[A-Za-z]:\\Users\\[^\\\s]+'),
}
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')
SYNTHETIC_DOMAINS = {'example.com', 'example.net', 'example.org', 'users.noreply.github.com'}
MARKDOWN_LINK = re.compile(r'(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def public_files(staged):
    files = {}
    if staged:
        for entry in git('ls-files', '--stage', '-z').split(b'\0'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, object_id, stage = metadata.decode().split()
            path = name.decode()
            if stage != '0':
                raise ValueError('Unmerged index entry: ' + path)
            if mode == '120000':
                raise ValueError('Symlinks cannot enter the public export: ' + path)
            files[path] = git('cat-file', 'blob', object_id)
    else:
        names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0')
        for name in names:
            if not name:
                continue
            path = name.decode()
            target = ROOT / path
            if target.is_symlink():
                raise ValueError('Symlinks cannot enter the public export: ' + path)
            if target.is_file():
                files[path] = target.read_bytes()
    return files


def inspect(files):
    findings = []
    decoded = {}
    for name, data in sorted(files.items()):
        path = PurePosixPath(name)
        if any(p in FORBIDDEN_PARTS for p in path.parts) or path.name in FORBIDDEN_NAMES:
            findings.append(name + ': forbidden local/runtime filename')
        if '.bak' in path.name or path.name.startswith('.env.') or path.suffix in {'.pyc', '.pyo', '.log', '.err', '.pem', '.key', '.p12', '.pfx'}:
            findings.append(name + ': backup, runtime, or credential artifact')
        if path.suffix.lower() == '.pdf' and name != 'skills/delegate/tests/fixtures/fixture.pdf':
            findings.append(name + ': PDF is outside the synthetic fixture allowlist')
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            if name != 'skills/delegate/tests/fixtures/fixture.pdf':
                findings.append(name + ': unreviewed binary file')
            continue
        decoded[name] = text
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(name + ': ' + label + ' (value withheld)')
        for match in EMAIL.finditer(text):
            domain = match.group(1).lower()
            if domain not in SYNTHETIC_DOMAINS and not domain.endswith(('.invalid', '.test', '.example', '.localhost')):
                findings.append(name + ': non-example email address (value withheld)')
                break
        if path.name == 'SKILL.md':
            if not text.startswith('---\n') or '\n---\n' not in text[4:]:
                findings.append(name + ': missing skill frontmatter')
            else:
                header = text.split('---', 2)[1]
                if not re.search(r'^name:\s*' + re.escape(path.parent.name) + r'\s*$', header, re.M):
                    findings.append(name + ': skill name does not match directory')
                if not re.search(r'^description:\s*\S', header, re.M):
                    findings.append(name + ': missing skill description')
        if path.suffix == '.md':
            for match in MARKDOWN_LINK.finditer(text):
                link = match.group(1).strip().strip('<>')
                if not link or link.startswith(('#', '/', '$', '~')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', link):
                    continue
                link = unquote(link.split('#', 1)[0].split('?', 1)[0])
                if not link:
                    continue
                # Pure path normalization checks the selected Git tree, not disk state.
                parts = []
                for part in (path.parent / link).parts:
                    if part == '..':
                        if parts: parts.pop()
                    elif part != '.': parts.append(part)
                target = '/'.join(parts)
                if target not in files and not any(p.startswith(target.rstrip('/') + '/') for p in files):
                    findings.append(name + ': missing local link target ' + target)
    try:
        manifest = json.loads(decoded['.claude-plugin/marketplace.json'])
        assert manifest['name'] and manifest['metadata']['version']
        for plugin in manifest['plugins']:
            assert plugin['version'] and plugin['license'] == 'MIT'
            for skill in plugin['skills']:
                assert skill.removeprefix('./') + '/SKILL.md' in files
    except (KeyError, ValueError, AssertionError, TypeError):
        findings.append('.claude-plugin/marketplace.json: invalid plugin/skill packaging')
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='Read actual staged blobs, including newly added files.')
    args = parser.parse_args()
    try:
        files = public_files(args.staged)
        findings = inspect(files)
    except (ValueError, subprocess.CalledProcessError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    if findings:
        for finding in findings:
            print('FAIL: ' + finding, file=sys.stderr)
        return 1
    print('PASS: ' + str(len(files)) + (' staged' if args.staged else ' intended') + ' public files; packaging, links, and configured leakage patterns.')
    print('Manual review and a separate history assessment are still required.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
