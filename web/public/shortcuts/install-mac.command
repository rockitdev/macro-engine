#!/bin/bash
# Downloads the credential-free Shortcut, asks Apple's local command to sign
# it, then opens it for review/import. Does not run the Shortcut or read Health.
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Run this installer in Terminal on your Mac."
  exit 1
fi
setup_dir=$(mktemp -d "${TMPDIR:-/tmp}/macro-engine-shortcut.XXXXXX")
mkdir -p "$setup_dir/signed"
curl --fail --show-error --location \
  'https://moore.tailc24dca.ts.net:4325/shortcuts/Sync%20Macro%20Engine.unsigned.shortcut' \
  --output "$setup_dir/unsigned.shortcut"
/usr/bin/plutil -lint "$setup_dir/unsigned.shortcut"
echo "Signing the Shortcut with Apple, then opening it for import."
/usr/bin/shortcuts sign --mode anyone \
  --input "$setup_dir/unsigned.shortcut" \
  --output "$setup_dir/signed/Sync Macro Engine.shortcut"
/usr/bin/open "$setup_dir/signed/Sync Macro Engine.shortcut"
echo "Signed file: $setup_dir/signed/Sync Macro Engine.shortcut"
echo "Finish importing on the Mac. Run it on your iPhone after Shortcuts syncs."
