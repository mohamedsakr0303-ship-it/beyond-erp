# PyInstaller recipe for "Beyond ERP" (online): a tiny launcher that opens the hosted app in its own window.
#   pyinstaller packaging/online.spec
import sys
from pathlib import Path

HERE = Path(SPECPATH)
mac = sys.platform == "darwin"
a = Analysis([str(HERE / "launcher.py")], pathex=[str(HERE.parent)], datas=[(str(HERE / "site_url.txt"), ".")],
             excludes=["tkinter", "PIL", "numpy", "openpyxl", "unittest", "pydoc", "email", "http", "xml"])
pyz = PYZ(a.pure)
if mac:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BeyondERP", console=False, icon=str(HERE / "icon.png"))
    coll = COLLECT(exe, a.binaries, a.datas, name="BeyondERP-online")
    app = BUNDLE(coll, name="Beyond ERP.app", icon=str(HERE / "icon.png"), bundle_identifier="com.beyonddesign.erp.online")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="BeyondERP", console=False, icon=str(HERE / "icon.ico"))
