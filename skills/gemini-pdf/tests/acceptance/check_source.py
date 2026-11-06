#!/usr/bin/env python3
"""Local, source-grounded acceptance metrics for synthetic transcription tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import quality_check as quality


TAG_SOURCE_RE = re.compile(r'(\s{3,}|[.,;:)]\s{1,2})\(((?:S\.)?\d{1,3})\)\s*$', re.MULTILINE)  # <=3 digits: equation numbers, never citation years like (2019)
TAG_MD_RE = re.compile(r'\\tag\*?\{([^{}]+)\}')
YEAR_RE = re.compile(r'\b(?:18|19|20)\d{2}[a-z]?\b')


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def without_tex_comments(text: str) -> str:
    return re.sub(r'(?<!\\)%[^\n]*', '', text)


def tex_arguments(text: str, command: str) -> list[str]:
    """Balanced braces retain title text inside formatting commands."""
    text = without_tex_comments(text)
    result = []
    pattern = re.compile(r'\\' + re.escape(command) + r'(?![A-Za-z])\*?\s*(?:\[[^\]]*\]\s*)?\{')
    for match in pattern.finditer(text):
        depth = 1
        end = match.end()
        while end < len(text) and depth:
            if text[end] == '{' and (end == 0 or text[end - 1] != '\\'):
                depth += 1
            elif text[end] == '}' and (end == 0 or text[end - 1] != '\\'):
                depth -= 1
            end += 1
        if depth == 0:
            result.append(text[match.end():end - 1])
    return result


def normalize_title(title: str) -> str:
    title = re.sub(r'\\[A-Za-z]+\*?', '', title)
    # drop enumerators such as 'A', 'S.1', '2.1' that survive as single letters; keep words of >= 2 letters;
    # strip citation/reference macros (\cite{...}, \ref{...}, \crossref{...}) — the Markdown renders them as names/numbers
    title = re.sub(r'\\(?:cite[tp]?|ref|crossref|autoref|eqref|label)\*?\{[^{}]*\}', ' ', title)
    title = re.sub(r'\\[A-Za-z]+\*?', ' ', title)
    return ' '.join(w for w in re.findall(r'[a-z]+', title.lower()) if len(w) >= 2)


def lcs_length(expected: list[str], observed: list[str], equal=None) -> int:
    # `equal` lets callers tolerate stripped macros: a title reduced to 'application to' matches 'application to a named reference'
    same = equal or (lambda a, b: a == b)
    row = [0] * (len(observed) + 1)
    for item in expected:
        previous = 0
        for column, other in enumerate(observed, 1):
            old = row[column]
            row[column] = previous + 1 if same(item, other) else max(row[column], row[column - 1])
            previous = old
    return row[-1]


def reference_count(markdown: str) -> int:
    match = re.search(r'^\s*(#{1,6})\s+(?:\d+[. ]+)?(?:References|Bibliography|Works Cited)\s*$', markdown, re.MULTILINE | re.IGNORECASE)
    if not match:
        return 0
    body = markdown[match.end():]
    next_heading = re.search(r'^#{1,' + str(len(match.group(1))) + r'}\s+', body, re.MULTILINE)
    if next_heading:
        body = body[:next_heading.start()]
    paragraphs = [block for block in re.split(r'\n\s*\n', body.strip()) if YEAR_RE.search(block)]
    # Most converters separate entries by blank lines. A compact one-entry-per-line
    # bibliography needs the year-bearing line count instead.
    lines = [line for line in body.splitlines() if YEAR_RE.search(line) and re.match(r'^\s*(?:[-*]\s+|\[\d+\]\s*)?(?:\*\*)?[A-Z]', line)]
    return max(len(paragraphs), len(lines))


def reading_order_text(args: argparse.Namespace, audit: dict) -> str:
    if args.plain_src:
        return read(args.plain_src)
    candidates = []
    input_pdf = audit.get('input', {}).get('pdf')
    if input_pdf:
        candidates += [Path(input_pdf), args.audit.parent / input_pdf]
    candidates.append(args.src.with_suffix('.pdf'))
    if args.tex:
        candidates.append(args.tex.with_suffix('.pdf'))
    for pdf in candidates:
        if not pdf.is_file():
            continue
        result = subprocess.run(['pdftotext', str(pdf), '-'], capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return result.stdout
    raise ValueError('reading-order source unavailable; supply --plain-src or a source PDF next to --src')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('md', 'src', 'audit'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expect-text-digest', required=True)
    parser.add_argument('--tex', type=Path)
    parser.add_argument('--bbl', type=Path)
    parser.add_argument('--plain-src', type=Path)
    args = parser.parse_args(argv)
    failures = 0

    def metric(name: str, oracle: object, observed: object, threshold: str, passed: bool) -> None:
        nonlocal failures
        failures += not passed
        print(f'{name} | {oracle} | {observed} | {threshold} | {"PASS" if passed else "FAIL"}')

    try:
        markdown = read(args.md)
        layout = read(args.src)
        audit = json.loads(read(args.audit))
        plain = reading_order_text(args, audit)
        tex = read(args.tex) if args.tex else None
        bbl = read(args.bbl) if args.bbl else None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        metric('inputs', 'readable local sources', str(exc), 'all required inputs readable', False)
        return 1

    numeric_tags = {match.group(2) for match in TAG_SOURCE_RE.finditer(layout) if match.group(2) != '0'}
    text_tags = set()
    if tex is not None:
        text_tags = {tag.strip() for tag in tex_arguments(tex, 'tag') if not re.fullmatch(r'(?:S\.)?\d+', tag.strip())}
    oracle_tags = numeric_tags | text_tags
    observed_tags = {tag.strip() for tag in TAG_MD_RE.findall(markdown)}
    metric('equation_tags', len(oracle_tags), len(observed_tags), 'distinct count within ±2', abs(len(oracle_tags) - len(observed_tags)) <= 2)
    tag_recall = len(numeric_tags & observed_tags) / len(numeric_tags) if numeric_tags else 1.0
    metric('numeric_tag_recall', len(numeric_tags), f'{tag_recall:.6f}', '>=0.95', tag_recall >= .95)

    quality.ACTIVE_PROFILE = 'chunk'
    md_body = quality.strip_stray_sentinels(quality.strip_front_matter(markdown)[0])[0]
    pages = quality.split_pages_formfeed(plain)
    # score_numeric_fidelity defines the shared capped-multiset/boilerplate rule;
    # its raw companion avoids turning rounded displayed recall into an oracle.
    quality.score_numeric_fidelity(plain, md_body, len(pages))
    numeric = quality.numeric_fidelity_values(plain, md_body, len(pages))
    metric('numeral_multiset_recall', numeric['total'], f"{numeric['recall']:.6f}", '>=0.90', numeric['recall'] >= .97)
    source_tri = quality.word_trigrams(quality.normalize_for_trigrams(plain, drop_repeated_lines=True))
    candidate_tri = quality.word_trigrams(quality.normalize_for_trigrams(md_body, drop_repeated_lines=False))
    trigram_recall = len(source_tri & candidate_tri) / len(source_tri) if source_tri else 1.0
    metric('trigram_recall', len(source_tri), f'{trigram_recall:.6f}', '>=0.60', bool(source_tri) and trigram_recall >= .85)

    if tex is not None:
        # sections may sit at '##' or, in a supplement, '###'; subsections one level deeper — match on any of these levels in order
        for command, prefix, floor in (('section', ('## ', '### '), 14), ('subsection', ('### ', '#### '), 10)):
            expected = [normalize_title(title) for title in tex_arguments(tex, command)]
            observed = [normalize_title(line[len(prefix):]) for line in markdown.splitlines() if line.startswith(prefix)]
            count = lcs_length(expected, observed, lambda a, b: a == b or (len(a) >= 6 and a in b) or (len(b) >= 6 and b in a))
            metric(command + '_sequence', len(expected), count, f'LCS>={floor}', count >= floor)
        expected_footnotes = len(re.findall(r'\\footnote\s*\{', without_tex_comments(tex)))
        footnotes = len(re.findall(r'^\s*\[\^[^\]\n]+\]:', markdown, re.MULTILINE))
        metric('footnotes', expected_footnotes, footnotes, '>=8', footnotes >= 8)
    if bbl is not None:
        expected_references = len(re.findall(r'\\harvarditem\b', without_tex_comments(bbl)))
        references = reference_count(markdown)
        metric('references', expected_references, references, 'count within ±2', abs(references - expected_references) <= 2)

    checks = quality.score_pages(pages, md_body)
    missing_total = sum(value['missing_sentences']['count'] for value in checks.values())
    for page, values in checks.items():
        missing = values['missing_sentences']['count']
        # The acceptance ceiling is document-wide; page rows expose where the
        # total comes from without inventing an additional per-page threshold.
        metric('missing_sentences_page_' + page, values['sentences'], missing, 'document total<=3', missing_total <= 3)
    metric('missing_sentences', sum(v['sentences'] for v in checks.values()), missing_total, '<=3', missing_total <= 3)
    dois = quality.injected_doi_values(plain, markdown)
    metric('injected_dois', 0, len(dois), '==0', not dois)
    markers = len(re.findall(r'<!--\s*AUDIT\b', markdown))
    metric('audit_markers', 0, markers, '==0 and status verified', markers == 0 and audit.get('status') == 'verified')
    metric('status', 'verified', audit.get('status'), '==verified', audit.get('status') == 'verified')
    digest = audit.get('input', {}).get('text_digest')
    # the pipeline stores an 8-hex prefix; accept a prefix match of >= 8 hex characters either way
    _d = (digest or '').lower(); _e = (args.expect_text_digest or '').lower(); _n = min(len(_d), len(_e))
    metric('text_digest', args.expect_text_digest, digest, 'prefix match (>=8 hex)', _n >= 8 and _d[:_n] == _e[:_n])
    deadline = audit.get('deadline', {})
    metric('deadline_hit', False, deadline.get('hit'), '==false', deadline.get('hit') is False)
    failed = audit.get('totals', {}).get('failed')
    metric('failed_chunks', 0, failed, '==0', failed == 0)
    elapsed, seconds = deadline.get('elapsed_s'), deadline.get('seconds')
    if isinstance(elapsed, (int, float)) and isinstance(seconds, (int, float)):
        metric('elapsed_seconds', seconds, elapsed, '<=deadline', elapsed <= seconds)
    else:
        metric('elapsed_seconds', 'reported deadline', elapsed, 'numeric elapsed and deadline', False)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
