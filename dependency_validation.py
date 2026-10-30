from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


IMPORT_NAMES = {
    "pandas": "pandas",
    "requests": "requests",
    "streamlit": "streamlit",
    "tzdata": "tzdata",
}
PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)$")


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def _canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_pins(path: Path) -> tuple[dict[str, tuple[str, str]], list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, [f"Cannot read {path.name}: {exc}"]

    pins: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            errors.append(f"{path.name}:{line_number} must use an exact package==version pin")
            continue
        package_name, package_version = match.groups()
        canonical_name = _canonicalize_name(package_name)
        if canonical_name in pins:
            errors.append(f"{path.name}:{line_number} duplicates package {package_name}")
            continue
        pins[canonical_name] = (package_name, package_version)
    return pins, errors


def validate_dependencies(requirements_path: Path, lock_path: Path) -> ValidationResult:
    requirements, errors = _read_pins(requirements_path)
    locked, lock_errors = _read_pins(lock_path)
    errors.extend(lock_errors)

    for canonical_name, (package_name, package_version) in requirements.items():
        locked_package = locked.get(canonical_name)
        if locked_package is None:
            errors.append(f"{package_name} is declared in {requirements_path.name} but not pinned in {lock_path.name}")
        elif locked_package[1] != package_version:
            errors.append(
                f"{requirements_path.name} pins {package_name} {package_version}, "
                f"but {lock_path.name} pins {locked_package[1]}"
            )

    for canonical_name, (package_name, locked_version) in locked.items():
        try:
            installed_version = version(package_name)
        except PackageNotFoundError:
            errors.append(f"Locked package {package_name} is not installed")
            continue
        if installed_version != locked_version:
            errors.append(
                f"Installed {package_name} {installed_version} does not match lock {locked_version}"
            )

        import_name = IMPORT_NAMES.get(canonical_name)
        if import_name is not None:
            try:
                importlib.import_module(import_name)
            except (ImportError, OSError) as exc:
                errors.append(f"Required runtime import {import_name} failed: {exc}")

    try:
        check = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        errors.append(f"Unable to run pip check: {exc}")
    else:
        if check.returncode:
            details = (check.stdout + check.stderr).strip()
            errors.append(f"pip check found dependency conflicts: {details}")

    return ValidationResult(tuple(errors))


def _fingerprint(requirements_path: Path, lock_path: Path) -> str:
    digest = hashlib.sha256()
    for path in (requirements_path, lock_path):
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _save_fingerprint(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(f"{value}\n", encoding="ascii")
    temporary_path.replace(path)


def ensure_dependencies(
    requirements_path: Path,
    lock_path: Path,
    fingerprint_path: Path,
    cache_dir: Path,
) -> int:
    result = validate_dependencies(requirements_path, lock_path)
    try:
        expected_fingerprint = _fingerprint(requirements_path, lock_path)
    except OSError as exc:
        print(f"Could not fingerprint the dependency files: {exc}", file=sys.stderr)
        return 1
    if result.valid:
        if fingerprint_path.is_file():
            try:
                saved_fingerprint = fingerprint_path.read_text(encoding="ascii").strip()
            except OSError as exc:
                print(f"Could not read the dependency fingerprint: {exc}", file=sys.stderr)
                return 1
        else:
            saved_fingerprint = ""
        if saved_fingerprint == expected_fingerprint:
            print("Dependencies validate against the fingerprint and lock; skipping installation.")
        else:
            print("Dependencies validate against the lock; skipping installation.")
    else:
        for error in result.errors:
            print(f"Dependency validation failed: {error}", file=sys.stderr)
        print("Repairing dependencies from the pinned lock...", flush=True)
        try:
            fingerprint_path.unlink(missing_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            install_arguments = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--disable-pip-version-check",
                "--cache-dir",
                str(cache_dir),
                "--upgrade",
                "-r",
                str(lock_path),
            ]
            install = subprocess.run(install_arguments, check=False)
        except OSError as exc:
            print(f"Dependency installation could not start: {exc}", file=sys.stderr)
            return 1
        if install.returncode:
            print(
                f"Dependency installation failed with exit code {install.returncode}; it will be retried on the next launch.",
                file=sys.stderr,
            )
            return 1

        result = validate_dependencies(requirements_path, lock_path)
        if result.errors and all(error.startswith("Required runtime import ") for error in result.errors):
            print("A locked package is missing files; reinstalling the locked packages...", flush=True)
            install_arguments.insert(4, "--force-reinstall")
            try:
                install = subprocess.run(install_arguments, check=False)
            except OSError as exc:
                print(f"Dependency reinstallation could not start: {exc}", file=sys.stderr)
                return 1
            if install.returncode:
                print(
                    f"Dependency reinstallation failed with exit code {install.returncode}; it will be retried on the next launch.",
                    file=sys.stderr,
                )
                return 1
            result = validate_dependencies(requirements_path, lock_path)
        if not result.valid:
            for error in result.errors:
                print(f"Dependency validation failed after installation: {error}", file=sys.stderr)
            print("Dependencies remain invalid; installation will be retried on the next launch.", file=sys.stderr)
            return 1
        print("Dependencies installed and validated successfully.")

    try:
        _save_fingerprint(fingerprint_path, expected_fingerprint)
    except OSError as exc:
        print(f"Could not save the validated dependency fingerprint: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and repair the dashboard's locked dependencies.")
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    arguments = parser.parse_args()

    return ensure_dependencies(
        arguments.requirements,
        arguments.lock,
        arguments.fingerprint,
        arguments.cache_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
