"""Build and audit the standalone read-only Windows executable."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_FILE = ROOT / "version.py"
FORBIDDEN_TEXT = ("GENERIC_" + "WRITE", "Write" + "File", "FSCTL_DISMOUNT_" + "VOLUME", "IOCTL_DISK_UPDATE_" + "PROPERTIES")
FORBIDDEN_IMPORTS = {"ipod-mini-fix", "iPodIPSWFlasher", "AppPatcher"}


def audit_source() -> None:
    for path in ROOT.rglob("*.py"):
        if path.name == "build.py" or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TEXT:
            if token in source:
                raise SystemExit(f"read-only source audit failed: {token} in {path.name}")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(any(bad in name for bad in FORBIDDEN_IMPORTS) for name in names):
                raise SystemExit(f"writer-oriented import rejected in {path.name}")


def _read_version_text() -> str:
    return VERSION_FILE.read_text(encoding="utf-8")


def _next_patch_version(source: str) -> tuple[str, str]:
    match = re.search(r'VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', source)
    if not match:
        raise RuntimeError("version.py does not contain a semantic VERSION")
    major, minor, patch = (int(value) for value in match.groups())
    old = f"{major}.{minor}.{patch}"
    new = f"{major}.{minor}.{patch + 1}"
    updated = source[:match.start()] + f'VERSION = "{new}"' + source[match.end():]
    return old, updated


def build() -> None:
    audit_source()
    original = _read_version_text()
    old_version, updated = _next_patch_version(original)
    VERSION_FILE.write_text(updated, encoding="utf-8")
    version_match = re.search(r'VERSION\s*=\s*"([^"]+)"', updated)
    version = version_match.group(1) if version_match else old_version
    try:
        command = [sys.executable, "-m", "PyInstaller", "--clean", str(ROOT / "ipod_raw_dumper.spec")]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            VERSION_FILE.write_text(original, encoding="utf-8")
            raise SystemExit(result.returncode)
    except Exception:
        if VERSION_FILE.read_text(encoding="utf-8") == updated:
            VERSION_FILE.write_text(original, encoding="utf-8")
        raise
    print(f"Built {ROOT / 'dist' / f'IpodRawDriveDumper_v{version}.exe'}")


if __name__ == "__main__":
    build()
