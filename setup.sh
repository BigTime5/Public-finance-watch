#!/usr/bin/env bash
# ══════════════════════════════════════════════════════
#  Kenya Public Finance Intelligence — Quick Start
#  Works on: Windows Git Bash, macOS, Linux
# ══════════════════════════════════════════════════════

set -e

echo ""
echo " Kenya Public Finance Intelligence Platform"
echo " ==========================================="
echo ""

# ── Check Python version ──────────────────────────────
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found."
    echo "  Activate your env first:  conda activate fresh-env"
    exit 1
fi
echo "[✓] Python: $($PYTHON --version)"

# ── Install dependencies ──────────────────────────────
echo ""
echo "[1/3] Installing dependencies..."
$PYTHON -m pip install -r requirements.txt --quiet
echo "[✓] Dependencies installed"

# ── Init DB ───────────────────────────────────────────
echo ""
echo "[2/3] Initialising database..."
$PYTHON main.py --init-only
echo "[✓] Database ready"

# ── Run ───────────────────────────────────────────────
echo ""
echo "[3/3] Starting scraper (quick mode — index only, no downloads)..."
echo "      To run a full scrape instead, Ctrl+C and run:"
echo "      python main.py --ppra-years 2024 2025 --export"
echo ""
$PYTHON main.py --sources ppra knbs oag cob treasury \
    --no-download --no-anomalies

echo ""
echo "[✓] Done. Run the analysis:"
echo "    python analyze.py --summary-only"
echo ""
