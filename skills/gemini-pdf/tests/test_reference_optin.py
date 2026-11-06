#!/usr/bin/env python3
"""An explicit sync destination is required even when a BibTeX key is given."""
from pathlib import Path
import os
import json
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'sync_to_reference.py'


class ReferenceOptInTests(unittest.TestCase):
    def test_no_destination_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.md'
            source.write_text('# Synthetic source\n')
            env = dict(os.environ)
            env.pop('REFERENCE_DIR', None)
            env.pop('GEMINI_PDF_REFERENCE_DIR', None)
            before = sorted(root.iterdir())
            result = subprocess.run([sys.executable, str(SCRIPT), '--key', 'synthetic', '--md', str(source)],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('explicit', result.stderr)
            self.assertEqual(sorted(root.iterdir()), before)

    def test_explicit_destination_and_env_precedence(self):
        for setting in ('--central-ref', 'GEMINI_PDF_REFERENCE_DIR', 'REFERENCE_DIR'):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'input.md'
                source.write_text('---\ntitle: Synthetic title\nauthors: Fixture Author\nyear: 2026\n---\n# Synthetic title\n')
                target = root / 'opt-in'
                env = dict(os.environ)
                env.pop('REFERENCE_DIR', None)
                env.pop('GEMINI_PDF_REFERENCE_DIR', None)
                args = [sys.executable, str(SCRIPT), '--key', 'synthetic', '--md', str(source)]
                if setting == '--central-ref':
                    args += [setting, str(target)]
                else:
                    env[setting] = str(target)
                if setting == 'GEMINI_PDF_REFERENCE_DIR':
                    env['REFERENCE_DIR'] = str(root / 'unused-legacy-target')
                result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((target / 'md' / 'synthetic.md').read_bytes(), source.read_bytes())
                self.assertFalse((root / 'unused-legacy-target').exists())

    def test_metadata_types_are_generic_and_validated(self):
        for value, expected in [('book', 'book'), (['book'], 'techreport')]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'input.md'
                source.write_text('# Synthetic source\n')
                sidecar = root / 'metadata.json'
                sidecar.write_text(json.dumps({'title': 'Synthetic title', 'entry_type': value}))
                target = root / 'reference'
                result = subprocess.run([sys.executable, str(SCRIPT), '--key', 'synthetic',
                                         '--md', str(source), '--sidecar', str(sidecar),
                                         '--central-ref', str(target)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((target / 'reference.bib').read_text().find('@' + expected + '{synthetic,') >= 0)


if __name__ == '__main__':
    unittest.main()
