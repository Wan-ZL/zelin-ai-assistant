#!/bin/bash
# make-signing-cert.sh — one-time, idempotent setup of the STABLE self-signed
# code-signing identity that mac/build.sh and .github/workflows/release.yml
# sign with.
#
# WHY THIS EXISTS
#   The app used to be ad-hoc signed ("-"), so macOS TCC (Screen Recording,
#   etc.) tied every grant to the ad-hoc cdhash — which changes on every build.
#   Result: users had to re-grant Screen Recording after every update. A STABLE
#   signing identity (even a free, self-signed one) keeps the app's Designated
#   Requirement constant across versions, so TCC grants PERSIST.
#
#   This is FREE — no Apple Developer Program membership. It is NOT notarized:
#   Gatekeeper still blocks the first open (right-click -> Open, once). It only
#   fixes the re-prompt-on-every-update problem, which is a SIGNING problem, not
#   an app-logic one.
#
# WHAT IT DOES (all local; nothing is pushed):
#   1. If the identity already exists in your keychain, stops (unless --force).
#   2. Creates a self-signed code-signing cert (OpenSSL) with the extensions
#      codesign requires.
#   3. Imports cert+key into your login keychain, authorized for /usr/bin/codesign
#      so local builds sign non-interactively. build.sh finds it automatically.
#   4. Exports a password-protected .p12 to ~/Downloads and prints the exact
#      `gh secret set` commands to wire CI. It NEVER writes the key/p12 into the
#      repo (a leaked key in git history is unrecoverable).
#
# Usage:
#   bash mac/scripts/make-signing-cert.sh          # create if missing (idempotent)
#   bash mac/scripts/make-signing-cert.sh --force  # recreate even if present
set -euo pipefail

IDENTITY="Zelin AI Engineer Dev"
DAYS=3650

# Outputs go to ~/Downloads per this repo's convention (never the repo). You
# delete both files after wiring CI — see NEXT STEPS printed at the end.
OUT_DIR="$HOME/Downloads"
P12_PATH="$OUT_DIR/ZelinSign.p12"
P12_B64_PATH="$OUT_DIR/ZelinSign.p12.base64"

FORCE=0
case "${1:-}" in
    "") ;;
    --force) FORCE=1 ;;
    *) echo "usage: $0 [--force]" >&2; exit 2 ;;
esac

# Prefer the system OpenSSL: macOS ships LibreSSL, whose default .p12 encryption
# `security import` can always read. A Homebrew OpenSSL 3.x default export uses a
# PBE that older macOS rejects; the system binary sidesteps that entirely.
OPENSSL="/usr/bin/openssl"
[ -x "$OPENSSL" ] || OPENSSL="$(command -v openssl)"

# Login keychain path — robust: modern ".keychain-db", else the reported default
# user keychain (older setups / non-standard names).
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
if [ ! -f "$LOGIN_KEYCHAIN" ]; then
    LOGIN_KEYCHAIN="$(security default-keychain -d user 2>/dev/null | sed -e 's/^[[:space:]]*"//' -e 's/"$//')"
fi

# --- idempotency guard ---
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
    if [ "$FORCE" -eq 0 ]; then
        echo "==> Identity '$IDENTITY' already exists in your keychain — nothing to do (idempotent)."
        echo "    build.sh already signs local builds with it."
        echo "    To (re)generate the .p12 for the CI secrets, re-run with --force."
        exit 0
    fi
    echo "==> --force: removing existing '$IDENTITY' copies before recreating"
    echo "    (avoids codesign 'ambiguous, matches multiple identities' errors)."
    # Best-effort: delete every cert with this common name so the name stays
    # unambiguous. Non-fatal if it can't (may leave a duplicate to clean up in
    # Keychain Access).
    while security find-certificate -c "$IDENTITY" >/dev/null 2>&1; do
        security delete-certificate -c "$IDENTITY" >/dev/null 2>&1 || break
    done
fi

# --- scratch dir for the private key + cert PEMs (shredded on exit) ---
WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

KEY_PEM="$WORK/key.pem"
CERT_PEM="$WORK/cert.pem"
CONF="$WORK/codesign.cnf"

# OpenSSL config with the extensions macOS codesign requires. keyUsage +
# extendedKeyUsage=codeSigning are what make this a *code-signing* cert; without
# them codesign refuses the identity.
cat > "$CONF" <<EOF
[ req ]
distinguished_name = dn
x509_extensions    = codesign_ext
prompt             = no

[ dn ]
CN = $IDENTITY

[ codesign_ext ]
basicConstraints     = critical,CA:false
keyUsage             = critical,digitalSignature
extendedKeyUsage     = critical,codeSigning
subjectKeyIdentifier = hash
EOF

echo "==> Generating self-signed code-signing cert ('$IDENTITY', ${DAYS} days)"
"$OPENSSL" req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY_PEM" -out "$CERT_PEM" \
    -days "$DAYS" -config "$CONF" 2>/dev/null
chmod 600 "$KEY_PEM"

# Random .p12 password, printed once so you can paste it into the CI secret.
# Strip /+= so it needs no shell/YAML escaping.
P12_PASSWORD="$("$OPENSSL" rand -base64 18 | tr -d '/+=' | cut -c1-24)"

echo "==> Exporting password-protected .p12 -> $P12_PATH"
mkdir -p "$OUT_DIR"
"$OPENSSL" pkcs12 -export \
    -inkey "$KEY_PEM" -in "$CERT_PEM" \
    -name "$IDENTITY" \
    -out "$P12_PATH" \
    -passout pass:"$P12_PASSWORD"
chmod 600 "$P12_PATH"

# --- import into the login keychain, authorized for codesign ---
echo "==> Importing cert+key into login keychain: $LOGIN_KEYCHAIN"
security import "$P12_PATH" -k "$LOGIN_KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign

# Let codesign use the key WITHOUT a GUI prompt on every build. This updates the
# key's ACL partition list, which needs your macOS LOGIN password — used locally
# only, never printed, never uploaded.
echo ""
echo "To let codesign use the key non-interactively, macOS must update the key's"
echo "partition list, which needs your macOS LOGIN password (local use only)."
printf "macOS login password (blank = skip; you'd click 'Always Allow' on first build): "
read -r -s LOGIN_PW || true
echo ""
if [ -n "${LOGIN_PW:-}" ]; then
    if security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
        -k "$LOGIN_PW" "$LOGIN_KEYCHAIN" >/dev/null 2>&1; then
        echo "    partition list updated — codesign can use the key non-interactively."
    else
        echo "    WARN: partition list update failed (wrong password?). Not fatal — on the"
        echo "          first codesign macOS prompts once; click 'Always Allow'."
    fi
else
    echo "    skipped — on first codesign macOS prompts once; click 'Always Allow'."
fi
LOGIN_PW=""

# --- verify the identity is now usable ---
echo ""
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$IDENTITY"; then
    echo "==> OK: '$IDENTITY' is now a usable code-signing identity."
else
    echo "ERROR: '$IDENTITY' not found after import — something went wrong." >&2
    exit 1
fi

# base64 for the CI secret (multiline is fine — CI decodes with `base64 --decode`).
base64 < "$P12_PATH" > "$P12_B64_PATH"
chmod 600 "$P12_B64_PATH"

cat <<STEPS

============================================================================
NEXT STEPS  (wire CI once, then delete the files)
============================================================================
Local builds are already signed: mac/build.sh finds '$IDENTITY'
automatically. The two GitHub secrets below make CI *release* builds sign the
same way, so tagged releases keep TCC grants across updates too.

  Secret 1 — MACOS_SIGN_CERT_P12       (base64 of the .p12)
  Secret 2 — MACOS_SIGN_CERT_PASSWORD  (the .p12 password, shown below)

  .p12 password:  ${P12_PASSWORD}

Add them with the GitHub CLI (run from the repo):

  gh secret set MACOS_SIGN_CERT_P12 < "${P12_B64_PATH}"
  gh secret set MACOS_SIGN_CERT_PASSWORD --body '${P12_PASSWORD}'

Then DELETE the local secret files (outside the repo, but still sensitive):

  rm -f "${P12_PATH}" "${P12_B64_PATH}"

The next release you tag will be signed with this stable identity. NOTE: the
FIRST stably-signed release re-prompts for Screen Recording ONCE (the identity
changes from ad-hoc to self-signed); after that, updates never re-prompt.
Gatekeeper first-open is unchanged (self-signed is not notarized).
============================================================================
STEPS
