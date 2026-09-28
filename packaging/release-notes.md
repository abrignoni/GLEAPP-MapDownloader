GLEAPP-MapDownloader {{VERSION}}: standalone executables of `mapdownloader.py`, built by this repository's own GitHub Actions workflow from the tagged commit.

- `GLEAPP-MapDownloader-{{VERSION}}-windows-x64.zip` runs on Intel and AMD PCs, and on Windows on ARM through its x64 emulation.
- `GLEAPP-MapDownloader-{{VERSION}}-windows-arm64.zip` is native for Windows on ARM.
- `GLEAPP-MapDownloader-{{VERSION}}-macos-arm64.zip` for Apple silicon Macs, `GLEAPP-MapDownloader-{{VERSION}}-macos-x64.zip` for Intel Macs.
- `GLEAPP-MapDownloader-{{VERSION}}-linux-x64.tar.gz` and `GLEAPP-MapDownloader-{{VERSION}}-linux-arm64.tar.gz`, built on Ubuntu 22.04 so they run on distributions with a glibc at least that old.
- `mapdownloader.py`, the tagged source file itself.

Each archive holds the tool, `README.txt`, `LICENSE` and `SHA256SUMS.txt`. `SHA256SUMS.txt` beside the archives covers the archives and `mapdownloader.py`. Before it was attached, each executable downloaded an area from a synthetic planet served on the build machine and wrote the same bytes as `python mapdownloader.py`, and its window stayed open when started with no arguments.

None of the executables is code signed: Windows SmartScreen and macOS Gatekeeper will each ask once, and the README inside says what to do. Unzip to a local folder rather than running from a network share.

`mapdownloader.py` needs nothing built: `python3 mapdownloader.py` works on macOS, Linux and Windows with Python 3.10 or later and the standard library alone (the window needs Tk, which some Linux distributions package separately). See the README for what it downloads and the license of the map data.
