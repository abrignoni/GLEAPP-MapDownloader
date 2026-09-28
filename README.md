# GLEAPP Map Downloader

Cuts an area out of the [Protomaps](https://protomaps.com) planet basemap and saves it as
one `.pmtiles` file, for use as an offline basemap in
[GLEAPP](https://github.com/abrignoni/GLEAPP) (Maps → Import) or in any viewer that reads
PMTiles. One file, pure Python, standard library only. It has a window, and a command line
for scripts.

Run it on a machine with internet access, then copy the file it writes to the offline
machine. GLEAPP itself never goes online; this tool is the part that does.

## Getting it

- **macOS**: the [latest release](https://github.com/abrignoni/GLEAPP-MapDownloader/releases/latest)
  has a disk image for Apple silicon and one for Intel Macs. Open it and drag
  **GLEAPP Map Downloader** to Applications. The app is signed with a Developer ID and
  notarised by Apple. For the command line, run the executable inside the app:
  `"/Applications/GLEAPP Map Downloader.app/Contents/MacOS/GLEAPP-MapDownloader" --help`.
- **Windows and Linux**: the same release has a single executable for each (x64 and
  arm64). Unzip and run. They are not code signed; the `README.txt` inside says what
  Windows will ask the first time.
- **Python**: `python3 mapdownloader.py`, Python 3.10 or later. The window needs Tk, which
  some Linux distributions package separately (`python3-tk` on Debian and Ubuntu).

## Using it

With no arguments it opens the window: pick an area (a named region or an exact box), a
detail level, and where to save; **Check size** reports the download size, **Download**
writes the file.

```
python mapdownloader.py --list-regions
python mapdownloader.py --region florida --maxzoom 12 --size-only
python mapdownloader.py --region florida --maxzoom 12 --out florida.pmtiles
python mapdownloader.py --bbox=-77.12,38.79,-76.90,38.99 --maxzoom 15 --out dc.pmtiles
python mapdownloader.py --build https://build.protomaps.com/20260928.pmtiles --region hawaii --out hawaii.pmtiles
```

`--maxzoom` sets the detail, 0 to 15: zoom 10 shows highways and towns, 13 neighborhoods,
15 every street and building. Without `--build` it uses the newest daily build that
Protomaps lists. An area too large for one file (more than 25 million tile positions, for
example North America at zoom 15) is refused before anything is downloaded, with a
suggestion to lower the zoom or shrink the area.

## How it works

The planet build is a single PMTiles v3 archive (138.4 GB for the build of 2026-09-28). The tool reads the
archive's header and directories with HTTP range requests, keeps the directory entries for
tiles inside the box up to the chosen zoom, and downloads only those tiles, with up to four
requests at a time. It writes them as a new PMTiles v3 archive: gzip directories, the
planet's own metadata (including its attribution), the box as the archive's bounds, and a
tile that repeats across the area (open water, for example) stored once. The file is written
as `<name>.part` and renamed only when complete, so a stopped or failed download leaves no
file that looks whole.

Only builds whose directories are gzip compressed are accepted; the Protomaps daily builds
are. Protomaps keeps the daily builds of the past week and the latest build of each patch version,
so a `--build` address stops working after that.

## Privacy

build.protomaps.com sees which parts of the planet you read, which is the area you ask for.
The window and the command line both say so. If the area itself is sensitive, cut a larger
area around it, or cut it on a machine that is not tied to the case.

## Map data license and attribution

The Protomaps basemap tilesets are, in Protomaps' words, "Produced Works of the
OpenStreetMap dataset" under the [Open Database License](https://www.openstreetmap.org/copyright),
and a map that shows them must visibly credit **© OpenStreetMap**. The file this tool writes
carries that credit in its metadata; GLEAPP shows "© OpenStreetMap contributors" on its maps.
If you show the map anywhere else, credit it there too. See the Protomaps
[licensing and attribution guidelines](https://github.com/protomaps/basemaps#licensing-and-attribution-guidelines)
and [data licenses](https://github.com/protomaps/basemaps/blob/main/LICENSE_DATA.md).

Protomaps asks that its downloads not be hotlinked (used as a live tile server). This tool
downloads an area once, into a file you keep. This project is not affiliated with Protomaps
or the OpenStreetMap Foundation.

The tool itself is MIT licensed (see `LICENSE`).

## Building

The release workflow builds everything on GitHub Actions: one executable each for Windows
and Linux, and for macOS an app bundle from `packaging/mapdownloader.spec`, signed,
notarised and packed into a disk image the way GLEAPP's is. The icons all come from
`packaging/logo.svg`; after changing it, run `python tools/make_icons.py` (it needs
`rsvg-convert`, Pillow, and `iconutil` on macOS) and commit what it rewrites.

## Testing

`python -m pytest tests` runs offline. The tests build a small planet in memory (every tile
from zoom 0 to 8, with an "ocean" half whose tiles share one content), serve it from
127.0.0.1 with byte ranges, download areas from it, and read every result back with an
independent PMTiles reader and tile numbering in `tests/synthetic_planet.py`. They check that
the file holds exactly the planet's tiles inside the box and nothing outside it, in both
directory layouts; that a stopped download leaves no file; that the window downloads the box
it shows, not one from an earlier size check; and the command line's refusals.

Checked against the real planet on 2026-09-28 (build `20260928.pmtiles`): Washington, DC to
zoom 15 (711 tiles, 27.2 MB) and Puerto Rico to zoom 12 (331 tiles) were byte-identical to
the planet, tile by tile, read through GLEAPP's own PMTiles reader.
