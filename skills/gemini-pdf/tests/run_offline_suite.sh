#!/bin/bash
# The first Python process ignores inherited Python configuration; all tests
# then run with a fresh environment, restricted tools, and recording stubs.
set -euo pipefail
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_PYTHON="${GEMINI_PDF_TEST_PYTHON:-$(command -v python3)}"
exec /usr/bin/env -i OFFLINE_TEST_PYTHONPATH="${OFFLINE_TEST_PYTHONPATH:-}" "$TEST_PYTHON" -I "$TEST_DIR/isolated_launcher.py" "$@"
