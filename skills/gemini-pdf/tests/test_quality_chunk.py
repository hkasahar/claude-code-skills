#!/usr/bin/env python3
"""T12: chunk fidelity gates and the frozen document profile, without network."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import quality_check as quality


FIRST = 'The first sample contains carefully measured observations from several independent regional markets.'
MISSING = (
    'Independent households choose durable products after comparing every available feature across local suppliers. '
    'Their detailed purchasing decisions reveal substantial variation across geographic areas and income groups.'
)
LAST = 'The final analysis reports the complete empirical results with sufficient detail for independent replication.'
SOURCE = FIRST + '\n' + MISSING + '\n' + LAST + '\nValues 0.019 0.047 2013.\f'
TABLE = '| Estimate | Standard error |\n|---|---|\n| $0.019$ | $0.047$ |\n'
TABLE_DOC = FIRST + '\n' + TABLE + MISSING + '\nAnother statistic is $x_i$.\n' + LAST


class ChunkQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='quality-T12-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        quality.ACTIVE_PROFILE = 'document'
        self.temp.cleanup()

    def score(self, candidate=SOURCE, source=SOURCE, **kwargs):
        md = self.root / 'candidate.md'
        src = self.root / 'source.txt'
        md.write_text(candidate)
        src.write_text(source)
        return quality.run_quality_check(str(md), None, str(src), 60, **kwargs)

    def test_inline_table_keeps_prose_only_in_chunk(self):
        quality.ACTIVE_PROFILE = 'document'
        legacy = quality.normalize_for_trigrams(TABLE_DOC, drop_repeated_lines=False)
        quality.ACTIVE_PROFILE = 'chunk'
        normalized = quality.normalize_for_trigrams(TABLE_DOC, drop_repeated_lines=False)
        prose = FIRST + MISSING + LAST
        self.assertGreaterEqual(len(normalized), 0.8 * len(prose))
        self.assertIn(MISSING.lower(), normalized)
        self.assertNotIn(MISSING.lower(), legacy)
        quality.ACTIVE_PROFILE = 'document'
        self.assertEqual(legacy, quality.normalize_for_trigrams(TABLE_DOC, drop_repeated_lines=False))

    def test_document_profile_golden(self):
        report = self.score(TABLE_DOC)
        report.pop('path')
        digest = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, '0f6c9040a952ebcdd17e311277c74cc3d2f5cc3e58e76ba4b02d9e55a9905e71')
        self.assertNotIn('chunk_gate', report)
        self.score(profile='chunk')
        again = self.score(TABLE_DOC)
        again.pop('path')
        self.assertEqual(report, again)

    def test_clean_gate_and_weights(self):
        report = self.score(profile='chunk', first_page=6)
        self.assertTrue(report['pass'])
        self.assertEqual(report['chunk_gate']['failed'], [])
        self.assertEqual(report['missing_sentences'], 0)
        self.assertEqual(report['sentences'], 4)
        self.assertEqual(list(report['page_checks']), ['6'])
        for key, weight in quality.CHUNK_WEIGHTS.items():
            self.assertEqual(report['dimensions'][key]['weight'], weight)

    def test_missing_sentences_trip_page_floor(self):
        report = self.score(SOURCE.replace(MISSING, ''), profile='chunk', first_page=7)
        self.assertIn('missing_sentences_page_max', report['chunk_gate']['failed'])
        self.assertEqual(report['missing_sentences'], 2)
        missing = report['page_checks']['7']['missing_sentences']
        self.assertEqual(missing['count'], 2)
        self.assertTrue(missing['heads'][0].startswith('independent households'))
        self.assertTrue(all(len(head) <= 90 for head in missing['heads']))

    def test_numeric_and_doi_floors(self):
        report = self.score(SOURCE.replace('0.019 0.047 2013', '') + '\nReference: 10.1234/fabricated.', profile='chunk')
        self.assertEqual(report['chunk_gate']['values']['numeric'], 0)
        self.assertIn('numeric', report['chunk_gate']['failed'])
        self.assertIn('0.019', report['missing_numerals'])
        self.assertEqual(report['injected_dois'], 1)
        self.assertEqual(report['injected_doi_list'], ['10.1234/fabricated'])
        self.assertIn('injected_dois', report['chunk_gate']['failed'])
        good = self.score(SOURCE + '\nDOI: 10.1234/PRINTED.', SOURCE + '\nDOI: 10.1234/printed', profile='chunk')
        self.assertEqual(good['injected_dois'], 0)
        metadata = self.score('---\ntitle: Sample\ndoi: 10.1234/fabricated\n---\n' + SOURCE, profile='chunk')
        self.assertEqual(metadata['injected_dois'], 1)
        self.assertIn('injected_dois', metadata['chunk_gate']['failed'])

    def test_page_furniture_is_excluded_from_every_numeric_check(self):
        folder = self.root / 'pages'
        folder.mkdir()
        bodies = [
            'The regional estimate equals 0.019 after adjusting household income and local market conditions.',
            'The national estimate equals 0.047 after accounting for observed variation in measured outcomes.',
            'The baseline estimate equals 0.125 when researchers combine separate observations from independent samples.',
            'The final estimate equals 0.250 and supports the reported comparison across multiple empirical specifications.',
        ]
        pages = []
        for page, body in enumerate(bodies, 517):
            text = '%s\n%s\nDownloaded from https://example.test/paper 2026\nhttps://doi.org/10.1162/\nrest_a_00953\n' % (page, body)
            pages.append(text)
            (folder / ('page_%s.txt' % (page - 516))).write_text(text)
        report = self.score('\n'.join(bodies), '\f'.join(pages),
                            profile='chunk', page_texts_dir=str(folder))
        self.assertEqual(report['page_number_run'], {'start': 517, 'end': 520})
        self.assertEqual(report['chunk_gate']['values']['numeric'], 1.0)
        self.assertEqual(report['missing_numerals'], [])
        self.assertEqual(report['dimensions']['numeric_fidelity']['score'], 100)
        self.assertIn('517', report['dimensions']['numeric_fidelity']['details'])
        self.assertIn('520', report['dimensions']['numeric_fidelity']['details'])
        for page in report['page_checks'].values():
            self.assertEqual(page['numeric_recall'], 1.0)
            self.assertEqual(page['numerals'], 1)
            self.assertEqual(page['missing_numerals'], [])

    def test_furniture_numeral_still_required_when_printed_in_body(self):
        body = FIRST + '\nThe final sample in 2026 contains carefully documented household observations from several markets.'
        footer = '\nDownloaded from https://example.test/paper 2026\n'
        other_pages = MISSING.split('. ') + [LAST]
        source = '\f'.join([body + footer] + [page + footer for page in other_pages])
        body += '\n' + '\n'.join(other_pages)
        complete = self.score(body, source, profile='chunk')
        self.assertEqual(complete['chunk_gate']['values']['numeric'], 1.0)
        omitted = self.score(body.replace('2026', ''), source, profile='chunk')
        self.assertEqual(omitted['chunk_gate']['values']['numeric'], 0.0)
        self.assertEqual(omitted['missing_numerals'], ['2026'])
        self.assertEqual(omitted['page_checks']['1']['missing_numerals'], ['2026'])
        for page in ('2', '3', '4'):
            self.assertEqual(omitted['page_checks'][page]['missing_numerals'], [])

    def test_figure_axes_drop_mixed_runs_but_require_body_values(self):
        axes = '0.020\n\n0.015\n\n0.010\n0.005\n0.000\n0\n1\n2\n3\n4'
        body = FIRST + '\nThe reported coefficient equals 0.005 after accounting for regional household characteristics.'
        source = body + '\n' + axes + '\n' + LAST
        report = self.score(body + '\n' + LAST, source, profile='chunk')
        expected = [[.020, .015, .010, .005, .000, 0, 1, 2, 3, 4]]
        self.assertEqual(report['figure_axis_runs'], expected)
        self.assertEqual(report['page_checks']['1']['figure_axis_runs'], expected)
        self.assertEqual(report['chunk_gate']['values']['numeric'], 1.0)
        self.assertIn('figure_axis_runs ' + json.dumps(report['figure_axis_runs']), report['dimensions']['numeric_fidelity']['details'])
        omitted = self.score((body + '\n' + LAST).replace('0.005', ''), source, profile='chunk')
        self.assertEqual(omitted['missing_numerals'], ['0.005'])
        self.assertEqual(omitted['page_checks']['1']['missing_numerals'], ['0.005'])

    def test_figure_round_grid_and_ap_subruns(self):
        source = '0.25\n1.00\n0.75\n0.50\nstop\n0.06\n0.04\n0.02\n0.00\n−0.02\n−4\n−3\n−2\n−1\n0'
        filtered, runs = quality.filter_figure_axis_lines(source)
        self.assertEqual(filtered.strip(), 'stop')
        self.assertEqual(runs, [[.25, 1, .75, .50], [.06, .04, .02, 0, -.02, -4, -3, -2, -1, 0]])
        filtered, runs = quality.filter_figure_axis_lines('+0.050%\n0.025%\n−0.000%')
        self.assertFalse(filtered.strip())
        self.assertEqual(runs, [[.05, .025, -0.0]])
        within = '0.100000000001\n0.200000000002\n0.300000000004'
        outside = '0.100000000001\n0.200000000002\n0.300000040004'
        self.assertFalse(quality.filter_figure_axis_lines(within)[0].strip())
        self.assertEqual(quality.filter_figure_axis_lines(outside), (outside, []))

    def test_axis_caption_neighbors_and_page_boundaries(self):
        source = '0.019\n\n\nFigure 1: Plot\n\n\n0.047\nstop\n0.083\nPanel A\n0.123\nNotes: Sample\n0.321'
        filtered, runs = quality.filter_figure_axis_lines(source)
        self.assertEqual(runs, [[.019], [.047], [.083], [.123], [.321]])
        self.assertNotIn('0.019', filtered)
        for source in ('Figure 1: Plot\n\n\n\n0.019', 'Figure 1: Plot\f0.019', '0.01\n0.02\f0.03'):
            self.assertEqual(quality.filter_figure_axis_lines(source), (source, []))

    def test_parenthesized_equation_tags_are_not_axis_ticks(self):
        for tags in ('(12)\n(13)\n(14)', '(S.3)\n(S.4)\n(S.5)', '(112)\n(113)\n(114)'):
            with self.subTest(tags=tags):
                self.assertEqual(quality.filter_figure_axis_lines(tags), (tags, []))
                source = 'Figure 1: Plot\n' + tags + '\n0.00\n0.05\n0.10'
                filtered, runs = quality.filter_figure_axis_lines(source)
                self.assertIn(tags, filtered)
                self.assertEqual(runs, [[0.00, 0.05, 0.10]])
        # Existing numeric-token eligibility stays unchanged; eligible tag
        # numerals must survive both the aggregate and per-page axis filters.
        source = FIRST + '\n(112)\n(113)\n(114)\n' + LAST
        report = self.score(FIRST + '\n' + LAST, source, profile='chunk')
        self.assertEqual(report['figure_axis_runs'], [])
        self.assertEqual(report['missing_numerals'], ['112', '113', '114'])
        self.assertEqual(report['page_checks']['1']['missing_numerals'], ['112', '113', '114'])

    def test_table_numerals_are_retained(self):
        table = 'Table 1: Estimates\n0.019\n0.047\n0.083\n(0.012)\n0.031\n(0.015)\n0.063\n(0.023)\n0.017\n0.011\n−0.971\n−0.977'
        self.assertEqual(quality.filter_figure_axis_lines(table), (table, []))
        report = self.score(FIRST + LAST, FIRST + '\n' + table + '\n' + LAST, profile='chunk')
        self.assertEqual(report['figure_axis_runs'], [])
        for value in ('0.019', '0.047', '0.083', '0.012', '0.031', '0.015', '0.063', '0.023'):
            self.assertIn(value, report['missing_numerals'])
        for column in ('119\n257\n493', '0.015\n0.045\n0.080'):
            self.assertEqual(quality.filter_figure_axis_lines(column), (column, []))

    def test_math_sentences_skip_symbols_accents_arguments_and_variables(self):
        quality.ACTIVE_PROFILE = 'chunk'
        prose = 'These independent households reveal substantial differences across several regional markets'
        for math in (' ∈ ∉ ≤ ≥ ≠ ⊤ ⊥ ∑ ∏ ∫ ∞ → ← ↔ ⇒ · × ± ∂ ∇ √',
                     ' α β γ δ ε θ π', ' x\u0304 x\u0304 x\u0304 x\u0304 x\u0304',
                     ' (0) (1) (d)', ' a b c d e f g h'):
            with self.subTest(math=math):
                result = quality.missing_sentence_check(prose + math, set())
                self.assertEqual(result['math_skipped'], 1)
                self.assertEqual(result['sentences'], 0)
                self.assertEqual(result['missing_sentences']['count'], 0)
                wrapped = quality.missing_sentence_check(prose + '\n' + math, set())
                self.assertEqual(wrapped, result)
        report = self.score(FIRST, FIRST + '. ' + prose + ' ∈ ∉ ≤ ≥ ≠ ⊤ ⊥', profile='chunk')
        self.assertEqual(report['math_skipped'], 1)
        self.assertEqual(report['math_skipped'], sum(page['math_skipped'] for page in report['page_checks'].values()))

    def test_sentence_eligibility_boundaries_and_trigram_hit_floor(self):
        quality.ACTIVE_PROFILE = 'chunk'
        words = 'aaa bbb ccc ddd eee fff ggg hhh'
        for text, eligible in ((words, True), ('aaa bbb ccc ddd eee fff ggg aa bb cc', False),
                               (words + ' iii a b c', False), (words + ' iii a b', True)):
            result = quality.missing_sentence_check(text, set())
            self.assertEqual(result['sentences'], int(eligible), text)
        prefix = words + ' ∈∈∈ '
        for length, skipped in ((100, 1), (101, 0)):
            result = quality.missing_sentence_check(prefix + 'z' * (length - len(prefix)), set())
            self.assertEqual(result['math_skipped'], skipped)
        text = words + ' iii jjj'
        trigrams = sorted(quality.word_trigrams(text))
        self.assertEqual(len(trigrams), 8)
        self.assertEqual(quality.missing_sentence_check(text, set(trigrams[:2]))['missing_sentences']['count'], 0)
        self.assertEqual(quality.missing_sentence_check(text, set(trigrams[:1]))['missing_sentences']['count'], 1)

    def test_p7_paragraph_deletion_local_or_synthetic(self):
        artifact_root = Path(__file__).resolve().parents[3] / 'p7'
        candidate = artifact_root / 'corrupt_b.md'
        files = sorted(artifact_root.glob('page_*.txt'))
        quality.ACTIVE_PROFILE = 'chunk'
        if candidate.is_file() and (artifact_root / 'page_7.txt').is_file():
            pages = {int(file.stem.split('_')[-1]): file.read_text() for file in files}
            checks = quality.score_pages(pages, candidate.read_text())
        else:
            checks = quality.score_pages({7: SOURCE}, SOURCE.replace(MISSING, ''))
        self.assertGreaterEqual(checks['7']['missing_sentences']['count'], 1)

    def test_wrapped_dois_are_printed_and_fabricated_doi_still_fails(self):
        source = SOURCE + '\nReferences\n10.3982/\nECTA12675\n10.1093/qje.\nqjs019\n'
        candidate = SOURCE + '\nReferences\n10.3982/ECTA12675\n10.1093/qje.qjs019\n'
        report = self.score(candidate, source, profile='chunk')
        self.assertEqual(report['injected_dois'], 0)
        self.assertEqual(report['injected_doi_list'], [])
        absent = self.score(candidate + '\n10.1234/not-in-pdf\n', source, profile='chunk')
        self.assertEqual(absent['injected_dois'], 1)
        self.assertEqual(absent['injected_doi_list'], ['10.1234/not-in-pdf'])
        self.assertIn('injected_dois', absent['chunk_gate']['failed'])

    def test_whole_document_doi_evidence_and_cli(self):
        doi = '10.1016/j.jeconom.2021.07.008'
        candidate = SOURCE + '\nReference: ' + doi + '\n'
        document = self.root / 'document.txt'
        document.write_text(SOURCE + '\fReference: 10.1016/j.\njeconom.2021.07.008\n')
        without_document = self.score(candidate, profile='chunk')
        self.assertEqual(without_document['injected_dois'], 1)
        report = self.score(candidate, profile='chunk', doc_text_path=str(document))
        self.assertEqual(report['injected_dois'], 0)
        self.assertEqual(report['chunk_gate']['values']['numeric'], 1.0)
        self.assertEqual(report['sentences'], 4)
        cmd = [sys.executable, quality.__file__, '--md', str(self.root / 'candidate.md'),
               '--source-text', str(self.root / 'source.txt'), '--profile', 'chunk',
               '--doc-text', str(document)]
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(json.loads(process.stderr)['injected_dois'], 0)
        fabricated = self.score(candidate + '\n10.1234/absent-everywhere\n', profile='chunk',
                                doc_text_path=str(document))
        self.assertEqual(fabricated['injected_dois'], 1)
        self.assertEqual(fabricated['injected_doi_list'], ['10.1234/absent-everywhere'])

    def test_sentence_furniture_does_not_hide_real_prose_omissions(self):
        header = 'A journal reporting empirical research on households and regional economic markets.'
        table = 'Treatment Control Total First Second Third Fourth Fifth sample mean observations.'
        footer = 'Downloaded from https://example.test/article by the campus library for personal academic use 2026.'
        source = '\f'.join((header + '\n' + FIRST + '\n' + table + '\n' + footer,
                             header + '\n' + MISSING,
                             header + '\n' + LAST))
        candidate = FIRST + '\n' + MISSING + '\n' + LAST
        report = self.score(candidate, source, profile='chunk')
        self.assertEqual(report['missing_sentences'], 0)
        self.assertEqual(report['sentences'], 4)
        self.assertEqual([page['sentences'] for page in report['page_checks'].values()], [1, 2, 1])
        omitted = self.score(candidate.replace(MISSING, ''), source, profile='chunk')
        self.assertEqual(omitted['missing_sentences'], 2)
        self.assertIn('missing_sentences_page_max', omitted['chunk_gate']['failed'])
        self.assertTrue(omitted['page_checks']['2']['missing_sentences']['heads'][0].startswith('independent households'))

    def test_page_files_equal_formfeeds_and_page_floor(self):
        source = SOURCE + ' '.join('unique%s alpha beta gamma delta epsilon zeta' % i for i in range(50)) + '.\f'
        report = self.score(SOURCE, source, profile='chunk', first_page=11)
        self.assertIn('page_trigram_min', report['chunk_gate']['failed'])
        folder = self.root / 'pages'
        folder.mkdir()
        for page, text in quality.split_pages_formfeed(source, 11).items():
            (folder / ('page_%s.txt' % page)).write_text(text)
        from_files = self.score(SOURCE, source, profile='chunk', first_page=11, page_texts_dir=str(folder))
        self.assertEqual(report['page_checks'], from_files['page_checks'])
        self.assertEqual(report['chunk_gate'], from_files['chunk_gate'])

    def test_weak_gate_skips_source_floors(self):
        report = self.score(FIRST * 10, '', profile='chunk')
        self.assertTrue(report['det_weak'])
        self.assertIsNone(report['chunk_gate']['values']['numeric'])
        self.assertEqual(report['page_checks'], {})
        self.assertEqual(report['dimensions']['math_quality']['weight'], .30)
        corrupted = ' '.join('123+456' for _ in range(6))
        self.assertTrue(self.score(FIRST * 10, corrupted, profile='chunk')['det_weak'])

    def test_floor_cli_overrides_env_and_rejects_invalid(self):
        md = self.root / 'candidate.md'
        src = self.root / 'source.txt'
        md.write_text(SOURCE)
        src.write_text(SOURCE)
        env = dict(os.environ, GEMINI_PDF_CHUNK_FLOORS='trigram=1.01')
        cmd = [sys.executable, quality.__file__, '--md', str(md), '--source-text', str(src), '--profile', 'chunk', '--floor', 'trigram=0.55']
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stderr)['chunk_gate']['floors']['trigram'], .55)
        for value in ('trigram=nan', 'trigram=-1', 'typo=1'):
            with self.assertRaises(ValueError):
                quality.parse_chunk_floors([value])

    def test_acceptance_checker_tags_and_injected_doi(self):
        source = SOURCE + 'A tagged relation ends with punctuation, T. (11)\n    (0)\n'
        src = self.root / 'source.txt'
        md = self.root / 'candidate.md'
        audit = self.root / 'candidate.audit.json'
        src.write_text(source)
        md.write_text(source + '\n$$ x = y ' + chr(92) + 'tag{11} $$\n')
        audit.write_text(json.dumps({'status': 'verified', 'input': {'text_digest': 'expected'},
                                     'deadline': {'hit': False, 'elapsed_s': 1, 'seconds': 60},
                                     'totals': {'failed': 0}}))
        checker = Path(__file__).parent / 'acceptance' / 'check_source.py'
        cmd = [sys.executable, str(checker), '--md', str(md), '--src', str(src),
               '--plain-src', str(src), '--audit', str(audit), '--expect-text-digest', 'expected']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('equation_tags | 1 | 1 |', result.stdout)
        self.assertNotIn('section_sequence', result.stdout)
        md.write_text(md.read_text() + '\nFabricated reference DOI: 10.1234/fake.\n')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn('injected_dois | 0 | 1 | ==0 | FAIL', result.stdout)



if __name__ == '__main__':
    unittest.main()
