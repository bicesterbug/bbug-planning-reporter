#!/usr/bin/env bash
# Fetch seed policy documents for initial deployment.
# Downloads all PDFs referenced in data/policy/seed_config.json
# into data/policy/seed/
#
# Usage: ./scripts/fetch_policies.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SEED_DIR="$PROJECT_ROOT/data/policy/seed"

mkdir -p "$SEED_DIR"

echo "=== Fetching seed policy documents ==="
echo "Target directory: $SEED_DIR"
echo ""

download() {
    local url="$1"
    local filename="$2"
    local description="$3"
    local target="$SEED_DIR/$filename"

    if [ -f "$target" ]; then
        echo "[SKIP] $description (already exists: $filename)"
        return 0
    fi

    echo "[DOWNLOAD] $description"
    echo "  URL: $url"
    echo "  File: $filename"

    if curl -fSL --progress-bar -o "$target" "$url"; then
        local size
        size=$(du -h "$target" | cut -f1)
        echo "  Done ($size)"
    else
        echo "  FAILED - removing partial download"
        rm -f "$target"
        return 1
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# LTN 1/20 - Cycle Infrastructure Design (July 2020)
# ---------------------------------------------------------------------------
download \
    "https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/951074/cycle-infrastructure-design-ltn-1-20.pdf" \
    "ltn_1_20.pdf" \
    "LTN 1/20 - Cycle Infrastructure Design (July 2020)"

# ---------------------------------------------------------------------------
# NPPF - National Planning Policy Framework (December 2024)
# ---------------------------------------------------------------------------
download \
    "https://assets.publishing.service.gov.uk/media/67aafe8f3b41f783cca46251/NPPF_December_2024.pdf" \
    "nppf_2024_12.pdf" \
    "NPPF - National Planning Policy Framework (December 2024)"

# ---------------------------------------------------------------------------
# NPPF - National Planning Policy Framework (September 2023)
# ---------------------------------------------------------------------------
download \
    "https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/1182995/NPPF_Sept_23.pdf" \
    "nppf_2023_09.pdf" \
    "NPPF - National Planning Policy Framework (September 2023)"

# ---------------------------------------------------------------------------
# Manual for Streets (March 2007)
# ---------------------------------------------------------------------------
download \
    "https://assets.publishing.service.gov.uk/media/5a7e0035ed915d74e6223743/pdfmanforstreets.pdf" \
    "manual_for_streets.pdf" \
    "Manual for Streets (March 2007)"

# ---------------------------------------------------------------------------
# Cherwell Local Plan 2011-2031 Part 1 (Adopted July 2015)
# ---------------------------------------------------------------------------
download \
    "https://www.cherwell.gov.uk/download/downloads/id/8144/final-adopted-local-plan-2011-2031-incorporating-re-adopted-policy-bicester-13.pdf" \
    "cherwell_local_plan_2015.pdf" \
    "Cherwell Local Plan 2011-2031 Part 1 (Adopted July 2015)"

# ---------------------------------------------------------------------------
# Oxfordshire Local Transport and Connectivity Plan (July 2022)
# ---------------------------------------------------------------------------
download \
    "https://www.southandvale.gov.uk/app/uploads/2024/12/LNP10-Oxfordshire-Local-Transport-and-Connectivity-Plan-LTCP.pdf" \
    "occ_ltcp_2022.pdf" \
    "Oxfordshire Local Transport and Connectivity Plan (July 2022)"

# ---------------------------------------------------------------------------
# Bicester Local Cycling and Walking Infrastructure Plan (2020)
# ---------------------------------------------------------------------------
download \
    "https://www.oxfordshire.gov.uk/sites/default/files/file/roads-and-transport-connecting-oxfordshire/Bicester_LCWIP_2020.pdf" \
    "bicester_lcwip.pdf" \
    "Bicester LCWIP (2020)"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=== Download summary ==="
echo "Files in $SEED_DIR:"
ls -lh "$SEED_DIR"/*.pdf 2>/dev/null || echo "  (no PDFs found)"
echo ""

EXPECTED=7
ACTUAL=$(ls "$SEED_DIR"/*.pdf 2>/dev/null | wc -l)
if [ "$ACTUAL" -eq "$EXPECTED" ]; then
    echo "All $EXPECTED policy documents downloaded successfully."
else
    echo "WARNING: Expected $EXPECTED files, found $ACTUAL."
    exit 1
fi
