#!/usr/bin/env python3
"""Sanitize converted Markdown before it enters the trusted reference library.

Conversion input is an untrusted PDF; a malicious document could plant active
HTML in the transcribed Markdown. This strips the dangerous constructs and
reports what was removed. Prose, math, links, and ordinary HTML tags that
downstream Markdown renderers treat as inert are left untouched.

Usage:
    python3 sanitize_md.py file.md          # sanitize in place, report to stderr
    python3 sanitize_md.py file.md --check  # report only, do not modify

Exit 0 always (sanitization is best-effort cleanup, not a gate).
"""

from __future__ import annotations

import argparse
import re
import sys

# Each entry: (label, compiled pattern). Patterns are case-insensitive and
# DOTALL so multi-line script blocks are caught.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("script block", re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)),
    ("script tag", re.compile(r"</?script\b[^>]*>", re.IGNORECASE)),
    ("iframe block", re.compile(r"<iframe\b[^>]*>.*?</iframe\s*>", re.IGNORECASE | re.DOTALL)),
    ("iframe tag", re.compile(r"</?iframe\b[^>]*>", re.IGNORECASE)),
    ("object/embed tag", re.compile(r"</?(?:object|embed|applet)\b[^>]*>", re.IGNORECASE)),
    ("event handler attribute", re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)),
    ("javascript: URL", re.compile(r"javascript\s*:", re.IGNORECASE)),
]


def sanitize(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Return (clean_text, [(label, removal_count), ...])."""
    removed: list[tuple[str, int]] = []
    for label, pattern in PATTERNS:
        text, n = pattern.subn("", text)
        if n:
            removed.append((label, n))
    return text, removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Strip active HTML from converted Markdown.")
    parser.add_argument("md_file", help="Markdown file to sanitize in place")
    parser.add_argument("--check", action="store_true", help="Report only; do not modify the file")
    args = parser.parse_args()

    try:
        with open(args.md_file, encoding="utf-8", errors="replace") as f:
            original = f.read()
    except OSError as e:
        print(f"sanitize_md: cannot read {args.md_file}: {e}", file=sys.stderr)
        return 0

    clean, removed = sanitize(original)

    if not removed:
        return 0

    for label, n in removed:
        print(f"sanitize_md: removed {n} {label}(s)", file=sys.stderr)

    if not args.check:
        with open(args.md_file, "w", encoding="utf-8") as f:
            f.write(clean)
        print(f"sanitize_md: {args.md_file} sanitized", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
