#!/usr/bin/env python3
"""Sync academic papers (PDF + MD + BibTeX) to the central reference library.

Two modes:
  Single paper (called by gemini-pdf after conversion):
    python3 sync_to_reference.py --key KEY --pdf /path/to.pdf --md /path/to.md \
        [--sidecar /path/to.json] [--central-ref /path/to/reference]

  Batch (sync a whole project reference folder at once):
    python3 sync_to_reference.py --batch --ref-dir /path/to/project/ref/ \
        [--central-ref /path/to/reference]

All operations are skip-if-exists (no overwrites, no duplicates).
Exit codes: 0 = success, 1 = error.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

# Opt-in: no default path. Resolve from --central-ref or GEMINI_PDF_REFERENCE_DIR.
DEFAULT_CENTRAL_REF = os.environ.get("GEMINI_PDF_REFERENCE_DIR")

# Copied from generate_bib.py — keep in sync
BOOK_KEYS = {
    "coverandthomas2006", "hall1980martingale", "rao1973",
    "tsybakov2009", "vaart1998", "lattimore2020bandit", "stewart1990",
}


# --- Author formatting (copied from generate_bib.py — keep in sync) ---

def format_author_bibtex(name):
    """Format author name for BibTeX. Handle particles like 'van der Vaart'."""
    particles = ["van der", "van den", "van", "de la", "de", "di", "el"]
    name = name.strip()
    parts = name.split()
    if len(parts) <= 1:
        return name
    for particle in sorted(particles, key=len, reverse=True):
        p_parts = particle.split()
        p_len = len(p_parts)
        for i in range(1, len(parts) - p_len + 1):
            candidate = " ".join(parts[i:i + p_len]).lower()
            if candidate == particle and i + p_len < len(parts):
                given = " ".join(parts[:i])
                family = " ".join(parts[i:])
                return "{" + family + "}, " + given
    family = parts[-1]
    given = " ".join(parts[:-1])
    return f"{family}, {given}"


def format_authors_bibtex(authors):
    """Format list of author names for BibTeX 'author' field."""
    if not authors:
        return ""
    formatted = [format_author_bibtex(a) for a in authors]
    return " and ".join(formatted)


def determine_entry_type(key, venue):
    """Determine BibTeX entry type from key and venue."""
    if key in BOOK_KEYS:
        return "book"
    if not venue:
        return "techreport"
    venue_lower = venue.lower()
    conference_indicators = ["conference", "proceedings", "neurips", "icml",
                             "iclr", "aaai", "aistats", "colt", "nips"]
    if any(ind in venue_lower for ind in conference_indicators):
        return "inproceedings"
    preprint_indicators = ["arxiv", "nber", "working paper", "ssrn"]
    if any(ind in venue_lower for ind in preprint_indicators):
        return "techreport"
    return "article"


# --- Helpers ---

def samefile_safe(src, dst):
    """True if src and dst point to the same file. False if dst doesn't exist."""
    try:
        return os.path.samefile(src, dst)
    except (OSError, ValueError):
        return False


def load_bib_keys(bib_path):
    """Return set of all BibTeX keys in file."""
    if not os.path.isfile(bib_path):
        return set()
    pattern = re.compile(r'^@\w+\{([^,\s]+)\s*,', re.MULTILINE)
    with open(bib_path) as f:
        return set(pattern.findall(f.read()))


def parse_front_matter(md_path):
    """Parse a YAML-ish front matter block from a converted Markdown file.

    Deliberately minimal (no PyYAML dependency): accepts only a leading block
    delimited by `---` lines containing plain `key: value` pairs. Authors may
    be a single line with `;`/` and ` separators. Malformed blocks are
    rejected (empty dict) rather than guessed at.

    Returns a metadata dict shaped like the JSON sidecar:
    {"title": str, "authors": [str, ...], "year": str, "venue": str, "doi": str}
    """
    allowed_keys = {"title", "authors", "year", "journal", "venue", "doi"}
    try:
        with open(md_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}

    # Skip leading blank lines; block must start with a bare ---
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return {}

    meta = {}
    for j in range(i + 1, min(i + 30, len(lines))):
        line = lines[j]
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
        if not m:
            return {}  # malformed block: reject rather than guess
        key, value = m.group(1).lower(), m.group(2).strip().strip('"').strip("'")
        if key not in allowed_keys or not value:
            continue
        # BibTeX-hostile characters are stripped defensively; the md is
        # derived from an untrusted PDF.
        value = value.replace("{", "").replace("}", "").replace("\\", "")
        if key == "authors":
            parts = re.split(r";| and ", value)
            authors = [p.strip() for p in parts if p.strip()]
            if authors:
                meta["authors"] = authors
        elif key == "journal":
            meta["venue"] = value
        else:
            meta[key] = value
    else:
        return {}  # no closing --- within the window: reject

    return meta


def load_sidecar(key, pdf_path, sidecar_path, central_ref):
    """Load metadata from JSON sidecar, trying multiple locations."""
    # 1. Explicit sidecar path
    if sidecar_path and os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 2. Next to input PDF: {pdf_path%.pdf}.json
    if pdf_path:
        auto_sidecar = os.path.splitext(pdf_path)[0] + ".json"
        if os.path.isfile(auto_sidecar):
            try:
                with open(auto_sidecar) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    # 3. In central reference: central_ref/pdf/{key}.json
    if central_ref and key:
        ref_sidecar = os.path.join(central_ref, "pdf", f"{key}.json")
        if os.path.isfile(ref_sidecar):
            try:
                with open(ref_sidecar) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    return {}


def build_bib_entry(key, metadata):
    """Build a BibTeX entry string from metadata dict."""
    venue = metadata.get("venue", "") or metadata.get("journal", "") or ""
    entry_type = determine_entry_type(key, venue)

    fields = []

    authors = metadata.get("authors", [])
    if authors:
        if isinstance(authors, list):
            author_str = format_authors_bibtex(authors)
        else:
            author_str = str(authors)
        if author_str:
            fields.append(f"  author = {{{author_str}}}")

    title = metadata.get("title", "")
    if title:
        fields.append(f"  title = {{{{{title}}}}}")

    year = metadata.get("year", "")
    if year:
        fields.append(f"  year = {{{year}}}")

    if venue:
        if entry_type == "article":
            fields.append(f"  journal = {{{venue}}}")
        elif entry_type == "inproceedings":
            fields.append(f"  booktitle = {{{venue}}}")
        elif entry_type in ("techreport",):
            fields.append(f"  institution = {{{venue}}}")

    doi = metadata.get("doi", "")
    if doi:
        fields.append(f"  doi = {{{doi}}}")

    # Always include file field
    fields.append(f"  file = {{{key}.pdf}}")

    entry = f"@{entry_type}{{{key},\n"
    entry += ",\n".join(fields)
    entry += ",\n}"
    return entry


def atomic_append_bib(bib_path, entry_str):
    """Atomically append a BibTeX entry using temp file + os.replace."""
    if os.path.isfile(bib_path):
        with open(bib_path) as f:
            content = f.read()
    else:
        content = f"% reference.bib\n% Auto-generated {datetime.now().isoformat()}\n"

    if not content.endswith("\n"):
        content += "\n"
    content += "\n" + entry_str + "\n"

    tmp = bib_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, bib_path)


def backup_bib_if_needed(central_ref):
    """Create a one-time backup of reference.bib before modification."""
    bib = os.path.join(central_ref, "reference.bib")
    bak = bib + ".bak"
    if os.path.isfile(bib) and not os.path.isfile(bak):
        shutil.copy2(bib, bak)
        print(f"[sync] Backed up reference.bib → reference.bib.bak", file=sys.stderr)


def find_pdf_dir(ref_dir):
    """Probe both layouts: ref_dir/pdf/ first, then ref_dir/ root."""
    pdf_subdir = os.path.join(ref_dir, "pdf")
    if os.path.isdir(pdf_subdir) and any(f.endswith(".pdf") for f in os.listdir(pdf_subdir)):
        return pdf_subdir
    return ref_dir


def list_pdf_keys(pdf_dir):
    """List all {key}.pdf files and return sorted list of keys."""
    keys = []
    for fname in sorted(os.listdir(pdf_dir)):
        if fname.endswith(".pdf"):
            keys.append(fname[:-4])
    return keys


def find_md(ref_dir, key):
    """Find markdown file for key in ref_dir/md/."""
    md_path = os.path.join(ref_dir, "md", f"{key}.md")
    return md_path if os.path.isfile(md_path) else None


def find_sidecar_in_dir(ref_dir, key):
    """Find JSON sidecar for key, checking multiple locations."""
    # ref_dir/{key}.json (flat project layout)
    flat = os.path.join(ref_dir, f"{key}.json")
    if os.path.isfile(flat):
        return flat
    # ref_dir/pdf/{key}.json (central reference layout)
    nested = os.path.join(ref_dir, "pdf", f"{key}.json")
    if os.path.isfile(nested):
        return nested
    return None


# --- Main sync functions ---

def sync_single(key, pdf_path, md_path, central_ref, sidecar_path=None, bib_keys_cache=None,
                force=False):
    """Sync a single paper to the central reference library.

    All copies are skip-if-exists unless force=True, which overwrites the
    PDF/MD copies (escape hatch for re-converted papers whose earlier, worse
    conversion already occupies the library slot). The bib entry is never
    replaced once its key exists.

    Returns dict with actions taken.
    """
    actions = {"pdf": False, "md": False, "sidecar": False, "bib": False, "skipped": []}

    pdf_dir = os.path.join(central_ref, "pdf")
    md_dir = os.path.join(central_ref, "md")
    os.makedirs(pdf_dir, exist_ok=True)
    os.makedirs(md_dir, exist_ok=True)

    pdf_dest = os.path.join(pdf_dir, f"{key}.pdf")
    md_dest = os.path.join(md_dir, f"{key}.md")
    bib_path = os.path.join(central_ref, "reference.bib")

    # 1. Copy PDF
    if pdf_path and os.path.isfile(pdf_path):
        if samefile_safe(pdf_path, pdf_dest):
            actions["skipped"].append("pdf")
        elif os.path.isfile(pdf_dest) and not force:
            actions["skipped"].append("pdf")
        else:
            shutil.copy2(pdf_path, pdf_dest)
            actions["pdf"] = True
            print(f"[sync] Copied PDF → {pdf_dest}", file=sys.stderr)

            # Also copy sidecar alongside PDF
            src_sidecar = sidecar_path or os.path.splitext(pdf_path)[0] + ".json"
            sidecar_dest = os.path.join(pdf_dir, f"{key}.json")
            if os.path.isfile(src_sidecar) and not os.path.isfile(sidecar_dest):
                if not samefile_safe(src_sidecar, sidecar_dest):
                    shutil.copy2(src_sidecar, sidecar_dest)
                    actions["sidecar"] = True

    # 2. Copy MD
    if md_path and os.path.isfile(md_path):
        if samefile_safe(md_path, md_dest):
            actions["skipped"].append("md")
        elif os.path.isfile(md_dest) and not force:
            actions["skipped"].append("md")
        else:
            shutil.copy2(md_path, md_dest)
            actions["md"] = True
            print(f"[sync] Copied MD → {md_dest}", file=sys.stderr)

    # 3. Update reference.bib
    if bib_keys_cache is not None:
        key_exists = key in bib_keys_cache
    else:
        key_exists = key in load_bib_keys(bib_path)

    if not key_exists:
        metadata = load_sidecar(key, pdf_path, sidecar_path, central_ref)
        if not metadata and md_path and os.path.isfile(md_path):
            # No JSON sidecar anywhere: fall back to the converted MD's own
            # front matter (the sidecar remains authoritative when present).
            metadata = parse_front_matter(md_path)
            if metadata:
                print(f"[sync] Metadata from MD front matter for {key}", file=sys.stderr)
        entry = build_bib_entry(key, metadata)
        backup_bib_if_needed(central_ref)
        atomic_append_bib(bib_path, entry)
        actions["bib"] = True
        # Update cache if provided
        if bib_keys_cache is not None:
            bib_keys_cache.add(key)
        print(f"[sync] Added {key} to reference.bib", file=sys.stderr)
    else:
        actions["skipped"].append("bib")

    return actions


def sync_batch(ref_dir, central_ref):
    """Sync all papers from a project ref_dir to the central reference library."""
    pdf_dir = find_pdf_dir(ref_dir)
    keys = list_pdf_keys(pdf_dir)

    if not keys:
        print(f"[sync] No PDF files found in {pdf_dir}", file=sys.stderr)
        return

    bib_path = os.path.join(central_ref, "reference.bib")
    bib_keys = load_bib_keys(bib_path)

    print(f"[sync] Batch sync: {len(keys)} PDFs from {pdf_dir}", file=sys.stderr)
    print(f"[sync] Central ref: {central_ref} ({len(bib_keys)} existing bib entries)", file=sys.stderr)

    stats = {"pdf": 0, "md": 0, "bib": 0, "skipped": 0}

    for i, key in enumerate(keys, 1):
        pdf_path = os.path.join(pdf_dir, f"{key}.pdf")
        md_path = find_md(ref_dir, key)
        sidecar = find_sidecar_in_dir(ref_dir, key)

        result = sync_single(key, pdf_path, md_path, central_ref, sidecar, bib_keys)

        if result["pdf"]:
            stats["pdf"] += 1
        if result["md"]:
            stats["md"] += 1
        if result["bib"]:
            stats["bib"] += 1
        if result["skipped"]:
            stats["skipped"] += 1

        if i % 100 == 0:
            print(f"[sync] Progress: {i}/{len(keys)}", file=sys.stderr)

    print(f"\n[sync] === Summary ===", file=sys.stderr)
    print(f"[sync] PDFs synced:  {stats['pdf']}", file=sys.stderr)
    print(f"[sync] MDs synced:   {stats['md']}", file=sys.stderr)
    print(f"[sync] BibTeX added: {stats['bib']}", file=sys.stderr)
    print(f"[sync] Skipped:      {stats['skipped']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Sync papers to central reference library"
    )
    parser.add_argument("--key", help="BibTeX key for the paper")
    parser.add_argument("--pdf", help="Path to PDF file")
    parser.add_argument("--md", help="Path to markdown file")
    parser.add_argument("--sidecar", help="Path to JSON metadata sidecar")
    parser.add_argument("--batch", action="store_true",
                        help="Batch mode: sync all PDFs from --ref-dir")
    parser.add_argument("--ref-dir", help="Reference directory for batch mode")
    parser.add_argument("--central-ref", default=DEFAULT_CENTRAL_REF,
                        help="Central reference library path (opt-in; or set GEMINI_PDF_REFERENCE_DIR)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing PDF/MD copies (bib entries are never replaced)")

    args = parser.parse_args()

    if args.batch:
        if not args.ref_dir:
            print("ERROR: --batch requires --ref-dir", file=sys.stderr)
            return 1
        if not os.path.isdir(args.ref_dir):
            print(f"ERROR: ref-dir not found: {args.ref_dir}", file=sys.stderr)
            return 1
        if not args.central_ref:
            print("[sync] No central reference library configured — skipping "
                  "(pass --central-ref or set GEMINI_PDF_REFERENCE_DIR to enable).",
                  file=sys.stderr)
            return 0
        sync_batch(os.path.abspath(args.ref_dir), os.path.abspath(args.central_ref))
    elif args.key:
        if not args.central_ref:
            print("[sync] No central reference library configured — skipping "
                  "(pass --central-ref or set GEMINI_PDF_REFERENCE_DIR to enable).",
                  file=sys.stderr)
            return 0
        sync_single(
            key=args.key,
            pdf_path=args.pdf,
            md_path=args.md,
            central_ref=os.path.abspath(args.central_ref),
            sidecar_path=args.sidecar,
            force=args.force,
        )
    else:
        print("ERROR: Provide --key for single mode or --batch for batch mode",
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
