#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build.sh — FQ App Full Build Pipeline
# ─────────────────────────────────────────────────────────────────────────────
# Steps:
#   1. Extract PDFs → data/extracted/*.json
#   2. Generate static browse interface → docs/browse.html
#   3. Git add + commit extracted JSON and browse HTML
#
# Usage:
#   bash pipeline/build.sh               # full build
#   bash pipeline/build.sh --skip-git    # build without committing
#   bash pipeline/build.sh --force       # force re-extract all PDFs
#
# Requirements:
#   pip install pymupdf
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE_DIR="$REPO_ROOT/pipeline"
PYTHON="${PYTHON:-python3}"
SKIP_GIT=false
FORCE=""

# ── Arg parse ──────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --skip-git) SKIP_GIT=true ;;
    --force)    FORCE="--force" ;;
    --help)
      echo "Usage: bash pipeline/build.sh [--skip-git] [--force]"
      exit 0
      ;;
  esac
done

# ── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}▶ $*${RESET}"; }
ok()   { echo -e "${GREEN}✓ $*${RESET}"; }
warn() { echo -e "${YELLOW}⚠ $*${RESET}"; }
die()  { echo -e "${RED}✗ $*${RESET}" >&2; exit 1; }

# ── Preflight ──────────────────────────────────────────────────────────────
echo -e "\n${BOLD}FQ App Build Pipeline${RESET}"
echo "─────────────────────────────────"
echo "Repo root : $REPO_ROOT"
echo "Python    : $($PYTHON --version 2>&1)"
echo ""

# Check pymupdf
if ! $PYTHON -c "import fitz" 2>/dev/null; then
  warn "pymupdf not found — installing..."
  $PYTHON -m pip install pymupdf --quiet || die "Failed to install pymupdf"
  ok "pymupdf installed"
fi

# Check PDFs exist
PDF_COUNT=$(ls "$REPO_ROOT/data/pdfs/"*.pdf 2>/dev/null | wc -l | tr -d ' ')
if [[ "$PDF_COUNT" -eq 0 ]]; then
  die "No PDFs found in data/pdfs/ — add PDFs before building"
fi
log "Found $PDF_COUNT PDF(s) in data/pdfs/"

# ── Step 1: Extract PDFs ───────────────────────────────────────────────────
log "Step 1/3 — Extracting PDFs → data/extracted/"
$PYTHON "$PIPELINE_DIR/extract_pdfs.py" $FORCE
ok "Extraction complete"

# ── Step 2: Generate browse interface ─────────────────────────────────────
log "Step 2/3 — Generating docs/browse.html"
$PYTHON "$PIPELINE_DIR/generate_browse.py"
ok "Browse interface generated"

# ── Step 3: Git commit ─────────────────────────────────────────────────────
if [[ "$SKIP_GIT" == "true" ]]; then
  warn "Step 3/3 — Git commit skipped (--skip-git)"
else
  log "Step 3/3 — Committing to Git"
  cd "$REPO_ROOT"

  # Stage extracted JSON, manifest, and browse HTML
  git add data/extracted/ docs/browse.html 2>/dev/null || true

  # Check if there's anything to commit
  if git diff --cached --quiet; then
    warn "Nothing new to commit (all files unchanged)"
  else
    TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")
    COMMIT_MSG="build: update extracted JSON + browse interface [${TIMESTAMP}]

Files changed:
$(git diff --cached --name-only 2>/dev/null | sed 's/^/  - /')"

    git commit -m "$COMMIT_MSG"
    ok "Committed → $(git rev-parse --short HEAD)"
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Build complete.${RESET}"
echo "  Browse interface : $REPO_ROOT/docs/browse.html"
echo "  Extracted JSON   : $REPO_ROOT/data/extracted/"
if [[ "$SKIP_GIT" == "false" ]]; then
  echo "  Git log          : $(cd "$REPO_ROOT" && git log --oneline -3 | head -3)"
fi
echo ""
