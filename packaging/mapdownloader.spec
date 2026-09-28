# -*- mode: python ; coding: utf-8 -*-
# pylint: disable=undefined-variable
"""PyInstaller spec for the macOS app, GLEAPP Map Downloader.app.

Used by the build workflow on macOS only; Windows and Linux are built as one executable
from the command line. A one-folder build inside a bundle, as GLEAPP's is, because a
Developer ID signature and notarisation need every Mach-O signed in place, which a
one-file build that unpacks itself at run time cannot give.

    pyinstaller --noconfirm --clean packaging/mapdownloader.spec

The window opens with no terminal beside it (console=False). The command line still
works from a terminal, through the executable inside the bundle:

    "/Applications/GLEAPP Map Downloader.app/Contents/MacOS/GLEAPP-MapDownloader" --help
"""

import re
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SOURCE = ROOT / "mapdownloader.py"
ICNS = ROOT / "packaging" / "icon.icns"
VERSION = re.search(r'^__version__ = "([^"]+)"', SOURCE.read_text(encoding="utf-8"), re.M).group(1)

a = Analysis([str(SOURCE)], pathex=[str(ROOT)])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="GLEAPP-MapDownloader",
          console=False,
          icon=str(ICNS))
coll = COLLECT(exe, a.binaries, a.datas, name="GLEAPP-MapDownloader")
app = BUNDLE(coll,
             name="GLEAPP Map Downloader.app",
             icon=str(ICNS),
             bundle_identifier="org.leapp.gleapp.mapdownloader",
             info_plist={"CFBundleDisplayName": "GLEAPP Map Downloader",
                         "CFBundleShortVersionString": VERSION,
                         "CFBundleVersion": VERSION,
                         "NSHighResolutionCapable": True})
