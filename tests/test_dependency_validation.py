import contextlib
import io
import subprocess
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import dependency_validation as dependencies


class DependencyValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.requirements = self.root / "requirements.txt"
        self.lock = self.root / "requirements-lock.txt"
        self.fingerprint = self.root / "runtime" / "dependency-fingerprint.txt"
        self.cache = self.root / "runtime" / "pip-cache"
        self.requirements.write_text("streamlit==1.66.1\n", encoding="utf-8")
        self.lock.write_text("streamlit==1.66.1\n", encoding="utf-8")
        self.installed_versions = {"streamlit": "1.66.1"}
        self.install_count = 0
        self.install_fails = False
        self.fail_next_import = False
        self.import_attempts = 0

        def installed_version(package_name):
            try:
                return self.installed_versions[package_name]
            except KeyError:
                raise PackageNotFoundError(package_name)

        def run_command(arguments, **kwargs):
            if arguments[3] == "check":
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
            if arguments[3] == "install":
                self.install_count += 1
                self.assertFalse(self.fingerprint.exists())
                if self.install_fails:
                    return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="synthetic failure")
                self.installed_versions["streamlit"] = "1.66.1"
                return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
            self.fail(f"Unexpected subprocess command: {arguments}")

        def import_module(name):
            self.import_attempts += 1
            if self.fail_next_import:
                self.fail_next_import = False
                raise ImportError("synthetic missing import")
            return object()

        self.version_patch = patch.object(dependencies, "version", side_effect=installed_version)
        self.import_patch = patch.object(dependencies.importlib, "import_module", side_effect=import_module)
        self.subprocess_patch = patch.object(dependencies.subprocess, "run", side_effect=run_command)
        self.version_patch.start()
        self.import_patch.start()
        self.subprocess_patch.start()
        self.addCleanup(self.temporary_directory.cleanup)
        self.addCleanup(self.subprocess_patch.stop)
        self.addCleanup(self.import_patch.stop)
        self.addCleanup(self.version_patch.stop)

    def run_ensure(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return dependencies.ensure_dependencies(
                self.requirements,
                self.lock,
                self.fingerprint,
                self.cache,
            )

    def test_healthy_startup_validates_and_skips_install(self):
        self.assertEqual(self.run_ensure(), 0)
        self.assertEqual(self.install_count, 0)
        self.assertTrue(self.fingerprint.is_file())

    def test_missing_and_incompatible_versions_are_repaired(self):
        for installed in (None, "1.65.0"):
            with self.subTest(installed=installed):
                self.installed_versions = {} if installed is None else {"streamlit": installed}
                self.install_count = 0
                self.fingerprint.unlink(missing_ok=True)
                self.assertEqual(self.run_ensure(), 0)
                self.assertEqual(self.install_count, 1)
                self.assertEqual(self.installed_versions["streamlit"], "1.66.1")
                self.assertTrue(self.fingerprint.is_file())

    def test_failed_install_keeps_startup_retryable(self):
        self.installed_versions = {}
        self.install_fails = True
        self.assertEqual(self.run_ensure(), 1)
        self.assertFalse(self.fingerprint.exists())

        self.install_fails = False
        self.assertEqual(self.run_ensure(), 0)
        self.assertEqual(self.install_count, 2)
        self.assertTrue(self.fingerprint.is_file())

    def test_import_failure_is_repaired_and_fingerprint_waits_for_validation(self):
        self.fail_next_import = True
        self.assertEqual(self.run_ensure(), 0)
        self.assertEqual(self.install_count, 1)
        self.assertGreaterEqual(self.import_attempts, 2)
        self.assertTrue(self.fingerprint.is_file())


if __name__ == "__main__":
    unittest.main()
