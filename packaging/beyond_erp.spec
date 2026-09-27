# PyInstaller recipe for "Beyond ERP Offline" — the full app running on this computer (works without internet; can
# also join the shared workspace). Built by .github/workflows/deploy.yml:
#   pyinstaller packaging/beyond_erp.spec
# The result runs without Python installed: it starts the local server and opens the browser.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
mac = sys.platform == "darwin"

a = Analysis([str(ROOT / "run.py")], pathex=[str(ROOT)],
             datas=[(str(ROOT / "erp" / "static"), "erp/static")] +
                   ([(str(ROOT / "erp" / "workspace_preset.json"), "erp")] if (ROOT / "erp" / "workspace_preset.json").exists() else []),
             hiddenimports=collect_submodules("erp") + collect_submodules("openpyxl"),
             excludes=["tkinter", "PIL", "numpy", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BeyondERP", console=not mac,
          icon=str(ROOT / "packaging" / ("icon.png" if mac else "icon.ico")))
coll = COLLECT(exe, a.binaries, a.datas, name="BeyondERP")
if mac:
    app = BUNDLE(coll, name="Beyond ERP Offline.app", icon=str(ROOT / "packaging" / "icon.png"),
                 bundle_identifier="com.beyonddesign.erp.offline",
                 info_plist={"CFBundleShortVersionString": __import__("re").search(
                     r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1),
                     "LSUIElement": False})
