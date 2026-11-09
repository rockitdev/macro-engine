#!/bin/bash
# Signs a bundled, credential-free Shortcut and opens it for import.
# Reads only files in this folder; contacts Apple for signing. No Tailscale,
# no Macro Engine server, and no WARP changes are needed on this Mac.
#   /bin/bash install-health-shortcut-mac.command                    # standard
#   /bin/bash install-health-shortcut-mac.command --without-resting-hr
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Run this installer in Terminal on your Mac."
  exit 1
fi
variant="Sync Macro Engine.unsigned.shortcut"
case "${1:-}" in
  "") ;;
  --without-resting-hr) variant="Sync Macro Engine (without resting HR).unsigned.shortcut" ;;
  *) echo "Unknown option: $1 (use --without-resting-hr or nothing)"; exit 1 ;;
esac
bundle_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shortcut_input="$bundle_dir/$variant"
if [[ ! -f "$shortcut_input" ]]; then
  echo "Keep the installer and the unsigned Shortcut files together in the extracted folder."
  exit 1
fi
setup_dir=$(mktemp -d "${TMPDIR:-/tmp}/macro-engine-shortcut.XXXXXX")
/usr/bin/plutil -lint "$shortcut_input"
echo "Signing $variant with Apple, then opening it for import."
# The signed file keeps the name Sync Macro Engine so it can replace the
# existing Shortcut and the RENPHO automation keeps pointing at it.
/usr/bin/shortcuts sign --mode anyone \
  --input "$shortcut_input" \
  --output "$setup_dir/Sync Macro Engine.shortcut"
/usr/bin/open "$setup_dir/Sync Macro Engine.shortcut"
echo "Signed file: $setup_dir/Sync Macro Engine.shortcut"
echo "Choose Replace if Shortcuts asks. When asked for the authorization header,"
echo "paste the whole line from Sync header.txt."
echo "Finish importing on the Mac. Run it on your iPhone after Shortcuts syncs."
