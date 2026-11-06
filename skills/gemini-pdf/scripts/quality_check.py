#!/usr/bin/env python3
r"""Score markdown output quality from PDF-to-markdown conversion on a 0-100 scale.

Evaluates content completeness, math quality, structural integrity, OCR artifact
detection, and failed chunk count. When source text is available, also evaluates
numeric fidelity and trigram recall against the source. Adaptive weighting
redistributes math weight when the source is non-mathematical.

The command preserves a stable shell-facing interface:
stdout is exactly one summary line, stderr is the full JSON report, and
exit 0 = pass while exit 1 = fail.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Regexes and constants
# ---------------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(
    r"\A(?:\ufeff)?---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)
FRONT_MATTER_TITLE_RE = re.compile(r"(?im)^\s*title\s*:\s*(?P<title>.*?)\s*$")
STRAY_SENTINEL_RE = re.compile(
    r"(?m)^[ \t]*<!--\s*END OF CHUNK [^>]*-->[ \t]*(?:\r?\n|$)"
)

DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?!\$)(?!\s?\d).*?(?<!\\)\$", re.DOTALL)
# Digit-started cells such as $0.019$ made the legacy DOTALL matcher treat
# their closing dollar as an opener and swallow later prose. Keep that legacy
# behavior for document scores, whose historical values are an API contract.
INLINE_MATH_RE_CHUNK = re.compile(r"(?<!\\)\$(?!\$)(?:[^$\n\\]|\\.)+?(?<!\\)\$")
ACTIVE_PROFILE = "document"
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\]\)>,]+", re.IGNORECASE)
# PDF text layers commonly break DOI suffixes after slashes and periods.
SOURCE_DOI_RE = re.compile(r"10\.\s*\d{4,9}/\s*[^\s\]\)>,/.]+(?:[/.]\s*[^\s\]\)>,/.]+)*", re.IGNORECASE)
AUDIT_MARKER_RE = re.compile(r"<!--\s*AUDIT\b[^>]*-->", re.IGNORECASE)
SENTENCE_WORD_RE = re.compile(r"[a-z][a-z']+")
LOWERCASE_WORD_RE = re.compile(r"\b[a-z][a-z']+\b")
NUMERIC_ONLY_LINE_RE = re.compile(r"^\s*[-−+]?\d+(?:\.\d+)?%?\s*$")
EQUATION_TAG_LINE_RE = re.compile(r"^\s*\((?:[A-Za-z]\.)?\d+(?:\.\d+)*\)\s*$")
FIGURE_LABEL_RE = re.compile(r"^\s*(?:Figure\b|Panel\b|Notes:)", re.IGNORECASE)
PROSE_WORD_RE = re.compile(r"\b[^\W\d_]{3,}\b")
SENTENCE_TOKEN_RE = re.compile(r"\b[^\W_]+\b")
MATH_ARGUMENT_RE = re.compile(r"\([0-9a-z]\)")
SENTENCE_MATH_SYMBOLS = set("∈∉≤≥≠⊤⊥∑∏∫∞→←↔⇒·×±∂∇√")
LATEX_ENV_RE = re.compile(
    r"\\begin\{(?P<env>[A-Za-z][A-Za-z0-9*_-]*)\}.*?\\end\{(?P=env)\}",
    re.DOTALL,
)
LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+[*]?")

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*?`")
ESCAPED_DOLLAR_RE = re.compile(r"\\\$")
CURRENCY_TOKEN_RE = re.compile(r"\$\s?\d[\d,.]*\b")
DIGIT_STARTED_INLINE_MATH_RE = re.compile(
    r"(?<!\\)\$\s?\d[\d,.]*(?:\\[^\s]|[^\s$])*\$"
)

EQ_NUMBER_RE = re.compile(r"^\s*\(\d+(?:\.\d+)?\)\s*$", re.MULTILINE)
MATH_UNICODE_RE = re.compile(r"[\u0370-\u03FF\u2200-\u22FF\u2A00-\u2AFF\u222B\u2211]")
TITLE_HEADING_RE = re.compile(r"^# [^\n]+", re.MULTILINE)
SECTION_HEADING_RE = re.compile(r"^## [^\n]+", re.MULTILINE)
BIBLIOGRAPHY_RE = re.compile(
    r"^#{1,3}\s*(References|Bibliography|Works Cited)",
    re.MULTILINE | re.IGNORECASE,
)
TABLE_SEPARATOR_RE = re.compile(r"\|[-:]+\|")
THEOREM_MARKER_RE = re.compile(
    r"(?:^|\n)\s*\*{0,2}"
    r"(Theorem|Lemma|Proposition|Corollary|Proof|Definition|Remark|Assumption)",
    re.IGNORECASE,
)

BROKEN_FRAC_RE = re.compile(r"\\frac\s*\{[^}]*$", re.MULTILINE)
NESTED_BROKEN_COMMAND_RE = re.compile(
    r"\\[A-Za-z]+\{[^}]*\\[A-Za-z]+\{[^}]*$",
    re.MULTILINE,
)
GARBLED_RUN_RE = re.compile(r"\b[bcdfghjklmnpqrstvwxyz]{5,}\b", re.IGNORECASE)
LIGATURE_RE = re.compile(r"[\uFB00-\uFB04]")
URL_RE = re.compile(r"https?://\S+")
# The pipeline writes markers like "<!-- CHUNK 3 FAILED (pages 21-30) -->";
# tolerate any suffix after FAILED (the old \s*--> form silently matched nothing).
FAILED_CHUNK_RE = re.compile(r"<!--\s*CHUNK\s+(\d+)\s+FAILED\b[^>]*-->", re.IGNORECASE)

HYPHENATED_LINEBREAK_RE = re.compile(r"(?<=\w)-\s*\r?\n\s*(?=\w)")
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"\b[a-z0-9]+(?:'[a-z0-9]+)?\b")
NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w.])[-+]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?%?(?![\w.])"
    r"|(?<![\w.])[-+]?\.\d+%?(?![\w.])"
)

UNCERTAIN_MARKER = "<!-- UNCERTAIN:"
LIGATURE_TRANSLATION = str.maketrans(
    {
        "\uFB00": "ff",
        "\uFB01": "fi",
        "\uFB02": "fl",
        "\uFB03": "ffi",
        "\uFB04": "ffl",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FailedChunkStats:
    """Failed chunk score plus the count and fraction needed for hard caps."""

    score: int
    details: str
    count: int
    total: Optional[int]
    fraction: float
    source: str


def get_page_count_pdfinfo(pdf_path: str) -> Optional[int]:
    """Use pdfinfo to extract page count. Returns None on failure."""
    try:
        result = subprocess.run(
            ["pdfinfo", pdf_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    return int(parts[1].strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
    return None


def estimate_pages_from_filesize(pdf_path: str) -> Optional[int]:
    """Rough page estimate: ~50 KB per page."""
    try:
        size = os.path.getsize(pdf_path)
        return max(1, round(size / 50_000))
    except OSError:
        return None


def get_page_count(pdf_path: Optional[str]) -> Optional[int]:
    """Best-effort page count from PDF."""
    if pdf_path is None or not os.path.isfile(pdf_path):
        return None
    count = get_page_count_pdfinfo(pdf_path)
    if count is not None:
        return count
    return estimate_pages_from_filesize(pdf_path)


def read_text_file(path: str) -> str:
    """Read a UTF-8-ish text file, replacing undecodable bytes."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def strip_front_matter(md_text: str) -> tuple[str, dict[str, Optional[str] | bool]]:
    """Strip YAML front matter and return lightweight metadata about it."""
    match = FRONT_MATTER_RE.match(md_text)
    if match is None:
        return md_text, {"present": False, "title": None}

    front_matter = match.group("body")
    title: Optional[str] = None
    title_match = FRONT_MATTER_TITLE_RE.search(front_matter)
    if title_match is not None:
        title = title_match.group("title").strip()
        if len(title) >= 2 and title[0] == title[-1] and title[0] in {"'", '"'}:
            title = title[1:-1].strip()
        if not title:
            title = None

    return md_text[match.end() :], {"present": True, "title": title}


def strip_stray_sentinels(md_text: str) -> tuple[str, int]:
    """Remove leaked end-of-chunk sentinels and return how many were found."""
    cleaned, count = STRAY_SENTINEL_RE.subn("", md_text)
    return cleaned, count


def fold_ligatures(text: str) -> str:
    """Replace common Unicode ligature code points with ASCII sequences."""
    return text.translate(LIGATURE_TRANSLATION)


def delatex_text(text: str) -> str:
    """Remove common LaTeX math blocks, inline math spans, environments, and commands."""
    text = DISPLAY_MATH_RE.sub(" ", text)
    text = LATEX_ENV_RE.sub(" ", text)
    inline_math = INLINE_MATH_RE_CHUNK if ACTIVE_PROFILE == "chunk" else INLINE_MATH_RE
    text = inline_math.sub(" ", text)
    text = LATEX_COMMAND_RE.sub(" ", text)
    return text


def measured_length(text: str) -> int:
    """Count characters after collapsing whitespace to reduce formatting noise."""
    return len(WHITESPACE_RE.sub(" ", text).strip())


def source_text_is_corrupted(source_text: str) -> bool:
    """Detect systematically corrupted PDF text layers.

    Some journals' PDFs (observed live: Cambridge/ET papers) carry text layers
    where '.' is encoded as '+' and '(' as '~'. Recall-style fidelity checks
    against such a layer punish a CORRECT conversion, so when the layer looks
    corrupted the source-dependent dimensions are skipped entirely.

    Symptoms tested: decimals joined by '+' outnumbering real decimals, and
    prose nearly devoid of sentence periods.
    """
    plus_decimals = len(re.findall(r"\d\+\d", source_text))
    dot_decimals = len(re.findall(r"\d\.\d", source_text))
    if plus_decimals >= 5 and plus_decimals > 2 * max(1, dot_decimals):
        return True
    n = len(source_text)
    if n >= 5000:
        periods_per_1000 = source_text.count(".") / (n / 1000)
        if periods_per_1000 < 1.0:
            return True
        # Symbol-font corruption variant (observed: Econometrica 1990s):
        # '(' encodes as 'Ž' etc. — Latin-Extended-A density far beyond what
        # European names in a bibliography could produce.
        latin_ext = sum(1 for ch in source_text if "Ā" <= ch <= "ſ")
        if latin_ext / (n / 1000) > 5.0:
            return True
    return False


def is_mathematical(md_text: str, source_text: Optional[str]) -> bool:
    """Heuristic: does the content look mathematical?"""
    dollar_count = md_text.count("$")
    if dollar_count >= 4:
        return True

    eq_nums = EQ_NUMBER_RE.findall(md_text)
    if len(eq_nums) >= 2:
        return True

    if source_text:
        math_unicode = MATH_UNICODE_RE.findall(source_text)
        if len(math_unicode) >= 3:
            return True

    return False


def map_linear_score(value: float, zero_at: float, full_at: float) -> int:
    """Map value linearly from zero_at -> 0 to full_at -> 100."""
    if value <= zero_at:
        return 0
    if value >= full_at:
        return 100
    return round(100 * (value - zero_at) / (full_at - zero_at))


# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------

def score_content_completeness(
    md_text: str,
    page_count: Optional[int],
    source_text: Optional[str],
) -> tuple[int, str]:
    """Score source-relative completeness, falling back to de-LaTeXed chars/page.

    The source-relative ratio compares RAW whitespace-collapsed lengths on both
    sides. De-LaTeXing only the output would be asymmetric: the source keeps its
    math as plain text, so a correct conversion of a math-dense paper (80%+ math
    is normal for theory) would crater to a ratio near 0.25 despite being
    complete. Raw-vs-raw, LaTeX verbosity roughly offsets symbol expansion
    (observed live: 0.98 and 1.14 on complete conversions).
    """
    output_chars = measured_length(delatex_text(md_text))

    if output_chars == 0:
        return 0, "empty file"

    if source_text is not None and source_text.strip():
        raw_output_chars = measured_length(md_text)
        source_chars = measured_length(source_text)
        if source_chars == 0:
            return 0, "empty source text"

        ratio = raw_output_chars / source_chars
        if 0.5 <= ratio <= 2.0:
            score = 100
        elif ratio < 0.5:
            score = map_linear_score(ratio, 0.2, 0.5)
        else:
            score = max(0, round(100 * (3.5 - ratio) / (3.5 - 2.0)))

        return score, f"raw ratio {ratio:.2f} ({raw_output_chars}/{source_chars} chars)"

    # No usable source text: raw chars/page. (Raw, not de-LaTeXed — math-dense
    # theory papers are mostly LaTeX in the output, and stripping it would
    # punish complete conversions; observed live at 80%+ math share.)
    raw_chars = measured_length(md_text)
    if page_count is None or page_count <= 0:
        est_pages = max(1, round(raw_chars / 5000))
        return 70, f"{raw_chars} chars, ~{est_pages} pages (estimated, no PDF)"

    cpp = raw_chars / page_count

    if 1500 <= cpp <= 10000:
        score = 100
    elif cpp < 1500:
        score = max(0, round(100 * cpp / 1500))
    else:
        score = max(0, round(100 * (1 - (cpp - 10000) / 10000)))

    return score, f"{cpp:.0f} chars/page ({page_count} pages)"


def strip_currency_like_dollars(text: str) -> str:
    r"""Strip escaped dollars and currency-like dollars before inline parity counting.

    Accepted limitation: inline math beginning with a bare digit, such as
    ``$5\%$``, is treated as currency-like and is not counted as inline math.
    """
    text = ESCAPED_DOLLAR_RE.sub("", text)
    text = DIGIT_STARTED_INLINE_MATH_RE.sub("", text)
    return CURRENCY_TOKEN_RE.sub("", text)


def score_math_quality(md_text: str) -> tuple[int, str]:
    """Score LaTeX math quality: balanced delimiters, well-formed commands."""
    if not md_text.strip():
        return 0, "empty file"

    penalties: list[str] = []
    total_penalty = 0

    text_no_display = DISPLAY_MATH_RE.sub("", md_text)
    text_no_code = CODE_BLOCK_RE.sub("", text_no_display)
    text_no_code = INLINE_CODE_RE.sub("", text_no_code)
    text_no_code = strip_currency_like_dollars(text_no_code)

    inline_dollars = text_no_code.count("$")
    if inline_dollars % 2 != 0:
        total_penalty += 15
        penalties.append(f"{inline_dollars} inline $ (odd)")
    inline_count = inline_dollars // 2

    display_dollars = md_text.count("$$")
    if display_dollars % 2 != 0:
        total_penalty += 15
        penalties.append(f"{display_dollars} $$ (odd)")
    display_count = display_dollars // 2

    broken_frac = len(BROKEN_FRAC_RE.findall(md_text))
    if broken_frac > 0:
        total_penalty += 5 * broken_frac
        penalties.append(f"{broken_frac} broken \\frac")

    corruption = len(NESTED_BROKEN_COMMAND_RE.findall(md_text))
    if corruption > 0:
        total_penalty += 3 * corruption
        penalties.append(f"{corruption} nested broken commands")

    score = max(0, 100 - total_penalty)

    details_parts = [f"{inline_count} inline, {display_count} display"]
    if penalties:
        details_parts.extend(penalties)

    return score, ", ".join(details_parts)


def score_structural_integrity(md_text: str) -> tuple[int, str]:
    """Score structural markers: title, sections, bibliography, tables, theorems."""
    if not md_text.strip():
        return 0, "empty file"

    found: list[str] = []
    total = 0
    max_total = 0

    max_total += 20
    has_title = bool(TITLE_HEADING_RE.search(md_text))
    if has_title:
        total += 20
        found.append("title")

    max_total += 25
    sections = SECTION_HEADING_RE.findall(md_text)
    section_count = len(sections)
    if section_count >= 2:
        total += 25
        found.append(f"{section_count} sections")
    elif section_count == 1:
        total += 12
        found.append(f"{section_count} section")

    max_total += 20
    has_bib = bool(BIBLIOGRAPHY_RE.search(md_text))
    if has_bib:
        total += 20
        found.append("bibliography")

    max_total += 15
    table_seps = TABLE_SEPARATOR_RE.findall(md_text)
    table_count = len(table_seps)
    if table_count > 0:
        total += 15
        found.append(f"{table_count} tables")

    max_total += 20
    theorem_patterns = THEOREM_MARKER_RE.findall(md_text)
    if theorem_patterns:
        total += 20
        found.append(f"{len(theorem_patterns)} theorem/proof markers")

    score = round(100 * total / max_total) if max_total > 0 else 0
    details = ", ".join(found) if found else "no structural elements found"
    return score, details


def score_ocr_artifacts(md_text: str) -> tuple[int, str]:
    """Score OCR artifact presence: replacement chars, garbled runs, ligatures."""
    if not md_text.strip():
        return 0, "empty file"

    issues: list[str] = []
    total_penalty = 0

    replacement_count = md_text.count("\uFFFD")
    if replacement_count > 0:
        total_penalty += min(50, replacement_count * 5)
        issues.append(f"{replacement_count} replacement chars")

    text_clean = DISPLAY_MATH_RE.sub("", md_text)
    text_clean = INLINE_MATH_RE.sub("", text_clean)
    text_clean = CODE_BLOCK_RE.sub("", text_clean)
    text_clean = INLINE_CODE_RE.sub("", text_clean)
    text_clean = LATEX_COMMAND_RE.sub("", text_clean)
    text_clean = URL_RE.sub("", text_clean)

    garbled = GARBLED_RUN_RE.findall(text_clean)
    garbled_count = len(garbled)
    if garbled_count > 0:
        total_penalty += min(30, garbled_count * 3)
        issues.append(f"{garbled_count} garbled runs")

    ligature_chars = LIGATURE_RE.findall(md_text)
    lig_count = len(ligature_chars)
    if lig_count > 0:
        total_penalty += min(20, lig_count * 2)
        issues.append(f"{lig_count} unresolved ligatures")

    score = max(0, 100 - total_penalty)
    details = ", ".join(issues) if issues else "clean"
    return score, details


def load_meta_chunks(meta_json_path: str) -> tuple[Optional[list[Any]], Optional[str]]:
    """Load chunk metadata, returning a chunks list or an error string."""
    try:
        with open(meta_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)

    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list):
        return None, "metadata JSON does not contain a chunks list"
    return chunks, None


def score_failed_chunks(md_text: str, meta_json_path: Optional[str]) -> FailedChunkStats:
    """Score failed chunks using metadata when available, otherwise marker count."""
    if meta_json_path:
        chunks, error = load_meta_chunks(meta_json_path)
        if chunks is not None:
            total = len(chunks)
            failed = sum(
                1
                for chunk in chunks
                if isinstance(chunk, dict) and str(chunk.get("status", "")).lower() == "failed"
            )
            fraction = failed / total if total > 0 else 0.0
            score = round(100 * (1 - fraction)) if total > 0 else 100
            details = f"{failed}/{total} failed chunks (metadata)"
            return FailedChunkStats(
                score=score,
                details=details,
                count=failed,
                total=total,
                fraction=fraction,
                source="metadata",
            )

        marker_stats = score_failed_chunks(md_text, None)
        details = f"{marker_stats.details}; metadata ignored: {error}"
        return FailedChunkStats(
            score=marker_stats.score,
            details=details,
            count=marker_stats.count,
            total=marker_stats.total,
            fraction=marker_stats.fraction,
            source="markers",
        )

    if not md_text.strip():
        return FailedChunkStats(
            score=0,
            details="empty file",
            count=0,
            total=None,
            fraction=0.0,
            source="markers",
        )

    failed_ids = [int(match) for match in FAILED_CHUNK_RE.findall(md_text)]
    count = len(failed_ids)

    if count == 0:
        return FailedChunkStats(
            score=100,
            details="0 failed chunks",
            count=0,
            total=None,
            fraction=0.0,
            source="markers",
        )

    score = max(0, 100 - count * 20)
    estimated_total = max(max(failed_ids), count)
    fraction = min(1.0, count / estimated_total) if estimated_total > 0 else 1.0
    return FailedChunkStats(
        score=score,
        details=f"{count} failed chunks (markers, estimated {estimated_total} total)",
        count=count,
        total=estimated_total,
        fraction=fraction,
        source="markers",
    )


def normalize_numeric_token(token: str) -> Optional[str]:
    """Normalize numeric tokens for matching and drop tokens with fewer than 3 digits."""
    token = token.replace(",", "").strip()
    if token.endswith("%"):
        token = token[:-1]
    if token.startswith("+"):
        token = token[1:]
    if token.startswith("."):
        token = "0" + token
    elif token.startswith("-."):
        token = "-0" + token[1:]

    digit_count = sum(ch.isdigit() for ch in token)
    if digit_count < 3:
        return None
    return token


def extract_numeric_tokens_by_line(text: str) -> tuple[list[str], dict[str, set[int]]]:
    """Extract normalized numeric tokens and map tokens to distinct source lines."""
    tokens: list[str] = []
    line_numbers: dict[str, set[int]] = {}

    for line_no, line in enumerate(text.splitlines(), start=1):
        line_tokens: set[str] = set()
        for match in NUMERIC_TOKEN_RE.findall(line):
            token = normalize_numeric_token(match)
            if token is None:
                continue
            tokens.append(token)
            line_tokens.add(token)
        for token in line_tokens:
            line_numbers.setdefault(token, set()).add(line_no)

    return tokens, line_numbers


def numeric_source_counter(source_text: str, page_count: Optional[int], *,
                           page_number_run: Optional[dict[str, int]] = None,
                           repeated_lines: Optional[set[str]] = None) -> tuple[Counter[str], set[str]]:
    """Share the legacy boilerplate filter so raw gates and document scores agree."""
    source_tokens, source_line_numbers = extract_numeric_tokens_by_line(source_text)

    dropped_tokens: set[str] = set()
    if ACTIVE_PROFILE == "chunk":
        if page_number_run is None:
            page_number_run = detect_page_number_run(split_pages_formfeed(source_text))
        # Detect axes before repetition filtering can break their source runs.
        filtered, _ = filter_figure_axis_lines(source_text)
        filtered = filter_chunk_source_lines(filtered, repeated_lines)
        filtered_tokens, source_line_numbers = extract_numeric_tokens_by_line(filtered)
        # A body occurrence remains evidence even if the same number is in a footer.
        dropped_tokens = set(source_tokens) - set(filtered_tokens)
        source_tokens = filtered_tokens
        if page_number_run:
            dropped_tokens.update(str(n) for n in range(page_number_run["start"] - 1,
                                                        page_number_run["end"] + 2))
    if page_count is not None and page_count > 0:
        line_frequency_threshold = max(3.0, page_count / 2)
        dropped_tokens |= {
            token
            for token, lines in source_line_numbers.items()
            if len(lines) >= line_frequency_threshold
        }
        # Printed page numbers are distinct per page (one each), so the
        # line-frequency filter cannot catch them — but they form a consecutive
        # integer run of length ~page_count. Find the best-covered run of pure
        # integers and drop it (the output strips page numbers by instruction).
        pure_ints = {
            int(t) for t in source_line_numbers
            if t.isdigit() and 2 <= len(t) <= 5
        }
        if ACTIVE_PROFILE != "chunk" and pure_ints and page_count >= 4:
            best_start, best_cover = None, 0
            for start in pure_ints:
                cover = sum(1 for k in range(page_count) if (start + k) in pure_ints)
                if cover > best_cover:
                    best_start, best_cover = start, cover
            if best_start is not None and best_cover >= 0.6 * page_count:
                dropped_tokens |= {
                    str(best_start + k)
                    for k in range(page_count)
                    if (best_start + k) in pure_ints
                }

    filtered_source = [token for token in source_tokens if token not in dropped_tokens]
    source_counter = Counter(filtered_source)
    for token in list(source_counter):
        source_counter[token] = min(source_counter[token], 5)

    return source_counter, dropped_tokens


def score_numeric_fidelity(
    source_text: str,
    md_text: str,
    page_count: Optional[int],
    *,
    page_number_run: Optional[dict[str, int]] = None,
    repeated_lines: Optional[set[str]] = None,
) -> tuple[int, str]:
    """Score multiset recall of source numeric tokens in markdown output."""
    source_counter, dropped_tokens = numeric_source_counter(
        source_text, page_count, page_number_run=page_number_run, repeated_lines=repeated_lines)
    run_details = (f"; page_number_run {page_number_run['start']}-{page_number_run['end']} dropped"
                   if page_number_run else "")
    if ACTIVE_PROFILE == "chunk":
        _, axis_runs = filter_figure_axis_lines(source_text)
        run_details += f"; figure_axis_runs {json.dumps(axis_runs)} dropped"
    output_tokens, _ = extract_numeric_tokens_by_line(md_text)

    total = sum(source_counter.values())
    if total == 0:
        return 100, "no source numeric tokens after filtering" + run_details

    output_counter = Counter(output_tokens)
    matched = sum(min(count, output_counter[token]) for token, count in source_counter.items())
    recall = matched / total

    if recall >= 0.90:
        score = 100
    elif recall <= 0.50:
        score = 0
    else:
        score = round(100 * (recall - 0.50) / (0.90 - 0.50))

    return (
        score,
        f"recall {recall:.2f} ({matched}/{total} capped tokens, "
        f"{len(dropped_tokens)} boilerplate tokens dropped)" + run_details,
    )


def normalize_line_for_repetition(line: str) -> str:
    """Normalize a single source line for repeated header/footer detection."""
    line = fold_ligatures(line)
    line = delatex_text(line)
    line = line.lower()
    return WHITESPACE_RE.sub(" ", line).strip()


def drop_repeated_source_lines(source_text: str) -> str:
    """Drop source lines whose normalized form repeats at least 3 times."""
    lines = source_text.splitlines()
    normalized_lines = [normalize_line_for_repetition(line) for line in lines]
    counts = Counter(line for line in normalized_lines if line)

    kept_lines = [
        line
        for line, normalized in zip(lines, normalized_lines)
        if not normalized or counts[normalized] < 3
    ]
    return "\n".join(kept_lines)


def repeated_source_lines(source_text: str) -> set[str]:
    counts = Counter(normalize_line_for_repetition(line) for line in source_text.splitlines())
    return {line for line, count in counts.items() if line and count >= 3}


def is_source_furniture(line: str, repeated_lines: set[str]) -> bool:
    normalized = normalize_line_for_repetition(line)
    return (normalized in repeated_lines or bool(URL_RE.search(line))
            or bool(re.match(r"\s*(?:downloaded from|published under a creative commons|"
                             r"international \(cc by)", line, re.IGNORECASE))
            or "©" in line
            or (len(re.findall(r"[A-Za-z]+", line)) >= 4 and not re.search(r"[a-z]", line)))


def filter_chunk_source_lines(source_text: str, repeated_lines: Optional[set[str]] = None,
                              *, sentences: bool = False) -> str:
    if repeated_lines is None:
        repeated_lines = repeated_source_lines(source_text)
    return "\n".join(line for line in source_text.splitlines()
                     if not is_source_furniture(line, repeated_lines)
                     and (not sentences or len(LOWERCASE_WORD_RE.findall(fold_ligatures(line))) >= 4))


def is_figure_axis_number(line: str) -> bool:
    """Equation tags remain source evidence, even beside numeric runs or captions."""
    return not EQUATION_TAG_LINE_RE.fullmatch(line) and bool(NUMERIC_ONLY_LINE_RE.fullmatch(line))


def filter_figure_axis_lines(source_text: str) -> tuple[str, list[list[float]]]:
    """Remove numeric-only axis runs, preserving body occurrences of their values.

    A round grid must be coarser than the printed precision: otherwise any
    decimal table would lie on a sufficiently fine grid. AP subruns allow
    adjacent axes with different steps to share a reading-order numeric block.
    """
    runs: list[list[float]] = []
    filtered_pages: list[str] = []
    for page in source_text.split("\f"):
        lines = page.split("\n")
        excluded: set[int] = set()
        block: list[int] = []

        def inspect(indices: list[int]) -> None:
            if len(indices) < 3:
                return
            values = [float(lines[i].strip().replace("−", "-").rstrip("%")) for i in indices]
            if not all(math.isfinite(value) for value in values):
                return
            marked: set[int] = set()
            precision = max(len(line.split(".")[1].rstrip("%")) if "." in line else 0
                            for line in (lines[i].strip() for i in indices))
            minimum_step = 5 * 10.0 ** -min(precision, 2)
            # Fine table estimates need an AP, not merely a shared decimal
            # grid. Coarse ticks may interleave panels on a .05/.25 grid.
            ratios = [value / minimum_step for value in values]
            if precision < 3 and all(math.isfinite(ratio) and math.isclose(ratio, round(ratio),
                                                                         rel_tol=1e-9) for ratio in ratios):
                marked.update(range(len(indices)))
            else:
                start = 0
                while start + 2 < len(values):
                    step = values[start + 1] - values[start]
                    end = start + 2
                    while end < len(values) and math.isclose(values[end] - values[end - 1], step,
                                                            rel_tol=1e-9):
                        end += 1
                    if end - start >= 3:
                        marked.update(range(start, end))
                        start = end - 1
                    else:
                        start += 1
            excluded.update(indices[i] for i in marked)

        for index, line in enumerate(lines + ["END"]):
            if is_figure_axis_number(line):
                block.append(index)
            elif line.strip():
                inspect(block)
                block = []
        labels = [i for i, line in enumerate(lines) if FIGURE_LABEL_RE.match(line)]
        for index, line in enumerate(lines):
            if is_figure_axis_number(line) and any(0 < abs(index - label) <= 3 for label in labels):
                excluded.add(index)
        # Report excluded source sequences, including short caption neighbors.
        current: list[float] = []
        for index, line in enumerate(lines + ["END"]):
            if index in excluded:
                current.append(float(line.strip().replace("−", "-").rstrip("%")))
            elif line.strip() and current:
                runs.append(current)
                current = []
        filtered_pages.append("\n".join("" if i in excluded else line for i, line in enumerate(lines)))
    return "\f".join(filtered_pages), runs


def detect_page_number_run(pages: dict[int, str]) -> Optional[dict[str, int]]:
    """Identify pagination by a common printed-number minus PDF-page offset."""
    offsets: dict[int, set[int]] = {}
    for page, text in pages.items():
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidates: set[int] = set()
        for index, line in enumerate(lines):
            if re.fullmatch(r"\d+", line):
                candidates.add(int(line))
            elif index < 3 or index >= len(lines) - 3:
                candidates.update(int(token) for token in line.split() if token.isdigit())
        for number in candidates:
            offsets.setdefault(number - page, set()).add(page)
    if not offsets:
        return None
    # At least two pages are needed to distinguish a run from an isolated integer.
    offset, matched_pages = max(offsets.items(), key=lambda item: (len(item[1]), -item[0]))
    if len(matched_pages) < max(2, math.ceil(0.6 * len(pages))):
        return None
    return {"start": min(matched_pages) + offset, "end": max(matched_pages) + offset}


def normalize_for_trigrams(text: str, *, drop_repeated_lines: bool) -> str:
    """Normalize text for source/output trigram comparison."""
    if drop_repeated_lines:
        text = drop_repeated_source_lines(text)

    text = fold_ligatures(text)
    if ACTIVE_PROFILE == "chunk":
        text = unicodedata.normalize("NFKC", text)
    text = HYPHENATED_LINEBREAK_RE.sub("", text)
    text = delatex_text(text)
    text = text.lower()
    return WHITESPACE_RE.sub(" ", text).strip()


def word_trigrams(text: str) -> set[tuple[str, str, str]]:
    """Return the set of word trigrams in normalized prose text."""
    words = WORD_RE.findall(text)
    if len(words) < 3:
        return set()
    return set(zip(words, words[1:], words[2:]))


def score_trigram_recall(source_text: str, md_text: str) -> tuple[int, str]:
    """Score set recall of normalized source prose trigrams in markdown output."""
    source_norm = normalize_for_trigrams(source_text, drop_repeated_lines=True)
    output_norm = normalize_for_trigrams(md_text, drop_repeated_lines=False)

    source_trigrams = word_trigrams(source_norm)
    if not source_trigrams:
        return 100, "no source prose trigrams after filtering"

    output_trigrams = word_trigrams(output_norm)
    matched = len(source_trigrams & output_trigrams)
    recall = matched / len(source_trigrams)

    if recall >= 0.75:
        score = 100
    elif recall <= 0.40:
        score = 0
    else:
        score = round(100 * (recall - 0.40) / (0.75 - 0.40))

    return score, f"recall {recall:.2f} ({matched}/{len(source_trigrams)} source trigrams)"


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "content_completeness": 0.30,
    "math_quality": 0.25,
    "structural_integrity": 0.20,
    "ocr_artifacts": 0.15,
    "failed_chunks": 0.10,
}

SOURCE_WEIGHTS: dict[str, float] = {
    "content_completeness": 0.20,
    "math_quality": 0.20,
    "structural_integrity": 0.15,
    "ocr_artifacts": 0.10,
    "failed_chunks": 0.10,
    "numeric_fidelity": 0.15,
    "trigram_recall": 0.10,
}

COMPARISON_WEIGHTS: dict[str, float] = {
    "math_quality": 0.40,
    "structural_integrity": 0.30,
    "ocr_artifacts": 0.15,
    "failed_chunks": 0.15,
}


CHUNK_WEIGHTS: dict[str, float] = {
    "content_completeness": 0.30, "math_quality": 0.20,
    "structural_integrity": 0.0, "ocr_artifacts": 0.10,
    "failed_chunks": 0.0, "numeric_fidelity": 0.25, "trigram_recall": 0.15,
}
CHUNK_WEAK_WEIGHTS: dict[str, float] = {
    "content_completeness": 0.50, "math_quality": 0.30,
    "structural_integrity": 0.0, "ocr_artifacts": 0.20, "failed_chunks": 0.0,
}
CHUNK_FLOORS: dict[str, float] = {
    "completeness": 60, "math": 85, "ocr": 70, "numeric": 0.80,
    "trigram": 0.55, "page_trigram_min": 0.15, "uncertain": 8,  # Math serialization can reduce per-page trigram recall; sentence checks also detect omissions.
    "missing_sentences_page_max": 1, "missing_sentences_max": 3,
    "injected_dois": 0,
}


def parse_chunk_floors(items: list[str]) -> dict[str, float]:
    """Reject misspelled or non-finite overrides rather than silently weakening a gate."""
    floors = dict(CHUNK_FLOORS)
    for item in items:
        key, separator, raw = item.partition("=")
        key = key.strip()
        if not separator or key not in floors:
            raise ValueError(f"unknown chunk floor: {item}")
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"chunk floor must be finite and nonnegative: {item}")
        floors[key] = value
    return floors


def compute_weights(math_doc: bool, source_available: bool) -> dict[str, float]:
    """Redistribute math weight if document is non-mathematical."""
    weights = dict(SOURCE_WEIGHTS if source_available else DEFAULT_WEIGHTS)
    if not math_doc:
        math_w = weights["math_quality"]
        weights["math_quality"] = 0.0
        remaining_sum = sum(value for key, value in weights.items() if key != "math_quality")
        if remaining_sum > 0:
            for key in weights:
                if key != "math_quality":
                    weights[key] += math_w * (weights[key] / remaining_sum)
    return weights


def failed_chunk_cap(failed_stats: FailedChunkStats) -> Optional[int]:
    """Return the overall-score hard cap imposed by failed chunks, if any."""
    if failed_stats.count <= 0:
        return None
    return max(10, 50 - round(30 * failed_stats.fraction))


def build_eligibility_reasons(
    dimensions: dict[str, dict[str, Any]],
    failed_stats: FailedChunkStats,
    source_available: bool,
) -> list[str]:
    """Build human-readable reasons for eligibility failure."""
    reasons: list[str] = []

    if failed_stats.count > 0:
        reasons.append(f"{failed_stats.count} failed chunks")

    completeness_score = int(dimensions["content_completeness"]["score"])
    if completeness_score < 30:
        reasons.append(f"content_completeness {completeness_score} < 30")

    if source_available:
        numeric_score = int(dimensions["numeric_fidelity"]["score"])
        trigram_score = int(dimensions["trigram_recall"]["score"])
        if numeric_score < 20:
            reasons.append(f"numeric_fidelity {numeric_score} < 20")
        if trigram_score < 20:
            reasons.append(f"trigram_recall {trigram_score} < 20")

    return reasons


def numeric_fidelity_values(source_text: str, md_text: str,
                            page_count: Optional[int], *,
                            page_number_run: Optional[dict[str, int]] = None,
                            repeated_lines: Optional[set[str]] = None) -> dict[str, Any]:
    source_counter, dropped = numeric_source_counter(
        source_text, page_count, page_number_run=page_number_run, repeated_lines=repeated_lines)
    output_counter = Counter(extract_numeric_tokens_by_line(md_text)[0])
    total = sum(source_counter.values())
    matched = sum(min(count, output_counter[token]) for token, count in source_counter.items())
    absent = source_counter - output_counter
    return {"recall": matched / total if total else 1.0, "total": total,
            "matched": matched, "missing": list(absent.elements()),
            "boilerplate_tokens_dropped": len(dropped),
            "figure_axis_runs": filter_figure_axis_lines(source_text)[1] if ACTIVE_PROFILE == "chunk" else []}


def split_pages_formfeed(source_text: str, first_page: int = 1) -> dict[int, str]:
    pages = source_text.split("\f")
    # pdftotext emits a terminal form feed, which does not introduce a blank page.
    if len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    return {first_page + offset: text for offset, text in enumerate(pages)}


def read_page_texts(source_text: str, first_page: int,
                    page_texts_dir: Optional[str]) -> dict[int, str]:
    if page_texts_dir:
        pages: dict[int, str] = {}
        for name in sorted(os.listdir(page_texts_dir)):
            match = re.search(r"(?:^|[_-])(\d+)\.txt$", name)
            if match:
                pages[int(match.group(1))] = read_text_file(os.path.join(page_texts_dir, name))
        if pages:
            return dict(sorted(pages.items()))
    return split_pages_formfeed(source_text, first_page)


def sentence_math_evidence(text: str) -> tuple[int, int, int]:
    characters = sum(ch in SENTENCE_MATH_SYMBOLS or "GREEK" in unicodedata.name(ch, "")
                     or unicodedata.category(ch).startswith("M") for ch in text)
    characters += sum(len(match.group()) for match in MATH_ARGUMENT_RE.finditer(text))
    tokens = SENTENCE_TOKEN_RE.findall(text)
    single_letters = sum(len(token) == 1 and token.isalpha() for token in tokens)
    return characters, single_letters, len(tokens)


def missing_sentence_check(source_text: str,
                           candidate_trigrams: set[tuple[str, str, str]], *,
                           repeated_lines: Optional[set[str]] = None) -> dict[str, Any]:
    chunk = ACTIVE_PROFILE == "chunk"
    if chunk:
        source_text = filter_chunk_source_lines(source_text, repeated_lines)
        lines = []
        for line in source_text.splitlines():
            characters, single_letters, _ = sentence_math_evidence(line)
            # Keep the existing prose/furniture screen, retaining mathematical
            # continuations so line wrapping cannot erase eligibility evidence.
            if (len(LOWERCASE_WORD_RE.findall(fold_ligatures(line))) >= 4
                    or characters or (single_letters and not PROSE_WORD_RE.search(line))):
                lines.append(line)
        # Measure before NFKC composes accents or delatex strips variables.
        normalized = WHITESPACE_RE.sub(" ", HYPHENATED_LINEBREAK_RE.sub("", fold_ligatures("\n".join(lines))))
    else:
        normalized = normalize_for_trigrams(source_text, drop_repeated_lines=True)
    sentences = 0
    missing = 0
    math_skipped = 0
    heads: list[str] = []
    for sentence in re.split(r"[.!?]\s+", normalized):
        sentence = sentence.strip()
        if len((PROSE_WORD_RE if chunk else SENTENCE_WORD_RE).findall(sentence)) < 8:
            continue
        if chunk:
            eligibility_text = WHITESPACE_RE.sub(" ", sentence)
            math_characters, single_letters, tokens = sentence_math_evidence(eligibility_text)
            if (math_characters / max(1, len(eligibility_text)) >= 0.03
                    or single_letters / max(1, tokens) >= 0.25):
                math_skipped += 1
                continue
        if sum(ch.isdigit() for ch in sentence) / max(1, len(sentence)) >= 0.15:
            continue
        if chunk:
            sentence = normalize_for_trigrams(sentence, drop_repeated_lines=True)
        trigrams = word_trigrams(sentence)
        if not trigrams:
            continue
        sentences += 1
        if len(trigrams & candidate_trigrams) / len(trigrams) < 0.25:
            missing += 1
            if len(heads) < 6:
                heads.append(sentence[:90])
    result = {"sentences": sentences, "missing_sentences": {"count": missing, "heads": heads}}
    if chunk:
        result["math_skipped"] = math_skipped
    return result


def injected_doi_values(source_text: str, md_text: str, doc_text: str = "") -> list[str]:
    # DOI names are case-insensitive; terminal prose punctuation is not part of one.
    def names(text: str, pattern: re.Pattern[str] = DOI_RE) -> set[str]:
        return {re.sub(r"\s+", "", match.rstrip(".;:\"'*").lower()) for match in pattern.findall(text)}
    # Keep each text separate: concatenating pages/documents without a separator
    # can manufacture DOI evidence across unrelated source boundaries.
    sources = [source_text, doc_text]
    printed = set().union(*(names(text, SOURCE_DOI_RE) for text in sources))
    compact_sources = [re.sub(r"\s+", "", text.lower()) for text in sources]
    return sorted(doi for doi in names(md_text) - printed
                  if not any(doi in text for text in compact_sources))


def score_pages(pages: dict[int, str], md_text: str) -> dict[str, Any]:
    candidate_trigrams = word_trigrams(normalize_for_trigrams(md_text, drop_repeated_lines=False))
    checks: dict[str, Any] = {}
    page_number_run = detect_page_number_run(pages)
    repeated_lines = repeated_source_lines("\n".join(pages.values()))
    for page, text in pages.items():
        source_trigrams = word_trigrams(normalize_for_trigrams(text, drop_repeated_lines=True))
        numeric = numeric_fidelity_values(text, md_text, None,
                                          page_number_run=page_number_run, repeated_lines=repeated_lines)
        checks[str(page)] = {
            "trigrams": len(source_trigrams),
            "trigram_recall": len(source_trigrams & candidate_trigrams) / len(source_trigrams) if source_trigrams else 1.0,
            "numerals": numeric["total"], "numeric_recall": numeric["recall"],
            "missing_numerals": numeric["missing"][:12],
            "figure_axis_runs": numeric["figure_axis_runs"],
            **missing_sentence_check(text, candidate_trigrams, repeated_lines=repeated_lines),
        }
    return checks


def add_chunk_profile(report: dict[str, Any], md_text: str, source_text: str,
                      page_count: Optional[int], source_available: bool,
                      first_page: int, page_texts_dir: Optional[str],
                      floor_overrides: Optional[dict[str, float]],
                      candidate_raw: Optional[str] = None,
                      doc_text: str = "") -> None:
    floors = dict(CHUNK_FLOORS)
    if floor_overrides:
        floors.update(floor_overrides)
    dimensions = report["dimensions"]
    values: dict[str, Any] = {
        "completeness": dimensions["content_completeness"]["score"],
        "math": dimensions["math_quality"]["score"],
        "ocr": dimensions["ocr_artifacts"]["score"],
        "score": report["overall_score"], "uncertain": report["uncertain_markers"],
        "completeness_ratio": measured_length(md_text) / measured_length(source_text) if source_available else None,
        "numeric": None, "trigram": None, "page_trigram_min": None,
        "missing_sentences": None, "sentences": None, "math_skipped": None,
        "missing_sentences_page_max": None, "missing_sentences_max": None,
        "injected_dois": None,
    }
    page_checks: dict[str, Any] = {}
    missing: list[str] = []
    injected: list[str] = []
    page_number_run = None
    axis_runs: list[list[float]] = []
    if source_available:
        pages = read_page_texts(source_text, first_page, page_texts_dir)
        page_number_run = detect_page_number_run(pages)
        repeated_lines = repeated_source_lines("\n".join(pages.values()))
        numeric = numeric_fidelity_values(source_text, md_text, page_count,
                                          page_number_run=page_number_run, repeated_lines=repeated_lines)
        missing = numeric["missing"][:25]
        axis_runs = numeric["figure_axis_runs"]
        values["numeric"] = numeric["recall"]
        source_tri = word_trigrams(normalize_for_trigrams(source_text, drop_repeated_lines=True))
        output_tri = word_trigrams(normalize_for_trigrams(md_text, drop_repeated_lines=False))
        values["trigram"] = len(source_tri & output_tri) / len(source_tri) if source_tri else 1.0
        page_checks = score_pages(pages, md_text)
        eligible_pages = [v["trigram_recall"] for v in page_checks.values() if v["trigrams"] >= 40]
        values["page_trigram_min"] = min(eligible_pages) if eligible_pages else None
        missing_counts = [v["missing_sentences"]["count"] for v in page_checks.values()]
        values["missing_sentences_page_max"] = max(missing_counts, default=0)
        values["missing_sentences"] = values["missing_sentences_max"] = sum(missing_counts)
        values["sentences"] = sum(v["sentences"] for v in page_checks.values())
        values["math_skipped"] = sum(v["math_skipped"] for v in page_checks.values())
        # Front matter is generated content too: a fabricated YAML doi must
        # fail even when the transcribed body contains no DOI at all.
        injected = injected_doi_values("\f".join(pages.values()) + "\n" + source_text,
                                       candidate_raw if candidate_raw is not None else md_text, doc_text)
        values["injected_dois"] = len(injected)
    lower_bounds = ["completeness", "math", "ocr"]
    upper_bounds: list[str] = []
    if source_available:
        lower_bounds += ["numeric", "trigram", "page_trigram_min"]
        upper_bounds += ["uncertain", "missing_sentences_page_max", "missing_sentences_max", "injected_dois"]
    failed = [key for key in lower_bounds if values[key] is not None and values[key] < floors[key]]
    failed += [key for key in upper_bounds if values[key] > floors[key]]
    if report["overall_score"] < report["threshold"]:
        failed.append("score")
    report.update({
        "profile": "chunk", "chunk_gate": {"pass": not failed, "failed": failed, "values": values, "floors": floors},
        "page_checks": page_checks, "missing_numerals": missing,
        "page_number_run": page_number_run,
        "figure_axis_runs": axis_runs, "math_skipped": values["math_skipped"],
        "missing_sentences": values["missing_sentences"], "sentences": values["sentences"],
        "injected_dois": values["injected_dois"], "injected_doi_list": injected[:10],
        "det_weak": not source_available, "audit_markers": len(AUDIT_MARKER_RE.findall(md_text)),
    })
    report["pass"] = report["chunk_gate"]["pass"]


def run_quality_check(
    md_path: str,
    pdf_path: Optional[str],
    source_text_path: Optional[str],
    threshold: int,
    meta_json_path: Optional[str] = None,
    *,
    profile: str = "document",
    first_page: int = 1,
    page_texts_dir: Optional[str] = None,
    floors: Optional[dict[str, float]] = None,
    doc_text_path: Optional[str] = None,
) -> dict[str, Any]:
    """Run all scoring dimensions and return the report dict."""
    global ACTIVE_PROFILE
    ACTIVE_PROFILE = profile
    md_text_raw = read_text_file(md_path)
    md_text, front_matter = strip_front_matter(md_text_raw)
    md_text, stray_sentinels = strip_stray_sentinels(md_text)

    source_text: Optional[str] = None
    if source_text_path:
        source_text = read_text_file(source_text_path)
    source_available = bool(source_text and source_text.strip())

    # A corrupted text layer makes recall-vs-source meaningless: score as if
    # no source text had been provided (fidelity dims skipped, page-based
    # completeness), but keep the raw text for math detection.
    source_corrupted = bool(
        source_available and source_text is not None and source_text_is_corrupted(source_text)
    )
    if source_corrupted:
        source_available = False

    page_count = get_page_count(pdf_path)
    page_number_run = None
    repeated_lines = None
    if profile == "chunk" and source_available:
        pages = read_page_texts(source_text or "", first_page, page_texts_dir)
        page_number_run = detect_page_number_run(pages)
        repeated_lines = repeated_source_lines("\n".join(pages.values()))
        if page_count is None:
            page_count = len(pages)
    math_doc = is_mathematical(md_text, source_text)
    weights = compute_weights(math_doc, source_available)
    if profile == "chunk":
        weights = dict(CHUNK_WEIGHTS if source_available else CHUNK_WEAK_WEIGHTS)

    source_for_scoring = source_text if source_available else None
    cc_score, cc_details = score_content_completeness(md_text, page_count, source_for_scoring)
    mq_score, mq_details = score_math_quality(md_text)
    si_score, si_details = score_structural_integrity(md_text)
    oa_score, oa_details = score_ocr_artifacts(md_text)
    fc_stats = score_failed_chunks(md_text, meta_json_path)

    dimension_scores: dict[str, tuple[int, str]] = {
        "content_completeness": (cc_score, cc_details),
        "math_quality": (mq_score, mq_details),
        "structural_integrity": (si_score, si_details),
        "ocr_artifacts": (oa_score, oa_details),
        "failed_chunks": (fc_stats.score, fc_stats.details),
    }

    if source_available and source_text is not None:
        nf_score, nf_details = score_numeric_fidelity(source_text, md_text, page_count,
                                                     page_number_run=page_number_run, repeated_lines=repeated_lines)
        tr_score, tr_details = score_trigram_recall(source_text, md_text)
        dimension_scores["numeric_fidelity"] = (nf_score, nf_details)
        dimension_scores["trigram_recall"] = (tr_score, tr_details)

    dimensions: dict[str, dict[str, Any]] = {}
    for name, (score, details) in dimension_scores.items():
        dimensions[name] = {
            "score": score,
            "weight": round(weights[name], 2),
            "details": details,
        }

    weighted_overall = sum(
        dimension_scores[name][0] * weight for name, weight in weights.items()
    )
    uncertain_count = md_text.count(UNCERTAIN_MARKER)
    uncertain_penalty = min(20, 2 * uncertain_count)
    penalized_overall = max(0.0, weighted_overall - uncertain_penalty)

    hard_cap = failed_chunk_cap(fc_stats)
    if hard_cap is not None:
        penalized_overall = min(penalized_overall, hard_cap)

    overall_score = max(0, min(100, round(penalized_overall)))

    comparison_score = round(
        sum(
            dimension_scores[name][0] * weight
            for name, weight in COMPARISON_WEIGHTS.items()
        )
    )

    eligibility_reasons = build_eligibility_reasons(dimensions, fc_stats, source_available)

    report = {
        "overall_score": overall_score,
        "pass": overall_score >= threshold,
        "threshold": threshold,
        "dimensions": dimensions,
        "is_mathematical": math_doc,
        "path": md_path,
        "comparison_score": comparison_score,
        "eligible": not eligibility_reasons,
        "eligibility_reasons": eligibility_reasons,
        "uncertain_markers": uncertain_count,
        "uncertain_penalty": uncertain_penalty,
        "failed_chunk_cap": hard_cap,
        "front_matter": front_matter,
        "stray_sentinels": stray_sentinels,
        "source_corrupted": source_corrupted,
    }
    if profile == "chunk":
        add_chunk_profile(report, md_text, source_text or "", page_count,
                          source_available, first_page, page_texts_dir, floors, md_text_raw,
                          read_text_file(doc_text_path) if doc_text_path else "")
    return report


def write_json_file(path: str, json_text: str) -> Optional[str]:
    """Write JSON text to path. Returns an error message on failure."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json_text)
            f.write("\n")
    except OSError as exc:
        return str(exc)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score markdown output quality from PDF-to-markdown conversion (0-100).",
    )
    parser.add_argument(
        "--md",
        required=True,
        help="Path to the markdown output file to evaluate.",
    )
    parser.add_argument(
        "--source-pdf",
        default=None,
        help="Path to the source PDF (used for page count).",
    )
    parser.add_argument(
        "--source-text",
        default=None,
        help="Path to extracted plain text from the PDF (aids math detection and source-relative scoring).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=60,
        help="Minimum score to pass (default: 60).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output JSON report to stderr (always enabled; this flag is accepted for compatibility).",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Additionally write the JSON report to this path.",
    )
    parser.add_argument(
        "--meta-json",
        default=None,
        help="Optional per-chunk metadata JSON for failed-chunk scoring.",
    )

    parser.add_argument("--profile", choices=("document", "chunk"), default="document")
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--page-texts-dir", default=None,
                        help="Per-paper-page UTF-8 text files (page_N.txt or N.txt).")
    parser.add_argument("--doc-text", default=None,
                        help="Whole-document reading-order text, used only to check DOI provenance.")
    parser.add_argument("--floor", action="append", default=[], metavar="K=V",
                        help="Override one chunk floor; repeatable, overrides the environment.")
    args = parser.parse_args()
    global ACTIVE_PROFILE
    ACTIVE_PROFILE = args.profile
    floors = None
    if args.profile == "chunk":
        if args.first_page < 1:
            parser.error("--first-page must be positive")
        floor_items = [v for v in os.environ.get("GEMINI_PDF_CHUNK_FLOORS", "").split(",") if v.strip()]
        try:
            floors = parse_chunk_floors(floor_items + args.floor)
        except ValueError as exc:
            parser.error(str(exc))

    report = run_quality_check(
        md_path=args.md,
        pdf_path=args.source_pdf,
        source_text_path=args.source_text,
        threshold=args.threshold,
        meta_json_path=args.meta_json,
        profile=args.profile,
        first_page=args.first_page,
        page_texts_dir=args.page_texts_dir,
        floors=floors,
        doc_text_path=args.doc_text,
    )

    json_text = json.dumps(report, indent=2)
    if args.json_out:
        json_out_error = write_json_file(args.json_out, json_text)
        if json_out_error is not None:
            report["json_out_error"] = json_out_error
            json_text = json.dumps(report, indent=2)

    sys.stderr.write(json_text)
    sys.stderr.write("\n")

    status = "PASS" if report["pass"] else "FAIL"
    print(f"{status} ({report['overall_score']}/100)")

    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
