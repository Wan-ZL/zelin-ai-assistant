#!/bin/bash
# Export new screenpipe OCR + audio to Obsidian vault
# Used by both cron (hourly) and manual trigger
# Dedup via marker files tracking last exported IDs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
# Same home the daemon uses (CONTRACT §19); default to the checkout this
# script lives in so a clone outside ~/Projects still reads its own config.
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}"

# The daemon's interpreter from config/runtime.json, else PATH python3.
runtime_python() {
    local py
    py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
    printf '%s\n' "$py"
}

# Resolve vault paths through the config layer (sources.obsidian_*, P1-6).
# Any failure (no python, no act package, broken config) falls back to the
# legacy hardcoded path — this script runs from cron and must never break
# because a dependency is missing.
resolve_config_path() {  # $1 = config key, $2 = fallback path
    local py resolved
    py="$(runtime_python)"
    resolved=""
    if [ -n "$py" ]; then
        resolved="$(cd "$REPO_ROOT" 2>/dev/null && "$py" -m act.lib.config --print-path "$1" 2>/dev/null)"
    fi
    if [ -n "$resolved" ]; then
        printf '%s\n' "$resolved"
    else
        printf '%s\n' "$2"
    fi
}

DB="$HOME/.screenpipe/db.sqlite"
OUT_DIR="$(resolve_config_path obsidian_unprocessed "$HOME/Documents/Obsidian Vault/1 - unprocessed")"
VAULT_ROOT="$(dirname "$(resolve_config_path obsidian_raw "$HOME/Documents/Obsidian Vault/2 - raw")")"
MARKER_DIR="$HOME/.screenpipe/export_markers"

# VAULT MIRROR mode (claude TCC isolation — see ingest/vault-sync.sh): pull
# the vault into the repo-local mirror via the app-bundle courier, then write
# the export INTO THE MIRROR. The whole chain (this script, claude in
# process-screenpipe.sh, radar) works repo-local; only the courier — with the
# app's stable TCC identity — touches ~/Documents. Helper missing / grant
# missing / pull failure → legacy direct-vault mode, chain never breaks.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/vault-sync.sh"
VAULT_SYNC_MODE="direct"
if vault_sync_pull "$VAULT_ROOT" 2>/dev/null; then
    VAULT_SYNC_MODE="mirror"
    OUT_DIR="$VAULT_MIRROR/1 - unprocessed"
fi
mkdir -p "$(dirname "$VAULT_SYNC_MODE_FILE")" 2>/dev/null
printf '%s\n' "$VAULT_SYNC_MODE" > "$VAULT_SYNC_MODE_FILE" 2>/dev/null

# cron Full Disk Access probe (CONTRACT §25): under cron (AIASSISTANT_CRON=1,
# set by the install.sh §18 chain) record whether this process can actually
# read the export target. In mirror mode OUT_DIR is repo-local, so read_ok
# reflects the courier pull that just succeeded — still the honest "can the
# chain reach its data source" signal. Without FDA, cron writes nothing into
# ~/Documents and reports nothing — this file is the only honest signal the
# doctor and the app's dependency page can read. Written BEFORE any early
# exit below so a blocked run still leaves evidence. Never fails the chain.
if [ -n "${AIASSISTANT_CRON:-}" ]; then
    PROBE_DIR="$REPO_ROOT/state"
    if mkdir -p "$PROBE_DIR" 2>/dev/null; then
        # same operation the export itself needs: create-if-missing + read.
        # (Without the mkdir the very first cron run would report a missing
        # dir as "blocked" — a false alarm on fresh installs.)
        mkdir -p "$OUT_DIR" 2>/dev/null
        if ls "$OUT_DIR" >/dev/null 2>&1; then READ_OK=true; else READ_OK=false; fi
        printf '{"ts":"%s","protected_path":"%s","read_ok":%s}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$OUT_DIR" "$READ_OK" \
            > "$PROBE_DIR/cron_probe.json.tmp" 2>/dev/null \
            && mv -f "$PROBE_DIR/cron_probe.json.tmp" "$PROBE_DIR/cron_probe.json" 2>/dev/null
    fi
fi

mkdir -p "$OUT_DIR" "$MARKER_DIR"

# Read last exported IDs
LAST_FRAME=$(cat "$MARKER_DIR/last_frame_id" 2>/dev/null || echo 0)
LAST_AUDIO=$(cat "$MARKER_DIR/last_audio_id" 2>/dev/null || echo 0)

# Sensitive-app exclusion (P1-9): skip frames whose app/window matches
# config.yaml recording.ignored_apps (defaults include password managers —
# see DEFAULT_IGNORED_APPS in act/lib/config.py). The engine already refuses
# to CAPTURE these windows (--ignored-windows, mac/Sources/Recording.swift);
# this filter additionally covers frames stored before the exclusion took
# effect (engine started with older args / pre-existing db rows).
RUNTIME_PY="$(runtime_python)"
EXCLUDE_SQL=""
PY_OK=0
if [ -n "$RUNTIME_PY" ]; then
    if EXCLUDE_SQL=$(cd "$REPO_ROOT" && "$RUNTIME_PY" -c \
        'from act.lib.config import recording_exclusion_sql; print(recording_exclusion_sql())' 2>/dev/null); then
        PY_OK=1   # empty output is valid here: ignored_apps: [] = explicit opt-out
    fi
fi
if [ "$PY_OK" -ne 1 ]; then
    # python unavailable → built-in defaults (keep in sync with
    # DEFAULT_IGNORED_APPS in act/lib/config.py; drift-guarded by
    # tests/test_capture_exclusion.py)
    EXCLUDE_SQL=""
    for term in '1password' 'bitwarden' 'lastpass' 'keepassxc' 'keychain access' 'private browsing' 'incognito'; do
        EXCLUDE_SQL="$EXCLUDE_SQL AND lower(coalesce(f.app_name, '')) NOT LIKE '%$term%' AND lower(coalesce(f.window_name, '')) NOT LIKE '%$term%'"
    done
fi

# Query new screen-text entries from frames.full_text
# (full_text = accessibility_text + ocr_text merged by screenpipe;
#  querying just ocr_text misses most frames because modern screenpipe
#  prefers macOS accessibility API and skips OCR when a11y text is available)
OCR_DATA=$(sqlite3 "$DB" "
SELECT f.timestamp, f.app_name, f.window_name, replace(f.full_text, char(10), ' ')
FROM frames f
WHERE f.id > $LAST_FRAME
  AND f.full_text IS NOT NULL
  AND length(f.full_text) > 0
  $EXCLUDE_SQL
ORDER BY f.id ASC;
" 2>/dev/null)

# Query new audio entries
AUDIO_DATA=$(sqlite3 "$DB" "
SELECT ac.timestamp, a.transcription, a.device
FROM audio_transcriptions a
JOIN audio_chunks ac ON a.audio_chunk_id = ac.id
WHERE a.id > $LAST_AUDIO
ORDER BY a.id ASC;
" 2>/dev/null)

# Exit if nothing new
if [ -z "$OCR_DATA" ] && [ -z "$AUDIO_DATA" ]; then
    echo "No new data to export."
    exit 0
fi

# Generate filename with timestamp
NOW=$(TZ='America/Los_Angeles' date +%Y-%m-%d_%H-%M-%S)
OUT_FILE="$OUT_DIR/screenpipe_${NOW}.md"

{
    echo "# Screenpipe Capture — $NOW"
    echo ""

    # Audio section
    if [ -n "$AUDIO_DATA" ]; then
        echo "## Audio Transcriptions"
        echo ""
        echo "$AUDIO_DATA" | while IFS='|' read -r timestamp transcription device; do
            ts_clean=$(echo "$timestamp" | cut -d'.' -f1 | sed 's/Z$//')
            time_only=$(TZ='America/Los_Angeles' date -j -f "%Y-%m-%dT%H:%M:%S" "$ts_clean" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "${ts_clean/T/ }")
            echo "**[$time_only]** ($device)"
            echo "$transcription"
            echo ""
        done
        echo "---"
        echo ""
    fi

    # OCR section
    if [ -n "$OCR_DATA" ]; then
        echo "## Screen OCR"
        echo ""
        echo "$OCR_DATA" | while IFS='|' read -r timestamp app window text; do
            ts_clean=$(echo "$timestamp" | cut -d'.' -f1 | sed 's/Z$//')
            time_only=$(TZ='America/Los_Angeles' date -j -f "%Y-%m-%dT%H:%M:%S" "$ts_clean" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "${ts_clean/T/ }")
            label=""
            [ -n "$app" ] && label="$app"
            [ -n "$window" ] && label="$label — $window"
            [ -z "$label" ] && label="Unknown"
            echo "**[$time_only]** $label"
            echo "$text"
            echo ""
        done
    fi
} > "$OUT_FILE"

# Update markers
NEW_FRAME=$(sqlite3 "$DB" "SELECT MAX(id) FROM frames WHERE full_text IS NOT NULL AND length(full_text) > 0;" 2>/dev/null)
NEW_AUDIO=$(sqlite3 "$DB" "SELECT MAX(id) FROM audio_transcriptions;" 2>/dev/null)
[ -n "$NEW_FRAME" ] && [ "$NEW_FRAME" != "" ] && echo "$NEW_FRAME" > "$MARKER_DIR/last_frame_id"
[ -n "$NEW_AUDIO" ] && [ "$NEW_AUDIO" != "" ] && echo "$NEW_AUDIO" > "$MARKER_DIR/last_audio_id"

# Mirror-mode source safety (2026-07-14 13:30 incident hardening): until a
# push runs, a mirror-mode dump exists ONLY in the mirror — and the export
# markers above are already advanced, so losing it means no re-export. When
# no processing is in flight (a push now is clean — no half-written raw/wiki
# to leak), push immediately so the source dump lands in the real vault the
# moment it exists. With processing in flight the round's own final push
# carries it home instead.
if [ "$VAULT_SYNC_MODE" = "mirror" ] && ! vault_sync_processing_live; then
    vault_sync_push "$VAULT_ROOT" >/dev/null 2>&1 || true
fi

echo "Exported to: $OUT_FILE"
