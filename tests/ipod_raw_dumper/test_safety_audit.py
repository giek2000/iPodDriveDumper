import ast
from pathlib import Path

from iPodRawDriveDumper.build import audit_source


def test_read_only_source_audit_passes():
    audit_source()


def test_no_writer_project_imports_in_dumper_package():
    root = Path(__file__).parents[2]
    production_files = [path for path in root.rglob("*.py") if "tests" not in path.parts]
    imported = []
    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
    assert not any("ipod-mini-fix" in name or "iPodIPSWFlasher" in name or "AppPatcher" in name for name in imported)
    source = "\n".join(path.read_text(encoding="utf-8") for path in production_files)
    for token in ("GENERIC_WRITE", "WriteFile", "FSCTL_DISMOUNT_VOLUME", "IOCTL_DISK_UPDATE_PROPERTIES"):
        assert token not in source
