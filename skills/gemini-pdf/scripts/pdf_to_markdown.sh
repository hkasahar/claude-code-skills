#!/bin/bash
# pdf_to_markdown.sh — Convert PDF to clean Markdown via markitdown + Antigravity (agy)
#
# Two-stage pipeline:
#   1. Extract text from PDF using markitdown (or pdftotext)
#   2. Pipe to Antigravity CLI (`agy --print`) for intelligent reformatting with LaTeX math
#
# For documents exceeding CHUNK_THRESHOLD chars, automatically splits at
# section boundaries and processes each chunk separately to work around
# Antigravity's ~14K output token limit per response.
#
# NOTE: Antigravity CLI v1.0+ has no --model flag. The model is configured in
#   ~/.gemini/antigravity-cli/settings.json (e.g., "Gemini 3.1 Pro (High)").
#   Set it once via `agy /model` interactive command or by editing the file.
#   The GEMINI_PDF_MODEL env var is retained for documentation purposes only.
#
# Usage:
#   pdf_to_markdown.sh <input.pdf> [output.md] [--extractor markitdown|pdftotext] [--no-chunk]
#     [--bib-key KEY] [--no-fallback] [--force-mathpix] [--quality-threshold N]
#
# Examples:
#   pdf_to_markdown.sh paper.pdf
#   pdf_to_markdown.sh paper.pdf output.md --extractor pdftotext
#   pdf_to_markdown.sh paper.pdf --no-chunk

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="$SCRIPT_DIR/prompt_template.txt"
CONTINUATION_PROMPT_FILE="$SCRIPT_DIR/prompt_template_continuation.txt"
# Model is informational only; agy reads it from ~/.gemini/antigravity-cli/settings.json
MODEL="${GEMINI_PDF_MODEL:-(agy settings.json)}"
TIMEOUT="${GEMINI_PDF_TIMEOUT:-600}"
MAX_TOKENS=1500000  # ~6MB of text; Gemini's 2M context window with headroom

# Chunking config
CHUNK_THRESHOLD="${GEMINI_PDF_CHUNK_THRESHOLD:-50000}"   # auto-chunk above this (chars)
CHUNK_MAX_SIZE="${GEMINI_PDF_CHUNK_MAX:-100000}"          # max chars per chunk

# Quality verification + Mathpix fallback
QUALITY_SCRIPT="$SCRIPT_DIR/quality_check.py"
MATHPIX_SCRIPT="$SCRIPT_DIR/mathpix_extract.py"
QUALITY_THRESHOLD="${GEMINI_PDF_QUALITY_THRESHOLD:-60}"
MATHPIX_FALLBACK="${GEMINI_PDF_MATHPIX_FALLBACK:-true}"
CONVERSION_PATH="antigravity"  # tracks which path produced final output

# --- Portable timeout (GNU timeout → gtimeout → perl fallback) ---
_timeout() {
    local secs="$1"; shift
    if command -v timeout &>/dev/null; then
        timeout "$secs" "$@"
    elif command -v gtimeout &>/dev/null; then
        gtimeout "$secs" "$@"
    else
        perl -e '
            use POSIX ":sys_wait_h";
            alarm shift @ARGV;
            $SIG{ALRM} = sub { kill 9, $pid; exit 124 };
            $pid = fork // die "fork: $!";
            if ($pid == 0) { exec @ARGV; die "exec: $!" }
            waitpid($pid, 0);
            exit ($? >> 8);
        ' "$secs" "$@"
    fi
}

# --- Chunking helpers ---

# Split extracted text at major section boundaries.
# Matches lines like "1 Introduction", "10 Conclusion" but NOT "2023 Annual" or "100 Observations".
# Requires: 1-2 digit number, space, uppercase letter, and line under 80 chars (heading heuristic).
# Writes sec_001.txt, sec_002.txt, ... to the given directory.
# The preamble (everything before the first section header) is sec_001.txt.
_split_at_sections() {
    local outdir="$1"
    awk -v outdir="$outdir" '
    BEGIN { idx = 1; file = sprintf("%s/sec_%03d.txt", outdir, idx) }
    /^[0-9][0-9]? [A-Z]/ && length($0) < 80 {
        idx++
        file = sprintf("%s/sec_%03d.txt", outdir, idx)
    }
    { print >> file }
    '
}

# Sub-split an oversized section file at subsection headers or paragraph boundaries.
# Replaces the original file with sub_001.txt, sub_002.txt, ... in a subdirectory.
_subsplit_large() {
    local filepath="$1"
    local max_size="$2"
    local filesize
    filesize=$(wc -c < "$filepath" | tr -d ' ')

    if [[ $filesize -le $max_size ]]; then
        return 0
    fi

    local subdir="${filepath%.txt}_parts"
    mkdir -p "$subdir"

    # Try subsection split first (^[0-9]+\.[0-9]+ [A-Z])
    awk -v outdir="$subdir" '
    BEGIN { idx = 1; file = sprintf("%s/sub_%03d.txt", outdir, idx) }
    /^[0-9]+\.[0-9]+ [A-Z]/ {
        idx++
        file = sprintf("%s/sub_%03d.txt", outdir, idx)
    }
    { print >> file }
    ' "$filepath"

    # Check if subsection split produced multiple files
    local sub_count
    sub_count=$(find "$subdir" -name 'sub_*.txt' | wc -l | tr -d ' ')

    if [[ $sub_count -le 1 ]]; then
        # Subsection split didn't help; fall back to paragraph boundary split
        rm -f "$subdir"/sub_*.txt

        awk -v outdir="$subdir" -v maxchars="$max_size" '
        BEGIN {
            idx = 1
            file = sprintf("%s/sub_%03d.txt", outdir, idx)
            chars = 0
        }
        {
            line_len = length($0) + 1
            # Split at blank lines when current chunk is large enough
            if ($0 == "" && chars >= maxchars * 0.8) {
                idx++
                file = sprintf("%s/sub_%03d.txt", outdir, idx)
                chars = 0
            }
            print >> file
            chars += line_len
        }
        ' "$filepath"
    fi

    # Now check if any sub-parts are still oversized (recursive sub-split)
    for subfile in "$subdir"/sub_*.txt; do
        [[ -f "$subfile" ]] || continue
        local subsize
        subsize=$(wc -c < "$subfile" | tr -d ' ')
        if [[ $subsize -gt $max_size ]]; then
            # Hard split: try paragraph boundaries first, then force line-count split
            local harddir="${subfile%.txt}_hard"
            mkdir -p "$harddir"
            awk -v outdir="$harddir" -v maxchars="$max_size" '
            BEGIN {
                idx = 1
                file = sprintf("%s/part_%03d.txt", outdir, idx)
                chars = 0
            }
            {
                line_len = length($0) + 1
                if ($0 == "" && chars >= maxchars * 0.5) {
                    idx++
                    file = sprintf("%s/part_%03d.txt", outdir, idx)
                    chars = 0
                }
                print >> file
                chars += line_len
            }
            ' "$subfile"

            # Check if hard split actually produced multiple files
            local hard_count
            hard_count=$(find "$harddir" -name 'part_*.txt' | wc -l | tr -d ' ')
            if [[ $hard_count -le 1 ]]; then
                # No blank lines at all — force split every N lines
                rm -f "$harddir"/part_*.txt
                local max_lines=$(( max_size / 80 ))  # ~80 chars/line estimate
                awk -v outdir="$harddir" -v maxlines="$max_lines" '
                BEGIN {
                    idx = 1
                    file = sprintf("%s/part_%03d.txt", outdir, idx)
                    lc = 0
                }
                {
                    lc++
                    if (lc > maxlines) {
                        idx++
                        file = sprintf("%s/part_%03d.txt", outdir, idx)
                        lc = 1
                    }
                    print >> file
                }
                ' "$subfile"
            fi

            rm "$subfile"
        fi
    done
}

# Build chunks by greedily grouping section files up to max size.
# First sub-splits any oversized sections, then groups small sections together.
# Writes chunk_001.txt, chunk_002.txt, ... to the given directory.
_build_chunks() {
    local secdir="$1"
    local chunkdir="$2"
    local max_size="$3"

    # Sub-split oversized sections
    for secfile in "$secdir"/sec_*.txt; do
        [[ -f "$secfile" ]] || continue
        _subsplit_large "$secfile" "$max_size"
    done

    # Collect all leaf files in order
    local -a all_files=()
    for secfile in "$secdir"/sec_*.txt; do
        [[ -f "$secfile" ]] || continue
        local parts_dir="${secfile%.txt}_parts"
        if [[ -d "$parts_dir" ]]; then
            # Use sub-parts instead of the original section
            for subfile in "$parts_dir"/sub_*.txt; do
                [[ -f "$subfile" ]] || continue
                local hard_dir="${subfile%.txt}_hard"
                if [[ -d "$hard_dir" ]]; then
                    for hardfile in "$hard_dir"/part_*.txt; do
                        [[ -f "$hardfile" ]] || continue
                        all_files+=("$hardfile")
                    done
                else
                    all_files+=("$subfile")
                fi
            done
        else
            all_files+=("$secfile")
        fi
    done

    # Greedy grouping
    local chunk_idx=1
    local chunk_file
    chunk_file=$(printf "%s/chunk_%03d.txt" "$chunkdir" "$chunk_idx")
    local chunk_chars=0

    if [[ ${#all_files[@]} -eq 0 ]]; then
        echo "[gemini-pdf] WARNING: No section files found, falling back to single chunk" >&2
        cp "$secdir"/../extracted.txt "$chunkdir/chunk_001.txt" 2>/dev/null || true
        return 0
    fi

    for leaf in ${all_files[@]+"${all_files[@]}"}; do
        local leaf_size
        leaf_size=$(wc -c < "$leaf" | tr -d ' ')

        if [[ $chunk_chars -gt 0 && $((chunk_chars + leaf_size)) -gt $max_size ]]; then
            # Start a new chunk
            chunk_idx=$((chunk_idx + 1))
            chunk_file=$(printf "%s/chunk_%03d.txt" "$chunkdir" "$chunk_idx")
            chunk_chars=0
        fi

        cat "$leaf" >> "$chunk_file"
        chunk_chars=$((chunk_chars + leaf_size))
    done
}

# --- Parse arguments ---
INPUT=""
OUTPUT=""
EXTRACTOR="markitdown"
NO_CHUNK=false
BIB_KEY=""
NO_FALLBACK=false
FORCE_MATHPIX=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extractor)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --extractor requires a value (markitdown or pdftotext)" >&2
                exit 1
            fi
            EXTRACTOR="$2"
            shift 2
            ;;
        --no-chunk)
            NO_CHUNK=true
            shift
            ;;
        --no-fallback)
            NO_FALLBACK=true
            shift
            ;;
        --force-mathpix)
            FORCE_MATHPIX=true
            shift
            ;;
        --quality-threshold)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --quality-threshold requires a numeric value" >&2
                exit 1
            fi
            QUALITY_THRESHOLD="$2"
            shift 2
            ;;
        --bib-key)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --bib-key requires a value (e.g., andrews-1999-ecma)" >&2
                exit 1
            fi
            BIB_KEY="$(basename "$2")"  # sanitize: strip path separators
            shift 2
            ;;
        -*)
            echo "ERROR: Unknown option: $1" >&2
            echo "Usage: pdf_to_markdown.sh <input.pdf> [output.md] [--extractor markitdown|pdftotext] [--no-chunk] [--bib-key KEY] [--no-fallback] [--force-mathpix] [--quality-threshold N]" >&2
            exit 1
            ;;
        *)
            if [[ -z "$INPUT" ]]; then
                INPUT="$1"
            elif [[ -z "$OUTPUT" ]]; then
                OUTPUT="$1"
            else
                echo "ERROR: Unexpected argument: $1" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    echo "Usage: pdf_to_markdown.sh <input.pdf> [output.md] [--extractor markitdown|pdftotext] [--no-chunk] [--bib-key KEY]" >&2
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "ERROR: Input file not found: $INPUT" >&2
    exit 1
fi

# Default output: same directory as input, .md extension
if [[ -z "$OUTPUT" ]]; then
    OUTPUT="${INPUT%.pdf}.md"
fi

ERR_LOG="${OUTPUT%.md}.err"

# --- Quality / Mathpix helper functions ---
_run_quality_check() {
    local md_file="$1" pdf_file="$2" threshold="$3"
    if [[ ! -f "$QUALITY_SCRIPT" ]]; then
        echo "[gemini-pdf] WARNING: quality_check.py not found, skipping quality verification" >&2
        return 0
    fi
    local result
    result=$(python3 "$QUALITY_SCRIPT" --md "$md_file" --source-pdf "$pdf_file" --threshold "$threshold" 2>/dev/null) || true
    echo "$result"
    local score
    score=$(echo "$result" | grep -oE '[0-9]+/100' | head -1 | cut -d/ -f1)
    if [[ -n "$score" && $score -ge $threshold ]]; then
        return 0
    else
        return 1
    fi
}

_has_mathpix_creds() {
    [[ -n "${MATHPIX_APP_ID:-}" && -n "${MATHPIX_APP_KEY:-}" ]]
}

_run_mathpix() {
    local pdf_file="$1" output_file="$2" mode="$3"
    if [[ ! -f "$MATHPIX_SCRIPT" ]]; then
        echo "[gemini-pdf] WARNING: mathpix_extract.py not found" >&2
        return 1
    fi
    python3 "$MATHPIX_SCRIPT" "$pdf_file" "$output_file" --mode "$mode" --verbose 2>&1 | sed 's/^/[gemini-pdf][mathpix] /' >&2
}

# --- Preflight checks ---
if ! command -v agy &>/dev/null; then
    echo "ERROR: Antigravity CLI (agy) not found." >&2
    echo "  Install: curl -fsSL https://antigravity.google/cli/install.sh | bash" >&2
    exit 1
fi

if ! command -v "$EXTRACTOR" &>/dev/null; then
    echo "ERROR: $EXTRACTOR not found." >&2
    if [[ "$EXTRACTOR" == "markitdown" ]]; then
        echo "  Install: pip install markitdown" >&2
    else
        echo "  Install: brew install poppler (provides pdftotext)" >&2
    fi
    exit 1
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "ERROR: Prompt template not found: $PROMPT_FILE" >&2
    exit 1
fi

# --- Force-Mathpix early exit (skip Gemini entirely) ---
if [[ "$FORCE_MATHPIX" == "true" ]]; then
    echo "[gemini-pdf] --force-mathpix: skipping Antigravity, using Mathpix directly" >&2
    START_TIME=$(date +%s)
    if ! _has_mathpix_creds; then
        echo "ERROR: --force-mathpix requires MATHPIX_APP_ID and MATHPIX_APP_KEY" >&2
        exit 1
    fi
    if ! _run_mathpix "$INPUT" "$OUTPUT" convert; then
        echo "ERROR: Mathpix conversion failed" >&2
        exit 1
    fi
    CONVERSION_PATH="mathpix-standalone"
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    OUTPUT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
    OUTPUT_LINES=$(wc -l < "$OUTPUT" | tr -d ' ')
    echo "[gemini-pdf] Done in ${ELAPSED}s (Mathpix standalone)" >&2
    echo "[gemini-pdf] Output: $OUTPUT_SIZE bytes, $OUTPUT_LINES lines → $OUTPUT" >&2
    # Quality check for reporting only
    if QC_RESULT=$(_run_quality_check "$OUTPUT" "$INPUT" "$QUALITY_THRESHOLD" 2>/dev/null); then
        echo "[gemini-pdf] Quality: $QC_RESULT" >&2
    else
        echo "[gemini-pdf] Quality: $QC_RESULT (informational)" >&2
    fi
    echo "[gemini-pdf] Conversion path: $CONVERSION_PATH" >&2
    # Jump to sync section — use exec trick: re-source just the sync block
    # (cleaner: just duplicate the sync + output lines)
    SYNC_SCRIPT="$SCRIPT_DIR/sync_to_reference.py"
    REFERENCE_DIR="${GEMINI_PDF_REFERENCE_DIR:-}"   # opt-in; sync no-ops when unset
    if [[ -f "$SYNC_SCRIPT" && -d "$REFERENCE_DIR" ]]; then
        if [[ -n "$BIB_KEY" ]]; then
            SYNC_ARGS=(--key "$BIB_KEY" --pdf "$INPUT" --md "$OUTPUT" --central-ref "$REFERENCE_DIR")
            SIDECAR="${INPUT%.pdf}.json"
            [[ -f "$SIDECAR" ]] && SYNC_ARGS+=(--sidecar "$SIDECAR")
            python3 "$SYNC_SCRIPT" "${SYNC_ARGS[@]}" 2>&1 | sed 's/^/[gemini-pdf] /' >&2 \
                || echo "[gemini-pdf] WARNING: sync to reference library failed" >&2
        else
            REFERENCE_MD_DIR="${REFERENCE_DIR}/md"
            REF_DEST="$REFERENCE_MD_DIR/$(basename "$OUTPUT")"
            if [[ -d "$REFERENCE_MD_DIR" && ! -f "$REF_DEST" ]]; then
                cp "$OUTPUT" "$REF_DEST" 2>/dev/null && echo "[gemini-pdf] Copied MD to $REF_DEST" >&2
            fi
        fi
    fi
    echo "$OUTPUT"
    exit 0
fi

# --- Extract text and check size ---
echo "[gemini-pdf] Extracting text with $EXTRACTOR..." >&2
START_TIME=$(date +%s)

EXTRACTED=$("$EXTRACTOR" "$INPUT" 2>/dev/null) || {
    echo "ERROR: $EXTRACTOR failed on $INPUT" >&2
    exit 1
}

CHAR_COUNT=${#EXTRACTED}
EST_TOKENS=$((CHAR_COUNT / 4))

echo "[gemini-pdf] Extracted: $CHAR_COUNT chars (~${EST_TOKENS} tokens)" >&2

if [[ $EST_TOKENS -gt $MAX_TOKENS ]]; then
    echo "ERROR: Document too large (~${EST_TOKENS} tokens, max ${MAX_TOKENS}). Consider splitting." >&2
    exit 1
fi

# --- Read prompts ---
PROMPT=$(cat "$PROMPT_FILE")
CONTINUATION_PROMPT=""
if [[ -f "$CONTINUATION_PROMPT_FILE" ]]; then
    CONTINUATION_PROMPT=$(cat "$CONTINUATION_PROMPT_FILE")
fi

# --- Decide: single-pass or chunked ---
if [[ "$NO_CHUNK" == "true" || $CHAR_COUNT -le $CHUNK_THRESHOLD ]]; then
    # === SINGLE-PASS (original behavior) ===
    if [[ $CHAR_COUNT -le $CHUNK_THRESHOLD ]]; then
        echo "[gemini-pdf] Document under threshold (${CHAR_COUNT} ≤ ${CHUNK_THRESHOLD}), single-pass mode" >&2
    else
        echo "[gemini-pdf] Chunking disabled (--no-chunk), single-pass mode" >&2
    fi

    echo "[gemini-pdf] Sending to Antigravity (model: $MODEL) with ${TIMEOUT}s timeout..." >&2

    # agy --print reads stdin and writes the response to stdout.
    # Model is set in ~/.gemini/antigravity-cli/settings.json (no --model flag in agy v1).
    # --print-timeout takes a Go duration (e.g., 600s = 10m).
    if echo "$EXTRACTED" | _timeout "$TIMEOUT" agy --print-timeout "${TIMEOUT}s" --print "$PROMPT" > "$OUTPUT" 2>"$ERR_LOG"; then
        END_TIME=$(date +%s)
        ELAPSED=$((END_TIME - START_TIME))
        OUTPUT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
        OUTPUT_LINES=$(wc -l < "$OUTPUT" | tr -d ' ')

        echo "[gemini-pdf] Done in ${ELAPSED}s" >&2
        echo "[gemini-pdf] Input:  $CHAR_COUNT chars ($(wc -c < "$INPUT" | tr -d ' ') bytes PDF)" >&2
        echo "[gemini-pdf] Output: $OUTPUT_SIZE bytes, $OUTPUT_LINES lines → $OUTPUT" >&2
        echo "[gemini-pdf] Errors: $ERR_LOG" >&2
    else
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 124 ]]; then
            echo "ERROR: Antigravity (agy) timed out after ${TIMEOUT}s. Try increasing GEMINI_PDF_TIMEOUT." >&2
        else
            echo "ERROR: Antigravity (agy) exited with code $EXIT_CODE. Check $ERR_LOG" >&2
        fi
        exit $EXIT_CODE
    fi
else
    # === CHUNKED PIPELINE ===
    echo "[gemini-pdf] Document exceeds threshold (${CHAR_COUNT} > ${CHUNK_THRESHOLD}), chunked mode" >&2
    echo "[gemini-pdf] Chunk max size: ${CHUNK_MAX_SIZE} chars" >&2

    # Create temp directory with cleanup trap
    TMPDIR_CHUNKS=$(mktemp -d "${TMPDIR:-/tmp}/gemini-pdf-chunks.XXXXXX")
    trap 'rm -rf "$TMPDIR_CHUNKS"' EXIT

    SECDIR="$TMPDIR_CHUNKS/sections"
    CHUNKDIR="$TMPDIR_CHUNKS/chunks"
    OUTDIR="$TMPDIR_CHUNKS/outputs"
    mkdir -p "$SECDIR" "$CHUNKDIR" "$OUTDIR"

    # Step 1: Split at section boundaries
    echo "[gemini-pdf] Splitting at section boundaries..." >&2
    echo "$EXTRACTED" | _split_at_sections "$SECDIR"

    SEC_COUNT=$(find "$SECDIR" -name 'sec_*.txt' | wc -l | tr -d ' ')
    echo "[gemini-pdf] Found $SEC_COUNT section(s)" >&2

    # Step 2: Build chunks (greedy grouping with sub-splitting)
    echo "[gemini-pdf] Building chunks (max ${CHUNK_MAX_SIZE} chars each)..." >&2
    _build_chunks "$SECDIR" "$CHUNKDIR" "$CHUNK_MAX_SIZE"

    CHUNK_COUNT=$(find "$CHUNKDIR" -name 'chunk_*.txt' | wc -l | tr -d ' ')
    echo "[gemini-pdf] Created $CHUNK_COUNT chunk(s)" >&2

    # Report chunk sizes
    for chunk_file in "$CHUNKDIR"/chunk_*.txt; do
        [[ -f "$chunk_file" ]] || continue
        local_size=$(wc -c < "$chunk_file" | tr -d ' ')
        echo "[gemini-pdf]   $(basename "$chunk_file"): ${local_size} chars" >&2
    done

    # Step 3: Process each chunk sequentially
    FAILED_CHUNKS=0
    TOTAL_OUTPUT_LINES=0
    CHUNK_NUM=0

    for chunk_file in "$CHUNKDIR"/chunk_*.txt; do
        [[ -f "$chunk_file" ]] || continue
        CHUNK_NUM=$((CHUNK_NUM + 1))
        CHUNK_SIZE=$(wc -c < "$chunk_file" | tr -d ' ')
        CHUNK_START=$(date +%s)

        local_out="$OUTDIR/out_$(printf '%03d' "$CHUNK_NUM").md"
        local_err="$OUTDIR/err_$(printf '%03d' "$CHUNK_NUM").log"

        # First chunk gets full prompt; subsequent chunks get continuation prompt
        if [[ $CHUNK_NUM -eq 1 ]]; then
            CURRENT_PROMPT="$PROMPT"
            echo "[gemini-pdf] Chunk $CHUNK_NUM/$CHUNK_COUNT (${CHUNK_SIZE} chars) — full prompt..." >&2
        else
            CURRENT_PROMPT="$CONTINUATION_PROMPT"
            echo "[gemini-pdf] Chunk $CHUNK_NUM/$CHUNK_COUNT (${CHUNK_SIZE} chars) — continuation prompt..." >&2
        fi

        if _timeout "$TIMEOUT" agy --print-timeout "${TIMEOUT}s" --print "$CURRENT_PROMPT" < "$chunk_file" > "$local_out" 2>"$local_err"; then
            CHUNK_END=$(date +%s)
            CHUNK_ELAPSED=$((CHUNK_END - CHUNK_START))
            CHUNK_OUT_LINES=$(wc -l < "$local_out" | tr -d ' ')
            CHUNK_OUT_SIZE=$(wc -c < "$local_out" | tr -d ' ')
            TOTAL_OUTPUT_LINES=$((TOTAL_OUTPUT_LINES + CHUNK_OUT_LINES))
            echo "[gemini-pdf]   ✓ Chunk $CHUNK_NUM done in ${CHUNK_ELAPSED}s (${CHUNK_OUT_SIZE} bytes, ${CHUNK_OUT_LINES} lines)" >&2
        else
            CHUNK_EXIT=$?
            CHUNK_END=$(date +%s)
            CHUNK_ELAPSED=$((CHUNK_END - CHUNK_START))
            FAILED_CHUNKS=$((FAILED_CHUNKS + 1))

            if [[ $CHUNK_EXIT -eq 124 ]]; then
                echo "[gemini-pdf]   ✗ Chunk $CHUNK_NUM timed out after ${CHUNK_ELAPSED}s" >&2
            else
                echo "[gemini-pdf]   ✗ Chunk $CHUNK_NUM failed (exit $CHUNK_EXIT) after ${CHUNK_ELAPSED}s" >&2
            fi

            # Write placeholder so we can continue
            printf '\n<!-- CHUNK %d FAILED (exit code %d) -->\n\n' "$CHUNK_NUM" "$CHUNK_EXIT" > "$local_out"
        fi
    done

    # Step 4: Concatenate all chunk outputs
    echo "[gemini-pdf] Concatenating $CHUNK_COUNT chunk outputs..." >&2
    : > "$OUTPUT"  # truncate output file
    FIRST=true
    for out_file in "$OUTDIR"/out_*.md; do
        [[ -f "$out_file" ]] || continue
        if [[ "$FIRST" == "true" ]]; then
            FIRST=false
        else
            printf '\n' >> "$OUTPUT"
        fi
        cat "$out_file" >> "$OUTPUT"
    done

    # Concatenate error logs
    : > "$ERR_LOG"
    for err_file in "$OUTDIR"/err_*.log; do
        [[ -f "$err_file" ]] || continue
        if [[ -s "$err_file" ]]; then
            echo "--- $(basename "$err_file") ---" >> "$ERR_LOG"
            cat "$err_file" >> "$ERR_LOG"
        fi
    done

    # Step 5: Report stats
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    OUTPUT_SIZE=$(wc -c < "$OUTPUT" | tr -d ' ')
    OUTPUT_LINES=$(wc -l < "$OUTPUT" | tr -d ' ')

    echo "[gemini-pdf] ─── Summary ───" >&2
    echo "[gemini-pdf] Total time: ${ELAPSED}s" >&2
    echo "[gemini-pdf] Chunks: $CHUNK_COUNT total, $((CHUNK_COUNT - FAILED_CHUNKS)) succeeded, $FAILED_CHUNKS failed" >&2
    echo "[gemini-pdf] Input:  $CHAR_COUNT chars ($(wc -c < "$INPUT" | tr -d ' ') bytes PDF)" >&2
    echo "[gemini-pdf] Output: $OUTPUT_SIZE bytes, $OUTPUT_LINES lines → $OUTPUT" >&2
    echo "[gemini-pdf] Errors: $ERR_LOG" >&2

    if [[ $FAILED_CHUNKS -eq $CHUNK_COUNT ]]; then
        echo "ERROR: All $CHUNK_COUNT chunks failed. Check $ERR_LOG" >&2
        exit 1
    elif [[ $FAILED_CHUNKS -gt 0 ]]; then
        echo "[gemini-pdf] WARNING: $FAILED_CHUNKS chunk(s) failed. Check output for <!-- CHUNK N FAILED --> placeholders." >&2
    fi
fi

# --- Quality verification + Mathpix fallback (post-Antigravity) ---
if [[ -f "$QUALITY_SCRIPT" ]]; then
    echo "[gemini-pdf] Running quality check (threshold: $QUALITY_THRESHOLD)..." >&2
    QC_RESULT=""
    if QC_RESULT=$(_run_quality_check "$OUTPUT" "$INPUT" "$QUALITY_THRESHOLD"); then
        echo "[gemini-pdf] Quality: $QC_RESULT" >&2
        CONVERSION_PATH="antigravity"
    else
        echo "[gemini-pdf] Quality: $QC_RESULT" >&2
        if [[ "$NO_FALLBACK" == "true" ]]; then
            echo "[gemini-pdf] --no-fallback: keeping Antigravity output despite low quality" >&2
            CONVERSION_PATH="antigravity (low-quality)"
        elif ! _has_mathpix_creds; then
            echo "[gemini-pdf] WARNING: Quality below threshold but MATHPIX credentials not set" >&2
            echo "[gemini-pdf] Set MATHPIX_APP_ID and MATHPIX_APP_KEY for automatic fallback" >&2
            CONVERSION_PATH="antigravity (low-quality, no fallback)"
        elif [[ -f "$MATHPIX_SCRIPT" ]]; then
            echo "[gemini-pdf] Attempting Mathpix fallback..." >&2
            MATHPIX_TMP="${OUTPUT%.md}.mathpix_tmp.md"
            if _run_mathpix "$INPUT" "$MATHPIX_TMP" convert; then
                # Check quality of Mathpix output
                if MQC=$(_run_quality_check "$MATHPIX_TMP" "$INPUT" "$QUALITY_THRESHOLD" 2>/dev/null); then
                    echo "[gemini-pdf] Mathpix quality: $MQC — using Mathpix output" >&2
                    mv "$MATHPIX_TMP" "$OUTPUT"
                    CONVERSION_PATH="mathpix-standalone"
                else
                    echo "[gemini-pdf] Mathpix quality: $MQC — also below threshold, using it anyway" >&2
                    mv "$MATHPIX_TMP" "$OUTPUT"
                    CONVERSION_PATH="mathpix-standalone (low-quality)"
                fi
            else
                echo "[gemini-pdf] Mathpix fallback failed, keeping Antigravity output" >&2
                rm -f "$MATHPIX_TMP"
                CONVERSION_PATH="antigravity (fallback-failed)"
            fi
        fi
    fi
fi

echo "[gemini-pdf] Conversion path: $CONVERSION_PATH" >&2

# --- Sync to central reference library (opt-in; no-ops when unset) ---
SYNC_SCRIPT="$SCRIPT_DIR/sync_to_reference.py"
REFERENCE_DIR="${GEMINI_PDF_REFERENCE_DIR:-}"
if [[ -f "$SYNC_SCRIPT" && -d "$REFERENCE_DIR" ]]; then
    if [[ -n "$BIB_KEY" ]]; then
        # Full sync: PDF + MD + BibTeX
        SYNC_ARGS=(--key "$BIB_KEY" --pdf "$INPUT" --md "$OUTPUT" --central-ref "$REFERENCE_DIR")
        SIDECAR="${INPUT%.pdf}.json"
        [[ -f "$SIDECAR" ]] && SYNC_ARGS+=(--sidecar "$SIDECAR")
        python3 "$SYNC_SCRIPT" "${SYNC_ARGS[@]}" 2>&1 | sed 's/^/[gemini-pdf] /' >&2 \
            || echo "[gemini-pdf] WARNING: sync to reference library failed" >&2
    else
        # MD-only copy (no key → can't name PDF or create bib entry)
        REFERENCE_MD_DIR="${REFERENCE_DIR}/md"
        REF_DEST="$REFERENCE_MD_DIR/$(basename "$OUTPUT")"
        if [[ -d "$REFERENCE_MD_DIR" && ! -f "$REF_DEST" ]]; then
            cp "$OUTPUT" "$REF_DEST" 2>/dev/null && echo "[gemini-pdf] Copied MD to $REF_DEST" >&2
        fi
    fi
fi

echo "$OUTPUT"
