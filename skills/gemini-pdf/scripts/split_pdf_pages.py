#!/usr/bin/env python3
"""Split a PDF into page-range PDFs with manifest output.

This utility is designed for PDF-to-Markdown pipelines that pair generated
range PDFs with tools using 1-indexed, inclusive page ranges.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


EXIT_USAGE = 1
EXIT_PROCESSING = 2
EXIT_DEPENDENCY = 3


class SplitPdfError(Exception):
    """Base class for controlled command failures."""


class UsageError(SplitPdfError):
    """Invalid command-line arguments or user-supplied paths."""


class ProcessingError(SplitPdfError):
    """A dependency was present, but processing the PDF failed."""


class DependencyError(SplitPdfError):
    """Required PDF tooling is unavailable."""


@dataclass(frozen=True)
class ManifestEntry:
    filename: str
    first_page: int
    last_page: int


class ManifestArgumentParser(argparse.ArgumentParser):
    """ArgumentParser variant that uses exit code 1 for usage errors."""

    def print_help(self, file: object | None = None) -> None:
        super().print_help(file if file is not None else sys.stderr)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = ManifestArgumentParser(
        description=(
            "Split a PDF into consecutive chunk PDFs or extract a single "
            "1-indexed inclusive page range, printing a tab-separated "
            "manifest to stdout."
        )
    )
    parser.add_argument("input_pdf", help="input PDF path")
    parser.add_argument("outdir", help="directory where output PDFs are written")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--pages-per-chunk",
        type=positive_int,
        metavar="N",
        help="split into consecutive chunks of N pages",
    )
    mode.add_argument(
        "--range",
        dest="page_range",
        nargs=2,
        type=int,
        metavar=("A", "B"),
        help="extract 1-indexed inclusive pages A through B",
    )
    return parser


def ensure_paths(input_pdf: Path, outdir: Path) -> None:
    if not input_pdf.is_file():
        raise UsageError(f"input PDF does not exist or is not a file: {input_pdf}")
    if outdir.exists() and not outdir.is_dir():
        raise UsageError(f"output path exists but is not a directory: {outdir}")
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProcessingError(f"failed to create output directory {outdir}: {exc}") from exc


def build_manifest(args: argparse.Namespace, page_count: int) -> list[ManifestEntry]:
    if page_count < 1:
        raise ProcessingError("input PDF has no pages")

    if args.pages_per_chunk is not None:
        entries: list[ManifestEntry] = []
        chunk_size = args.pages_per_chunk
        chunk_index = 1
        for first_page in range(1, page_count + 1, chunk_size):
            last_page = min(first_page + chunk_size - 1, page_count)
            entries.append(
                ManifestEntry(f"chunk_{chunk_index:03d}.pdf", first_page, last_page)
            )
            chunk_index += 1
        return entries

    first_page, last_page = args.page_range
    if not (1 <= first_page <= last_page <= page_count):
        raise UsageError(
            "invalid page range "
            f"{first_page}..{last_page}; expected 1 <= A <= B <= {page_count}"
        )
    return [
        ManifestEntry(
            f"range_{first_page:03d}_{last_page:03d}.pdf",
            first_page,
            last_page,
        )
    ]


def print_manifest(entries: Sequence[ManifestEntry]) -> None:
    for entry in entries:
        print(f"{entry.filename}\t{entry.first_page}\t{entry.last_page}")


def load_pypdf_reader(input_pdf: Path, pdf_reader_class: Any) -> Any:
    try:
        reader = pdf_reader_class(str(input_pdf))
    except Exception as exc:
        raise ProcessingError(f"failed to read PDF {input_pdf}: {exc}") from exc

    try:
        is_encrypted = bool(reader.is_encrypted)
    except Exception as exc:
        raise ProcessingError(f"failed to inspect encryption for {input_pdf}: {exc}") from exc

    if is_encrypted:
        try:
            decrypt_result = reader.decrypt("")
        except Exception as exc:
            raise ProcessingError(
                "PDF is encrypted and blank-password decryption failed: " f"{exc}"
            ) from exc
        if not decrypt_result:
            raise ProcessingError("PDF is encrypted and blank-password decryption failed")

    return reader


def get_pypdf_page_count(reader: Any) -> int:
    try:
        return len(reader.pages)
    except Exception as exc:
        raise ProcessingError(f"failed to count PDF pages with pypdf: {exc}") from exc


def atomic_write_pypdf(writer: Any, output_pdf: Path) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(output_pdf.parent),
            prefix=f".{output_pdf.name}.",
            suffix=".tmp",
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            writer.write(tmp_file)
        os.replace(tmp_path, output_pdf)
    except Exception as exc:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ProcessingError(f"failed to write {output_pdf}: {exc}") from exc


def split_with_pypdf(
    reader: Any,
    pdf_writer_class: Any,
    outdir: Path,
    entries: Sequence[ManifestEntry],
) -> None:
    for entry in entries:
        writer = pdf_writer_class()
        for page_number in range(entry.first_page, entry.last_page + 1):
            # Manifest/range pages are 1-indexed inclusive; pypdf pages are 0-indexed.
            writer.add_page(reader.pages[page_number - 1])
        atomic_write_pypdf(writer, outdir / entry.filename)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise DependencyError(
            "pypdf is unavailable and required Poppler tool is missing from PATH: "
            f"{name}"
        )
    return path


def require_poppler_tools() -> dict[str, str]:
    return {
        "pdfinfo": require_tool("pdfinfo"),
        "pdfseparate": require_tool("pdfseparate"),
        "pdfunite": require_tool("pdfunite"),
    }


def command_error(proc: subprocess.CompletedProcess[str]) -> str:
    details = proc.stderr.strip() or proc.stdout.strip()
    if details:
        return details
    return f"exit status {proc.returncode}"


def run_poppler(
    argv: Sequence[str], failure_message: str
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProcessingError(f"{failure_message}: {exc}") from exc
    if proc.returncode != 0:
        raise ProcessingError(f"{failure_message}: {command_error(proc)}")
    return proc


def get_poppler_page_count(pdfinfo: str, input_pdf: Path) -> int:
    proc = run_poppler([pdfinfo, str(input_pdf)], f"failed to inspect {input_pdf}")
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            _, value = line.split(":", 1)
            try:
                page_count = int(value.strip())
            except ValueError as exc:
                raise ProcessingError(f"pdfinfo returned invalid page count: {line}") from exc
            if page_count < 1:
                raise ProcessingError("input PDF has no pages")
            return page_count
    raise ProcessingError("pdfinfo output did not include a Pages line")


def make_private_tempdir(outdir: Path) -> Path:
    try:
        return Path(tempfile.mkdtemp(prefix=".split_pdf_pages_", dir=str(outdir)))
    except OSError as exc:
        raise ProcessingError(
            f"failed to create temporary directory under {outdir}: {exc}"
        ) from exc


def split_single_pages(pdfseparate: str, input_pdf: Path, tempdir: Path) -> None:
    pattern = tempdir / "page-%d.pdf"
    run_poppler(
        [pdfseparate, str(input_pdf), str(pattern)],
        f"failed to split {input_pdf} into single-page PDFs",
    )


def atomic_pdfunite(pdfunite: str, source_pages: Sequence[Path], output_pdf: Path) -> None:
    fd = -1
    tmp_path: Path | None = None
    try:
        fd, raw_tmp_path = tempfile.mkstemp(
            prefix=f".{output_pdf.name}.",
            suffix=".tmp.pdf",
            dir=str(output_pdf.parent),
        )
        os.close(fd)
        fd = -1
        tmp_path = Path(raw_tmp_path)
        tmp_path.unlink()

        run_poppler(
            [pdfunite, *[str(path) for path in source_pages], str(tmp_path)],
            f"failed to write {output_pdf}",
        )
        os.replace(tmp_path, output_pdf)
    except Exception as exc:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, ProcessingError):
            raise
        raise ProcessingError(f"failed to write {output_pdf}: {exc}") from exc


def split_with_poppler(
    tools: dict[str, str],
    input_pdf: Path,
    outdir: Path,
    entries: Sequence[ManifestEntry],
    page_count: int,
) -> None:
    tempdir = make_private_tempdir(outdir)
    try:
        split_single_pages(tools["pdfseparate"], input_pdf, tempdir)

        # pdfseparate emits unpadded names such as page-1.pdf; never glob-sort them.
        single_pages = [
            tempdir / f"page-{page_number}.pdf"
            for page_number in range(1, page_count + 1)
        ]
        missing_pages = [path.name for path in single_pages if not path.is_file()]
        if missing_pages:
            preview = ", ".join(missing_pages[:5])
            if len(missing_pages) > 5:
                preview += ", ..."
            raise ProcessingError(f"pdfseparate did not produce expected pages: {preview}")

        for entry in entries:
            source_pages = single_pages[entry.first_page - 1 : entry.last_page]
            atomic_pdfunite(tools["pdfunite"], source_pages, outdir / entry.filename)
    finally:
        try:
            shutil.rmtree(tempdir)
        except OSError as exc:
            print(
                f"warning: failed to remove temporary directory {tempdir}: {exc}",
                file=sys.stderr,
            )


def run(args: argparse.Namespace) -> list[ManifestEntry]:
    input_pdf = Path(args.input_pdf)
    outdir = Path(args.outdir)
    ensure_paths(input_pdf, outdir)

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        tools = require_poppler_tools()
        page_count = get_poppler_page_count(tools["pdfinfo"], input_pdf)
        entries = build_manifest(args, page_count)
        split_with_poppler(tools, input_pdf, outdir, entries, page_count)
        return entries

    reader = load_pypdf_reader(input_pdf, PdfReader)
    page_count = get_pypdf_page_count(reader)
    entries = build_manifest(args, page_count)
    split_with_pypdf(reader, PdfWriter, outdir, entries)
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        entries = run(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ProcessingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PROCESSING
    except DependencyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPENDENCY

    print_manifest(entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
