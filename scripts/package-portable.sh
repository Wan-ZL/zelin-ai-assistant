#!/bin/bash
# Package the cross-platform PORTABLE install bundles (Linux + Windows).
#
# Windows/Linux are pure-Python (no compiled binary), so the "installer" for
# each is a self-contained archive of the source files needed to run plus the
# platform install script. A friend downloads the archive from the GitHub
# Release, unpacks it, and runs install-linux.sh / install.ps1 from the
# extracted tree (both locate the repo root via their own path) — no git clone.
#
# Produces into dist/:
#   ZelinAIAssistant-<tag>-linux.tar.gz     (contains install-linux.sh)
#   ZelinAIAssistant-<tag>-windows.zip      (contains install.ps1)
# Both unpack under a single top-level dir  ZelinAIAssistant-<tag>/.
#
# No compilation. Deterministic file set. Uses only tar + zip (both present on
# macos-latest, where release.yml runs this). Runnable locally on macOS too.
#
# Usage:
#   bash scripts/package-portable.sh [tag]
# tag defaults to v<act.__version__> when omitted (e.g. v0.29.0).
set -euo pipefail

# Locate the repo root from this script's own path so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Tag: $1 verbatim, else v<version> read from the single source of truth.
TAG="${1:-}"
if [ -z "$TAG" ]; then
  VERSION="$(python3 -c 'import act; print(act.__version__)')"
  TAG="v${VERSION}"
fi

DIST="$REPO_ROOT/dist"
mkdir -p "$DIST"

# The file set every portable bundle needs to run the headless pipeline.
# EXCLUDES the Swift app (mac/ ios/ shared/), .git/, .github/, .claude/,
# tests/, supabase/ — none appear here.
COMMON=(
  act
  ingest
  webui
  config
  config.example.yaml
  requirements-cloud.txt
  README.md
  README.zh-CN.md
  LICENSE.md
  CHANGELOG.md
  uninstall.sh
  docs
)

# Remove build caches and any gitignored runtime/secret data that could be
# present in a live checkout (release.yml runs compileall before this, so
# __pycache__ WILL exist). Keeps the bundle lean and never ships secrets.
scrub() {
  local dir="$1"
  find "$dir" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$dir" -type d -name '.ruff_cache' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$dir" \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' -o -name '*.log' \) -delete 2>/dev/null || true
  # gitignored sensitive/runtime files that live under config/ (copied wholesale)
  rm -rf "$dir/config/secrets" "$dir/config/runtime.json" "$dir/config/redaction_terms.txt" "$dir/config.yaml" 2>/dev/null || true
  # runtime registry entries carry real extracted work data (only the example is tracked)
  if [ -d "$dir/act/registry" ]; then
    find "$dir/act/registry" -name 'R-*.yaml' ! -name 'R-000-example.yaml' -delete 2>/dev/null || true
  fi
}

# Build one bundle. $1 = platform label, $2 = extra platform script, $3 = format.
build_bundle() {
  local platform="$1" script="$2" fmt="$3"
  local parent stage
  parent="$(mktemp -d "$DIST/.stage-${platform}.XXXXXX")"
  stage="$parent/ZelinAIAssistant-${TAG}"
  mkdir -p "$stage"

  cp -R "${COMMON[@]}" "$stage"/
  cp "$script" "$stage"/
  scrub "$stage"

  case "$fmt" in
    tar.gz)
      local out="$DIST/ZelinAIAssistant-${TAG}-${platform}.tar.gz"
      rm -f "$out"
      # COPYFILE_DISABLE stops bsdtar from emitting macOS ._AppleDouble members.
      ( cd "$parent" && COPYFILE_DISABLE=1 tar -czf "$out" "ZelinAIAssistant-${TAG}" )
      echo "  built $out"
      ;;
    zip)
      local out="$DIST/ZelinAIAssistant-${TAG}-${platform}.zip"
      rm -f "$out"
      # -X drops extra file attributes; exclude any stray .DS_Store.
      ( cd "$parent" && zip -q -r -X "$out" "ZelinAIAssistant-${TAG}" -x '*.DS_Store' )
      echo "  built $out"
      ;;
    *)
      echo "::error::unknown format $fmt" >&2; exit 1 ;;
  esac

  rm -rf "$parent"
}

echo "Packaging portable bundles for ${TAG} into dist/ ..."
build_bundle linux   "$REPO_ROOT/install-linux.sh" tar.gz
build_bundle windows "$REPO_ROOT/install.ps1"      zip
echo "Done."
