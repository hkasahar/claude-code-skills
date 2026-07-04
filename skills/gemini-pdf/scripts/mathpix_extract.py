#!/usr/bin/env python3
"""
Mathpix PDF-to-Markdown extraction wrapper.

Core Mathpix API interactions (upload, poll, download) for use in
standalone conversion or as the extraction stage of a hybrid pipeline
(Mathpix extraction -> Gemini formatting).

Usage:
    # Extract mode: raw markdown to stdout (for piping to Gemini)
    python3 mathpix_extract.py input.pdf --mode extract

    # Convert mode: write final markdown to file
    python3 mathpix_extract.py input.pdf output.md --mode convert

    # Verbose progress to stderr
    python3 mathpix_extract.py input.pdf --verbose

Environment variables required:
    MATHPIX_APP_ID  - Your Mathpix app ID
    MATHPIX_APP_KEY - Your Mathpix app key
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests


# Retry configuration
MAX_RETRIES: int = 3
RETRY_DELAY: int = 5  # seconds


def log(msg: str, verbose: bool = True) -> None:
    """Print a message to stderr (never pollutes stdout in extract mode)."""
    if verbose:
        print(msg, file=sys.stderr)


def get_mathpix_credentials() -> tuple[str, str]:
    """
    Load Mathpix API credentials from environment variables.

    Returns (app_id, app_key). Exits with code 1 if either is missing.
    """
    app_id: Optional[str] = os.environ.get("MATHPIX_APP_ID")
    app_key: Optional[str] = os.environ.get("MATHPIX_APP_KEY")

    if not app_id or not app_key:
        print("=" * 60, file=sys.stderr)
        print("ERROR: Mathpix API credentials are required", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(file=sys.stderr)
        print("This tool requires Mathpix for high-quality math OCR.", file=sys.stderr)
        print(file=sys.stderr)
        print("Setup instructions:", file=sys.stderr)
        print("  1. Sign up at https://mathpix.com", file=sys.stderr)
        print("  2. Go to https://accounts.mathpix.com/ocr-api", file=sys.stderr)
        print("  3. Create an API key", file=sys.stderr)
        print("  4. Set environment variables:", file=sys.stderr)
        print(file=sys.stderr)
        print('     export MATHPIX_APP_ID="your_app_id"', file=sys.stderr)
        print('     export MATHPIX_APP_KEY="your_app_key"', file=sys.stderr)
        print(file=sys.stderr)
        print("  Add to ~/.zshrc or ~/.bashrc for persistence.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)

    return app_id, app_key


def upload_pdf_with_retry(
    pdf_path: str, app_id: str, app_key: str, verbose: bool = False
) -> str:
    """Upload PDF to Mathpix with automatic retry on failure.

    Returns the Mathpix pdf_id on success. Exits with code 2 on failure.
    """
    url = "https://api.mathpix.com/v3/pdf"

    headers = {
        "app_id": app_id,
        "app_key": app_key,
    }

    options = {
        "conversion_formats": {"md": True},
        "math_inline_delimiters": ["$", "$"],
        "math_display_delimiters": ["$$", "$$"],
        "rm_spaces": True,
    }

    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if verbose and attempt > 1:
                log(f"  Retry attempt {attempt}/{MAX_RETRIES}...")

            with open(pdf_path, "rb") as f:
                files = {
                    "file": (Path(pdf_path).name, f, "application/pdf"),
                    "options_json": (None, json.dumps(options), "application/json"),
                }
                response = requests.post(
                    url, headers=headers, files=files, timeout=120
                )

            if response.status_code == 200:
                result = response.json()
                pdf_id = result.get("pdf_id")
                if pdf_id:
                    return pdf_id
                last_error = "No pdf_id in response"
            else:
                last_error = f"HTTP {response.status_code}: {response.text}"

        except requests.exceptions.Timeout:
            last_error = "Request timed out"
        except requests.exceptions.RequestException as e:
            last_error = str(e)

        if attempt < MAX_RETRIES:
            log(f"  Upload failed: {last_error}", verbose)
            log(f"  Waiting {RETRY_DELAY}s before retry...", verbose)
            time.sleep(RETRY_DELAY)

    print(
        f"Error: Failed to upload PDF after {MAX_RETRIES} attempts", file=sys.stderr
    )
    print(f"Last error: {last_error}", file=sys.stderr)
    sys.exit(2)


def check_processing_status(pdf_id: str, app_id: str, app_key: str) -> dict:
    """Check the processing status of a PDF."""
    url = f"https://api.mathpix.com/v3/pdf/{pdf_id}"

    headers = {
        "app_id": app_id,
        "app_key": app_key,
    }

    response = requests.get(url, headers=headers, timeout=30)
    return response.json()


def wait_for_completion(
    pdf_id: str, app_id: str, app_key: str, verbose: bool = False
) -> dict:
    """Wait for PDF processing to complete with retry on transient errors.

    Exits with code 2 if processing fails or connection is lost.
    """
    consecutive_errors: int = 0
    max_consecutive_errors: int = 3

    while True:
        try:
            status = check_processing_status(pdf_id, app_id, app_key)
            consecutive_errors = 0  # Reset on success

            state = status.get("status")

            if verbose:
                percent = status.get("percent_done", 0)
                print(f"  Processing: {percent}% complete", end="\r", file=sys.stderr)

            if state == "completed":
                if verbose:
                    print("\n  Processing complete!", file=sys.stderr)
                return status
            elif state == "error":
                print(
                    f"\nError processing PDF: {status.get('error')}", file=sys.stderr
                )
                sys.exit(2)

        except requests.exceptions.RequestException as e:
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                print(
                    f"\nError: Lost connection to Mathpix API: {e}", file=sys.stderr
                )
                sys.exit(2)
            if verbose:
                print(
                    f"\n  Connection error, retrying... ({consecutive_errors}/{max_consecutive_errors})",
                    file=sys.stderr,
                )

        time.sleep(2)


def get_markdown_result_with_retry(
    pdf_id: str, app_id: str, app_key: str, verbose: bool = False
) -> str:
    """Download the markdown result with retry on failure.

    Returns the markdown string. Exits with code 2 on failure.
    """
    url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.md"

    headers = {
        "app_id": app_id,
        "app_key": app_key,
    }

    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if verbose and attempt > 1:
                log(f"  Retry attempt {attempt}/{MAX_RETRIES}...")

            response = requests.get(url, headers=headers, timeout=120)

            if response.status_code == 200:
                return response.text
            else:
                last_error = f"HTTP {response.status_code}: {response.text}"

        except requests.exceptions.Timeout:
            last_error = "Request timed out"
        except requests.exceptions.RequestException as e:
            last_error = str(e)

        if attempt < MAX_RETRIES:
            log(f"  Download failed: {last_error}", verbose)
            log(f"  Waiting {RETRY_DELAY}s before retry...", verbose)
            time.sleep(RETRY_DELAY)

    print(
        f"Error: Failed to download markdown after {MAX_RETRIES} attempts",
        file=sys.stderr,
    )
    print(f"Last error: {last_error}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mathpix PDF-to-Markdown extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
modes:
  extract   Output raw markdown to stdout (default).
            Use this for hybrid pipeline: Mathpix -> Gemini formatting.
  convert   Write final markdown to output file (standalone Mathpix).

examples:
  python3 mathpix_extract.py paper.pdf                        # extract to stdout
  python3 mathpix_extract.py paper.pdf --verbose              # with progress
  python3 mathpix_extract.py paper.pdf out.md --mode convert  # write to file
  python3 mathpix_extract.py paper.pdf --mode extract | ...   # pipe to next stage

environment variables:
  MATHPIX_APP_ID   Your Mathpix app ID (required)
  MATHPIX_APP_KEY  Your Mathpix app key (required)
""",
    )
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output markdown file path (required for convert mode if not auto-named)",
    )
    parser.add_argument(
        "--mode",
        choices=["extract", "convert"],
        default="extract",
        help="extract: raw markdown to stdout (default); convert: write to file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress information to stderr",
    )

    args = parser.parse_args()

    # Validate input file
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: PDF file not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)
    if not pdf_path.is_file():
        print(f"Error: Not a file: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    # Resolve output path for convert mode
    output_path: Optional[Path] = None
    if args.mode == "convert":
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = pdf_path.with_suffix(".md")

    # 1. Get credentials
    app_id, app_key = get_mathpix_credentials()

    # 2. Upload PDF
    log(f"Uploading {pdf_path.name} to Mathpix...", args.verbose)
    pdf_id = upload_pdf_with_retry(str(pdf_path), app_id, app_key, args.verbose)
    log(f"PDF ID: {pdf_id}", args.verbose)

    # 3. Poll for completion
    log("Waiting for processing...", args.verbose)
    wait_for_completion(pdf_id, app_id, app_key, args.verbose)

    # 4. Download markdown
    log("Downloading markdown...", args.verbose)
    markdown = get_markdown_result_with_retry(pdf_id, app_id, app_key, args.verbose)

    # 5. Output
    if args.mode == "extract":
        # Raw markdown to stdout only -- no file writes
        sys.stdout.write(markdown)
    else:
        # Convert mode: write to file
        assert output_path is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown)
        # Print path to stdout so callers can find it
        print(str(output_path))
        log(f"Wrote {len(markdown):,} chars to {output_path}", args.verbose)


if __name__ == "__main__":
    main()
