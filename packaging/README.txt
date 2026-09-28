GLEAPP Map Downloader, built as a standalone executable
=======================================================

  GLEAPP-MapDownloader   the tool (GLEAPP-MapDownloader.exe on Windows).
                         Double-click it for the window: pick an area and a
                         detail level, Check size, then Download. On Windows a
                         terminal window opens beside it; leave it open while
                         the tool runs.

                         Or run it from a terminal in this folder, for example:
                             GLEAPP-MapDownloader --list-regions
                             GLEAPP-MapDownloader --region florida --maxzoom 12 --size-only
                             GLEAPP-MapDownloader --region florida --maxzoom 12 --out florida.pmtiles
                             GLEAPP-MapDownloader --bbox=-77.12,38.79,-76.90,38.99 --maxzoom 15 --out dc.pmtiles
                         On Linux write ./GLEAPP-MapDownloader from this folder.

  SHA256SUMS.txt         the hash of the executable as built. Check it with
                             certutil -hashfile GLEAPP-MapDownloader.exe SHA256   (Windows)
                             sha256sum -c SHA256SUMS.txt                          (Linux)

Run it on a machine with internet access. It reads the Protomaps planet
basemap from build.protomaps.com, which sees the area you ask for, and writes
one .pmtiles file. Copy that file to the offline machine and import it in
GLEAPP (Maps -> Import). The map data is (c) OpenStreetMap contributors under
the ODbL; credit it wherever the map is shown.

It installs nothing and needs no administrator rights. It is built from
mapdownloader.py in https://github.com/abrignoni/GLEAPP-MapDownloader by the
repository's own GitHub Actions workflow, and is not code signed:

  Windows   SmartScreen may ask once before running it. Unzip to a local
            folder rather than running from a network share.
  Linux     mark the file executable if the archive did not keep the bit:
                chmod +x GLEAPP-MapDownloader

Builds: windows-x64, windows-arm64 (native for Windows on ARM; the x64 build
also runs there through emulation), linux-x64, linux-arm64. macOS is a signed,
notarised disk image instead: see the release page.
