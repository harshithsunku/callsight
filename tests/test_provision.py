"""Unit tests for callsight.provision: bundled-tool provisioning."""

import hashlib
import http.client
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import provision

# A fake "ctags" that survives the download smoke test (<path> --version
# must exit 0 and mention Ctags).
FAKE_CTAGS = b"#!/bin/sh\necho 'Universal Ctags fake 0.0'\n"


class FakeResponse:
    """Minimal urllib response: read() + context manager."""
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sha256_line(name, data):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


class HomeFixture(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ,
                                  {"CALLSIGHT_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bin = os.path.join(self.home, "bin")
        self.ctags = os.path.join(self.bin, "ctags")
        # ensure_ctags() caches download failures process-wide; reset it.
        provision._download_failed = False
        self.addCleanup(setattr, provision, "_download_failed", False)

    def write_fake_ctags(self, mode=0o755):
        os.makedirs(self.bin, exist_ok=True)
        with open(self.ctags, "wb") as f:
            f.write(FAKE_CTAGS)
        os.chmod(self.ctags, mode)


class TestFindCtags(HomeFixture):
    def test_path_hit_wins(self):
        self.write_fake_ctags()
        with mock.patch("shutil.which", return_value="/usr/bin/ctags"):
            self.assertEqual(provision.find_ctags(), "/usr/bin/ctags")

    def test_bundled_hit(self):
        self.write_fake_ctags()
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(provision.find_ctags(), self.ctags)

    def test_bundled_not_executable_is_ignored(self):
        self.write_fake_ctags(mode=0o644)
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(provision.find_ctags())

    def test_none_anywhere(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(provision.find_ctags())


class TestAssetName(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(provision.asset_name("x86_64"),
                         "callsight-ctags-linux-x86_64")
        self.assertEqual(provision.asset_name("aarch64"),
                         "callsight-ctags-linux-aarch64")

    def test_normalization(self):
        self.assertEqual(provision.asset_name("AMD64"),
                         "callsight-ctags-linux-x86_64")
        self.assertEqual(provision.asset_name("arm64"),
                         "callsight-ctags-linux-aarch64")

    def test_unknown_arch(self):
        self.assertIsNone(provision.asset_name("riscv64"))


class TestDownloadCtags(HomeFixture):
    def fake_urlopen(self, checksums=None, binary=FAKE_CTAGS):
        asset = provision.asset_name()
        payloads = {
            f"/{asset}": FakeResponse(binary),
            f"/{asset}.sha256": FakeResponse(
                sha256_line(asset, binary)
                if checksums is None else checksums),
        }

        def _urlopen(url, timeout=None):
            for suffix, resp in payloads.items():
                if url.endswith(suffix):
                    return resp
            raise OSError(f"unexpected URL {url}")
        return _urlopen

    def test_installs_verified_binary(self):
        with mock.patch("urllib.request.urlopen", self.fake_urlopen()):
            path = provision.download_ctags()
        self.assertEqual(path, self.ctags)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), FAKE_CTAGS)
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)
        # Atomic install: no temp files left behind in bin/.
        self.assertEqual(os.listdir(self.bin), ["ctags"])
        # Smoke test passed, so find_ctags() picks it up.
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(provision.find_ctags(), path)

    def test_checksum_mismatch(self):
        bad = sha256_line(provision.asset_name(), b"tampered")
        with mock.patch("urllib.request.urlopen",
                        self.fake_urlopen(checksums=bad)):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))

    def test_malformed_checksum_file(self):
        # No line for our asset at all.
        bad = b"deadbeef  callsight-ctags-linux-other\n"
        with mock.patch("urllib.request.urlopen",
                        self.fake_urlopen(checksums=bad)):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))

    def test_network_failure(self):
        def boom(url, timeout=None):
            raise OSError("no network")
        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))

    def test_truncated_download(self):
        # http.client.IncompleteRead is not an OSError; it must still
        # surface as the documented RuntimeError, not a traceback.
        class Truncated(FakeResponse):
            def read(self):
                raise http.client.IncompleteRead(b"partial")

        def _urlopen(url, timeout=None):
            return Truncated(b"")
        with mock.patch("urllib.request.urlopen", _urlopen):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))

    def test_install_oserror(self):
        with mock.patch("urllib.request.urlopen", self.fake_urlopen()), \
                mock.patch("os.makedirs",
                           side_effect=OSError("read-only fs")):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))

    def test_smoke_failure_removes_dest(self):
        # The downloaded "binary" runs but fails --version: it must be
        # unlinked again and reported as RuntimeError.
        bogus = b"#!/bin/sh\nexit 1\n"
        with mock.patch("urllib.request.urlopen",
                        self.fake_urlopen(binary=bogus)):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()
        self.assertFalse(os.path.exists(self.ctags))
        if os.path.isdir(self.bin):
            self.assertEqual(os.listdir(self.bin), [])

    def test_unsupported_arch(self):
        with mock.patch("callsight.provision.asset_name",
                        return_value=None):
            with self.assertRaises(RuntimeError):
                provision.download_ctags()


class TestEnsureCtags(HomeFixture):
    def test_returns_existing(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ctags"):
            self.assertEqual(provision.ensure_ctags(), "/usr/bin/ctags")

    def test_downloads_when_missing(self):
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("callsight.provision.download_ctags",
                           return_value=self.ctags):
            self.assertEqual(provision.ensure_ctags(), self.ctags)

    def test_download_failure_returns_none(self):
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("callsight.provision.download_ctags",
                           side_effect=RuntimeError("boom")):
            self.assertIsNone(provision.ensure_ctags())

    def test_download_failure_is_cached(self):
        # A failed download is remembered: later calls return None without
        # re-attempting (the UI calls ensure_ctags() on every scan).
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("callsight.provision.download_ctags",
                           side_effect=RuntimeError("boom")) as dl:
            self.assertIsNone(provision.ensure_ctags())
            self.assertIsNone(provision.ensure_ctags())
        self.assertEqual(dl.call_count, 1)


if __name__ == "__main__":
    unittest.main()
