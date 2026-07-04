#!/usr/bin/env python3
"""Score markdown output quality from PDF-to-markdown conversion on a 0-100 scale.

Evaluates five dimensions: content completeness, math quality, structural
integrity, OCR artifact detection, and failed chunk count. Adaptive weighting
redistributes math weight when the source is non-mathematical.

Exit 0 = pass, Exit 1 = fail.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def is_mathematical(md_text: str, source_text: Optional[str]) -> bool:
    """Heuristic: does the content look mathematical?"""
    # Check output markdown for dollar-sign math
    dollar_count = md_text.count("$")
    if dollar_count >= 4:
        return True

    # Check for equation-number patterns like (1), (2.3) on their own line
    eq_nums = re.findall(r"^\s*\(\d+(?:\.\d+)?\)\s*$", md_text, re.MULTILINE)
    if len(eq_nums) >= 2:
        return True

    # Check source text for math Unicode (integrals, summations, Greek, etc.)
    if source_text:
        math_unicode = re.findall(r"[\u0370-\u03FF\u2200-\u22FF\u2A00-\u2AFF\u222B\u2211]", source_text)
        if len(math_unicode) >= 3:
            return True

    return False


# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------

def score_content_completeness(md_text: str, page_count: Optional[int]) -> tuple[int, str]:
    """Score based on chars per page. 100 if in [2000, 12000], linear falloff."""
    char_count = len(md_text)

    if char_count == 0:
        return 0, "empty file"

    if page_count is None or page_count <= 0:
        # No page info: estimate pages from md length assuming 5K chars/page
        est_pages = max(1, round(char_count / 5000))
        return 70, f"{char_count} chars, ~{est_pages} pages (estimated, no PDF)"

    cpp = char_count / page_count

    if 2000 <= cpp <= 12000:
        score = 100
    elif cpp < 2000:
        # Linear scale: 0 at 0 cpp, 100 at 2000 cpp
        score = max(0, round(100 * cpp / 2000))
    else:
        # Linear scale: 100 at 12000, 0 at 24000 (generous upper bound)
        score = max(0, round(100 * (1 - (cpp - 12000) / 12000)))

    return score, f"{cpp:.0f} chars/page ({page_count} pages)"


def score_math_quality(md_text: str) -> tuple[int, str]:
    """Score LaTeX math quality: balanced delimiters, well-formed commands."""
    if not md_text.strip():
        return 0, "empty file"

    penalties: list[str] = []
    total_penalty = 0

    # --- Count inline math ($...$) ---
    # Remove display math first to avoid double-counting
    text_no_display = re.sub(r"\$\$[^$]*?\$\$", "", md_text, flags=re.DOTALL)
    # Also remove code blocks to avoid false positives
    text_no_code = re.sub(r"```[^`]*?```", "", text_no_display, flags=re.DOTALL)
    text_no_code = re.sub(r"`[^`]*?`", "", text_no_code)

    inline_dollars = text_no_code.count("$")
    if inline_dollars % 2 != 0:
        total_penalty += 15
        penalties.append(f"{inline_dollars} inline $ (odd)")
    inline_count = inline_dollars // 2

    # --- Count display math ($$...$$) ---
    display_dollars = md_text.count("$$")
    if display_dollars % 2 != 0:
        total_penalty += 15
        penalties.append(f"{display_dollars} $$ (odd)")
    display_count = display_dollars // 2

    # --- Check for broken LaTeX commands ---
    # \frac{ without matching }
    frac_opens = len(re.findall(r"\\frac\s*\{", md_text))
    # Count closing braces after each \frac — simplified: just check total brace balance
    # in math contexts is even. Instead check for obviously broken patterns.
    broken_frac = len(re.findall(r"\\frac\s*\{[^}]*$", md_text, re.MULTILINE))
    if broken_frac > 0:
        total_penalty += 5 * broken_frac
        penalties.append(f"{broken_frac} broken \\frac")

    # \sum, \int without following content
    broken_sum = len(re.findall(r"\\(?:sum|int|prod)\s*[^_^{\\a-zA-Z\s\d]", md_text))

    # Stray backslashes: \\ outside of math/table context (very rough)
    # Skip this — too many false positives in tables and aligned environments.

    # Common corruption patterns
    corruption = len(re.findall(r"\\[a-zA-Z]+\{[^}]*\\[a-zA-Z]+\{[^}]*$", md_text, re.MULTILINE))
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

    # Title: first # heading (weight 20)
    max_total += 20
    has_title = bool(re.search(r"^# [^\n]+", md_text, re.MULTILINE))
    if has_title:
        total += 20
        found.append("title")

    # Sections: at least 2 ## headings (weight 25)
    max_total += 25
    sections = re.findall(r"^## [^\n]+", md_text, re.MULTILINE)
    section_count = len(sections)
    if section_count >= 2:
        total += 25
        found.append(f"{section_count} sections")
    elif section_count == 1:
        total += 12
        found.append(f"{section_count} section")

    # Bibliography: References or Bibliography heading or section (weight 20)
    max_total += 20
    has_bib = bool(re.search(
        r"^#{1,3}\s*(References|Bibliography|Works Cited)",
        md_text,
        re.MULTILINE | re.IGNORECASE,
    ))
    if has_bib:
        total += 20
        found.append("bibliography")

    # Tables: |---| pattern (weight 15)
    max_total += 15
    table_seps = re.findall(r"\|[-:]+\|", md_text)
    table_count = len(table_seps)
    if table_count > 0:
        total += 15
        found.append(f"{table_count} tables")

    # Theorem/proof markers (weight 20)
    max_total += 20
    theorem_patterns = re.findall(
        r"(?:^|\n)\s*\*{0,2}(Theorem|Lemma|Proposition|Corollary|Proof|Definition|Remark|Assumption)",
        md_text,
        re.IGNORECASE,
    )
    if theorem_patterns:
        total += 20
        found.append(f"{len(theorem_patterns)} theorem/proof markers")

    # Scale to 100
    score = round(100 * total / max_total) if max_total > 0 else 0
    details = ", ".join(found) if found else "no structural elements found"
    return score, details


def score_ocr_artifacts(md_text: str) -> tuple[int, str]:
    """Score OCR artifact presence: replacement chars, garbled runs, ligatures."""
    if not md_text.strip():
        return 0, "empty file"

    issues: list[str] = []
    total_penalty = 0

    # Unicode replacement characters (U+FFFD)
    replacement_count = md_text.count("\uFFFD")
    if replacement_count > 0:
        total_penalty += min(50, replacement_count * 5)
        issues.append(f"{replacement_count} replacement chars")

    # Garbled consonant runs: 4+ consonants without vowels (excluding common
    # abbreviations and LaTeX commands). Only check outside code/math blocks.
    text_clean = re.sub(r"\$[^$]*?\$", "", md_text, flags=re.DOTALL)
    text_clean = re.sub(r"```[^`]*?```", "", text_clean, flags=re.DOTALL)
    text_clean = re.sub(r"`[^`]*?`", "", text_clean)
    text_clean = re.sub(r"\\[a-zA-Z]+", "", text_clean)  # remove LaTeX commands
    text_clean = re.sub(r"https?://\S+", "", text_clean)  # remove URLs

    garbled = re.findall(r"\b[bcdfghjklmnpqrstvwxyz]{5,}\b", text_clean, re.IGNORECASE)
    garbled_count = len(garbled)
    if garbled_count > 0:
        total_penalty += min(30, garbled_count * 3)
        issues.append(f"{garbled_count} garbled runs")

    # Unresolved ligatures: single Unicode chars for ff (U+FB00), fi (U+FB01),
    # fl (U+FB02), ffi (U+FB03), ffl (U+FB04)
    ligature_chars = re.findall(r"[\uFB00-\uFB04]", md_text)
    lig_count = len(ligature_chars)
    if lig_count > 0:
        total_penalty += min(20, lig_count * 2)
        issues.append(f"{lig_count} unresolved ligatures")

    score = max(0, 100 - total_penalty)
    details = ", ".join(issues) if issues else "clean"
    return score, details


def score_failed_chunks(md_text: str) -> tuple[int, str]:
    """Score based on count of CHUNK N FAILED markers."""
    if not md_text.strip():
        return 0, "empty file"

    failed = re.findall(r"<!--\s*CHUNK\s+\d+\s+FAILED\s*-->", md_text)
    count = len(failed)

    if count == 0:
        return 100, "0 failed chunks"

    # Each failed chunk costs 20 points, bottoming at 0
    score = max(0, 100 - count * 20)
    return score, f"{count} failed chunks"


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


def compute_weights(math_doc: bool) -> dict[str, float]:
    """Redistribute math weight if document is non-mathematical."""
    weights = dict(DEFAULT_WEIGHTS)
    if not math_doc:
        math_w = weights.pop("math_quality")
        # Distribute proportionally among remaining dimensions
        remaining_sum = sum(weights.values())
        for k in weights:
            weights[k] += math_w * (weights[k] / remaining_sum)
        weights["math_quality"] = 0.0
    return weights


def run_quality_check(
    md_path: str,
    pdf_path: Optional[str],
    source_text_path: Optional[str],
    threshold: int,
) -> dict[str, Any]:
    """Run all scoring dimensions and return the report dict."""
    # Read markdown
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            md_text = f.read()
    except OSError:
        md_text = ""

    # Read optional source text
    source_text: Optional[str] = None
    if source_text_path:
        try:
            with open(source_text_path, "r", encoding="utf-8", errors="replace") as f:
                source_text = f.read()
        except OSError:
            pass

    # Determine page count
    page_count = get_page_count(pdf_path)

    # Detect if mathematical
    math_doc = is_mathematical(md_text, source_text)

    # Compute adaptive weights
    weights = compute_weights(math_doc)

    # Score each dimension
    cc_score, cc_details = score_content_completeness(md_text, page_count)
    mq_score, mq_details = score_math_quality(md_text)
    si_score, si_details = score_structural_integrity(md_text)
    oa_score, oa_details = score_ocr_artifacts(md_text)
    fc_score, fc_details = score_failed_chunks(md_text)

    dimensions: dict[str, dict[str, Any]] = {
        "content_completeness": {
            "score": cc_score,
            "weight": round(weights["content_completeness"], 2),
            "details": cc_details,
        },
        "math_quality": {
            "score": mq_score,
            "weight": round(weights["math_quality"], 2),
            "details": mq_details,
        },
        "structural_integrity": {
            "score": si_score,
            "weight": round(weights["structural_integrity"], 2),
            "details": si_details,
        },
        "ocr_artifacts": {
            "score": oa_score,
            "weight": round(weights["ocr_artifacts"], 2),
            "details": oa_details,
        },
        "failed_chunks": {
            "score": fc_score,
            "weight": round(weights["failed_chunks"], 2),
            "details": fc_details,
        },
    }

    # Weighted overall score
    overall = sum(
        dim["score"] * dim["weight"] for dim in dimensions.values()
    )
    overall_score = round(overall)

    return {
        "overall_score": overall_score,
        "pass": overall_score >= threshold,
        "threshold": threshold,
        "dimensions": dimensions,
        "is_mathematical": math_doc,
        "path": md_path,
    }


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
        help="Path to extracted plain text from the PDF (aids math detection).",
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

    args = parser.parse_args()

    report = run_quality_check(
        md_path=args.md,
        pdf_path=args.source_pdf,
        source_text_path=args.source_text,
        threshold=args.threshold,
    )

    # Always write JSON report to stderr
    json.dump(report, sys.stderr, indent=2)
    sys.stderr.write("\n")

    # One-line summary to stdout
    status = "PASS" if report["pass"] else "FAIL"
    print(f"{status} ({report['overall_score']}/100)")

    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
