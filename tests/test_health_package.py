"""Offline Mac bundle: credential handling, determinism, no private-server downloads."""
import importlib.util
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_health_shortcut", ROOT / "scripts/package_health_shortcut.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)

FAKE = b"Bearer test-only-not-a-real-token\n"


def names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename.split("/", 1)[1]: i for i in zf.infolist()}


class PackageTest(unittest.TestCase):
    def test_public_bundle_is_credential_free_and_deterministic(self):
        data = package.build_zip(None)
        self.assertEqual(data, package.build_zip(None))
        entries = names(data)
        self.assertEqual(set(entries), {"Sync Macro Engine.unsigned.shortcut",
                                        "Sync Macro Engine (without resting HR).unsigned.shortcut",
                                        "install-health-shortcut-mac.command", "START HERE.txt"})
        self.assertNotIn(b"test-only-not-a-real-token", data)
        mode = entries["install-health-shortcut-mac.command"].external_attr >> 16
        self.assertTrue(mode & stat.S_IXUSR)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.filename.endswith(".shortcut"):
                    body = zf.read(info).decode("latin-1")
                    self.assertFalse("Bearer test" in body)

    def test_private_bundle_carries_header_only_as_its_own_file(self):
        data = package.build_zip(FAKE)
        self.assertIn("Sync header.txt", names(data))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(zf.read("Macro Engine Shortcut/Sync header.txt"), FAKE)
            for info in zf.infolist():
                if not info.filename.endswith("Sync header.txt"):
                    self.assertNotIn(b"test-only-not-a-real-token", zf.read(info))

    def test_installer_never_downloads_or_touches_vpn(self):
        script = (ROOT / "scripts/install-health-shortcut-mac.command").read_text()
        script = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
        for word in ("curl", "wget", "http://", "https://", "tailscale", "warp-cli", "networksetup"):
            self.assertNotIn(word, script.lower())
        self.assertIn("/usr/bin/shortcuts sign", script)
        self.assertIn("--without-resting-hr", script)

    def test_written_bundle_is_owner_only_and_header_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "prev.zip"
            package.write_private(previous, package.build_zip(FAKE))
            self.assertEqual(package.existing_header(previous), FAKE)
            out = Path(tmp) / "out.zip"
            package.write_private(out, package.build_zip(package.existing_header(previous)))
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
            self.assertIsNone(package.existing_header(Path(tmp) / "missing.zip"))


if __name__ == "__main__":
    unittest.main()
