# PyInstaller spec for the standalone read-only dumper.
# The GUI auto-elevates at runtime (see app.py); the executable is NOT marked
# requireAdministrator so CLI subcommands keep working stdout in the caller's console.
from pathlib import Path

block_cipher = None
root = Path(SPECPATH)
version_scope = {}
exec((root / "version.py").read_text(encoding="utf-8"), version_scope)
app_name = version_scope["APP_NAME"]
version = version_scope["VERSION"]

analysis = Analysis(
    [str(root / "app.py")],
    pathex=[str(root.parent)],
    binaries=[], datas=[(str(root / "dumper.png"), ".")],
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["tkinter"],
    cipher=block_cipher,
)
pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name=f"{app_name}_v{version}", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "dumper.ico"),
)
