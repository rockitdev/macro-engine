"""Build the offline Mac installation ZIP for Sync Macro Engine.

    python3 scripts/package_health_shortcut.py            # private bundle
    python3 scripts/package_health_shortcut.py --public   # credential-free bundle

Both contain the unsigned Shortcuts (regenerated from the builder), the local
installer, and START HERE.txt. The private bundle also carries the existing
`Sync header.txt`, copied byte-for-byte from the previous private ZIP (or
--header-file); it is never printed, never regenerated, and the token is not
rotated. Output is deterministic (fixed timestamps/order) and mode 0600.
Both default outputs are gitignored.
"""

import argparse
import importlib.util
import io
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "Macro Engine Shortcut"
HEADER = "Sync header.txt"
PRIVATE_ZIP = ROOT / "macro-engine-shortcut-mac.zip"
PUBLIC_ZIP = ROOT / "macro-engine-shortcut-mac-public.zip"
ORIGIN = "https://moore.tailc24dca.ts.net:4325"
STAMP = (1980, 1, 1, 0, 0, 0)

START_HERE = """Install Sync Macro Engine on your Mac

Keep Cloudflare WARP on. This installer does not use Tailscale and does not
contact Macro Engine; it only asks Apple to sign the bundled file.

1. Extract this ZIP and leave its files together.
2. Open Terminal. Type /bin/bash followed by a space, drag
   install-health-shortcut-mac.command into Terminal, then press Return.
3. Shortcuts opens the signed file. Choose Replace for the existing
   "Sync Macro Engine" so your RENPHO automation keeps working.
   When asked for the authorization header, paste the whole line
   from {header_hint}.
4. Wait for iCloud to sync the Shortcut to your iPhone.
5. On the iPhone: connect Tailscale, unlock, and run Sync Macro Engine.
   Allow the new Health categories (Steps, Resting Heart Rate) and
   network access when asked.
6. Open Macro Engine > Progress > Activity. If it asks, choose your
   Apple Watch as the step source (once).

If the run stops at the Resting Heart Rate step, reinstall with:
   /bin/bash install-health-shortcut-mac.command --without-resting-hr

Automations (on the iPhone, Shortcuts > Automation):
- Keep: App > RENPHO > Is Closed > Run Immediately > Sync Macro Engine.
- Add: Time of Day > 9:00 PM > Daily > Run Immediately > Sync Macro Engine,
  so activity uploads even on days you don't weigh in. The next run also
  refreshes yesterday's late Watch data. Re-runs are always safe.
Health can only be read while the phone is unlocked, and Tailscale must
be connected; if an automation fails, open the Shortcut and run it.

Workouts: Apple Shortcuts cannot read workouts from Health. Add them in
Progress > Activity > Add workout. This bundle has not been tested on
an Apple device yet; see docs/apple-health-capabilities.md.
"""


def _builder():
    spec = importlib.util.spec_from_file_location("build_health_shortcut", ROOT / "scripts/build_health_shortcut.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(zf, name, data, mode=0o600):
    info = zipfile.ZipInfo(f"{FOLDER}/{name}", STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16
    zf.writestr(info, data)


def existing_header(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    with zipfile.ZipFile(path) as zf:
        try:
            return zf.read(f"{FOLDER}/{HEADER}")
        except KeyError:
            return None


def build_zip(header: bytes | None, origin: str = ORIGIN) -> bytes:
    builder = _builder()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, options in builder.VARIANTS.items():
            _entry(zf, name, builder.serialize(builder.build(origin, **options)))
        installer = (ROOT / "scripts/install-health-shortcut-mac.command").read_bytes()
        _entry(zf, "install-health-shortcut-mac.command", installer, 0o700)
        hint = HEADER if header is not None else "the header you were given privately"
        _entry(zf, "START HERE.txt", START_HERE.format(header_hint=hint).encode())
        if header is not None:
            _entry(zf, HEADER, header)
    return buf.getvalue()


def write_private(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--public", action="store_true", help="omit the authorization header")
    parser.add_argument("--header-file", type=Path, help="private header file (default: previous private ZIP)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--origin", default=ORIGIN)
    args = parser.parse_args(argv)
    header = None
    if not args.public:
        header = args.header_file.read_bytes() if args.header_file else existing_header(PRIVATE_ZIP)
        if header is None:
            parser.error("no Sync header.txt found in the previous private ZIP; pass --header-file or --public")
    output = args.output or (PUBLIC_ZIP if args.public else PRIVATE_ZIP)
    write_private(output, build_zip(header, args.origin))
    kind = "credential-free" if header is None else "private (contains Sync header.txt; keep it private)"
    print(f"Wrote {kind} bundle: {output}")


if __name__ == "__main__":
    main()
