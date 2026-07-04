#!/bin/bash
# ask_codex_batch.sh — Dispatch multiple Codex tasks in parallel from a manifest
#
# Usage:
#   bash ask_codex_batch.sh manifest.tsv
#
# Manifest format (TSV, one task per line):
#   output_path<TAB>@prompt_file
#
# Inline prompts are NOT supported — all prompts must be written to files
# first and referenced with @. This avoids shell quoting issues.
#
# Environment variables:
#   BATCH_TIMEOUT    Overall timeout in seconds (default: 580)
#   CODEX_TIMEOUT    Per-task timeout (default: BATCH_TIMEOUT - 20)
#   CODEX_MODEL      Model to use (passed through to ask_codex.sh)
#
# Output:
#   stdout: "BATCH: N/M succeeded | task1:DONE task2:MODEL_FAILED ..."
#   Exit code: 0 if all succeeded, 1 if any failed

set -uo pipefail
# Note: no set -e — we need to handle individual task failures

MANIFEST="${1:-}"
BATCH_TIMEOUT="${BATCH_TIMEOUT:-580}"

if [[ -z "$MANIFEST" || ! -f "$MANIFEST" ]]; then
    echo "Usage: ask_codex_batch.sh manifest.tsv" >&2
    echo "Manifest format: output_path<TAB>@prompt_file (one per line)" >&2
    exit 1
fi

# Override CODEX_TIMEOUT to fit within batch timeout unless explicitly set
if [[ -z "${CODEX_TIMEOUT+x}" ]]; then
    export CODEX_TIMEOUT=$((BATCH_TIMEOUT - 20))
fi

# Resolve script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASK_CODEX="${SCRIPT_DIR}/ask_codex.sh"

if [[ ! -f "$ASK_CODEX" ]]; then
    echo "ERROR: ask_codex.sh not found at ${ASK_CODEX}" >&2
    exit 1
fi

# Portable realpath -m (macOS lacks -m flag)
_realpath_m() {
    local target="$1"
    local dir; dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd || echo "$(dirname "$target")")"
    echo "$dir/$(basename "$target")"
}

# Parse model-level status from output file
# Returns: DONE, MODEL_FAILED, UNPARSEABLE
_parse_model_status() {
    local file="$1"
    if [[ ! -f "$file" || ! -s "$file" ]]; then
        echo "UNPARSEABLE"
        return
    fi
    # Find first non-comment, non-blank line
    local first_line
    first_line=$(grep -v '^\s*$' "$file" | grep -v '^<!--' | head -1)

    case "$first_line" in
        "STATUS: DONE"*)    echo "DONE" ;;
        "STATUS: FAILED"*)  echo "MODEL_FAILED" ;;
        "STATUS: PARTIAL"*) echo "MODEL_FAILED" ;;
        "VERDICT: PASS"*)   echo "DONE" ;;
        "VERDICT: CORRECT"*) echo "DONE" ;;
        "VERDICT: ISSUES"*) echo "DONE" ;;
        "VERDICT: CRITICAL"*) echo "MODEL_FAILED" ;;
        "VERDICT: ERROR"*)  echo "MODEL_FAILED" ;;
        "VERDICT: GAP"*)    echo "MODEL_FAILED" ;;
        FAILED*)            echo "MODEL_FAILED" ;;
        *)                  echo "UNPARSEABLE" ;;
    esac
}

# Read manifest into arrays
declare -a TASK_NAMES=()
declare -a OUTPUT_PATHS=()
declare -a PROMPT_ARGS=()
declare -a PIDS=()

while IFS=$'\t' read -r out_path prompt_arg; do
    # Skip blank lines and comments
    [[ -z "$out_path" || "$out_path" == \#* ]] && continue

    out_path="$(_realpath_m "$out_path")"
    mkdir -p "$(dirname "$out_path")"

    # Extract task name from output filename (for summary)
    task_name="$(basename "$out_path" .md)"

    TASK_NAMES+=("$task_name")
    OUTPUT_PATHS+=("$out_path")
    PROMPT_ARGS+=("$prompt_arg")
done < "$MANIFEST"

N_TASKS=${#TASK_NAMES[@]}
if [[ $N_TASKS -eq 0 ]]; then
    echo "ERROR: No valid tasks found in manifest" >&2
    exit 1
fi

echo "[delegate/batch] Dispatching $N_TASKS tasks (timeout: ${BATCH_TIMEOUT}s, per-task: ${CODEX_TIMEOUT}s)..." >&2

# Cleanup on exit
trap 'kill $(jobs -p) 2>/dev/null; wait' EXIT INT TERM

# Launch all tasks in parallel
for i in "${!TASK_NAMES[@]}"; do
    echo "[delegate/batch]   ${TASK_NAMES[$i]} → ${OUTPUT_PATHS[$i]}" >&2
    bash "$ASK_CODEX" "${PROMPT_ARGS[$i]}" "${OUTPUT_PATHS[$i]}" &
    PIDS+=($!)
done

# Wait for all PIDs, capturing exit codes
declare -a EXIT_CODES=()
for i in "${!PIDS[@]}"; do
    wait "${PIDS[$i]}" 2>/dev/null
    EXIT_CODES+=($?)
done

# Determine final status for each task
declare -a FINAL_STATUS=()
SUCCEEDED=0

for i in "${!TASK_NAMES[@]}"; do
    exit_code=${EXIT_CODES[$i]}

    if [[ $exit_code -eq 124 ]]; then
        FINAL_STATUS+=("TIMEOUT")
    elif [[ $exit_code -ne 0 ]]; then
        FINAL_STATUS+=("RUNTIME_FAILED")
    else
        # Process exited OK — check model-level status
        model_status=$(_parse_model_status "${OUTPUT_PATHS[$i]}")
        FINAL_STATUS+=("$model_status")
    fi

    if [[ "${FINAL_STATUS[$i]}" == "DONE" ]]; then
        ((SUCCEEDED++))
    fi
done

# Build summary string
SUMMARY_PARTS=""
for i in "${!TASK_NAMES[@]}"; do
    SUMMARY_PARTS+=" ${TASK_NAMES[$i]}:${FINAL_STATUS[$i]}"
done

echo "" >&2
echo "[delegate/batch] Complete." >&2

# Print summary to stdout (this is what Claude Code reads)
echo "BATCH: ${SUCCEEDED}/${N_TASKS} succeeded |${SUMMARY_PARTS}"

# On failures, print head -3 of failed outputs to stderr for diagnosis
for i in "${!TASK_NAMES[@]}"; do
    if [[ "${FINAL_STATUS[$i]}" != "DONE" ]]; then
        echo "" >&2
        echo "[delegate/batch] FAILED: ${TASK_NAMES[$i]} (${FINAL_STATUS[$i]})" >&2
        if [[ -f "${OUTPUT_PATHS[$i]}" ]]; then
            head -3 "${OUTPUT_PATHS[$i]}" >&2
        else
            echo "  (no output file)" >&2
        fi
    fi
done

# Exit non-zero if any task failed
if [[ $SUCCEEDED -lt $N_TASKS ]]; then
    exit 1
fi
