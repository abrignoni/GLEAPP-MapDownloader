#!/usr/bin/env python3
"""GLEAPP Map Downloader: cut an area out of the Protomaps planet basemap into one .pmtiles file.

Runs on a machine with internet access. Copy the file it writes to the offline machine
and import it in GLEAPP (Maps -> Import). GLEAPP itself never goes online.

The planet build is a PMTiles v3 archive on build.protomaps.com. This reads its
directories with HTTP range requests, keeps the tiles that fall inside the chosen box
up to the chosen zoom, downloads only those, and writes them as a new PMTiles v3
archive. Python standard library only; no pmtiles program is needed.

    python mapdownloader.py                       the window (or run the executable)
    python mapdownloader.py --list-regions
    python mapdownloader.py --region north-america --maxzoom 10 --out na.pmtiles
    python mapdownloader.py --bbox=-77.12,38.79,-76.90,38.99 --maxzoom 15 --out dc.pmtiles
    python mapdownloader.py --region florida --maxzoom 12 --size-only
    python mapdownloader.py --version

Map data (c) OpenStreetMap contributors, ODbL; basemap build by Protomaps.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

__version__ = "0.2.0"

BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
USER_AGENT = f"GLEAPP-MapDownloader/{__version__}"
ATTRIBUTION = "Map data (c) OpenStreetMap contributors (ODbL). Basemap build: Protomaps."

MAX_LAT = 85.0511287798
ROOT_LIMIT = 16384 - 127          # the header and root directory share the first 16 KiB
MERGE_GAP = 256 * 1024            # read two ranges as one when this close
MAX_REQUEST = 8 * 1024 * 1024     # but never more than this in one request
WORKERS = 4                       # the pmtiles CLI's own default for extract

# Approximate boxes (W, S, E, N). "Exact box" in the window, or --bbox, takes any other.
REGIONS = {
    "world":            ("World",                      (-180.0, -85.0, 180.0, 85.0)),
    "north-america":    ("North America",              (-170.0, 7.0, -50.0, 84.0)),
    "central-america":  ("Central America",            (-92.5, 7.0, -77.0, 18.6)),
    "caribbean":        ("Caribbean",                  (-85.5, 10.0, -59.0, 27.5)),
    "south-america":    ("South America",              (-82.0, -56.5, -34.0, 13.5)),
    "europe":           ("Europe",                     (-25.0, 34.0, 45.0, 72.0)),
    "africa":           ("Africa",                     (-26.0, -35.5, 52.0, 38.0)),
    "asia":             ("Asia",                       (25.0, -11.0, 180.0, 82.0)),
    "oceania":          ("Australia and Oceania",      (110.0, -48.0, 180.0, 0.0)),
    "usa-lower48":      ("United States (lower 48)",   (-125.0, 24.4, -66.9, 49.4)),
    "alaska":           ("Alaska",                     (-170.0, 51.2, -129.9, 71.5)),
    "hawaii":           ("Hawaii",                     (-160.3, 18.9, -154.8, 22.3)),
    "puerto-rico":      ("Puerto Rico",                (-67.30, 17.85, -65.20, 18.55)),
    "canada":           ("Canada",                     (-141.0, 41.7, -52.6, 83.2)),
    "mexico":           ("Mexico",                     (-118.4, 14.5, -86.7, 32.7)),
    "florida":          ("Florida",                    (-87.7, 24.4, -80.0, 31.0)),
    "texas":            ("Texas",                      (-106.7, 25.8, -93.5, 36.5)),
    "california":       ("California",                 (-124.5, 32.5, -114.1, 42.0)),
    "new-york":         ("New York",                   (-79.8, 40.5, -71.8, 45.0)),
    "washington-dc":    ("Washington, DC area",        (-77.12, 38.79, -76.90, 38.99)),
    "united-kingdom":   ("United Kingdom and Ireland", (-10.7, 49.8, 1.9, 60.9)),
}

DETAIL = {
    8: "Zoom 8: countries, major highways, cities",
    10: "Zoom 10: highways, towns",
    12: "Zoom 12: main roads",
    13: "Zoom 13: neighborhoods",
    14: "Zoom 14: most streets",
    15: "Zoom 15: every street and building",
}

# The logo, a folded map with a pin in GLEAPP's colors: ICON_64 and ICON_32 for the window
# and taskbar, LOGO_96 for the window header. Rendered from packaging/logo.svg by
# tools/make_icons.py, which rewrites these three blocks; do not edit them by hand.
ICON_64 = (
    "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"
)
ICON_32 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADnUlEQVR42u2WX0xbZRjGf985pcW2oaFTOjL+CG5BQiDQ"
    "jug2I0YYy6IdDiXGsMxkJhq9mNmMzjtjskUTMJm7IMag82LGbDNZjGwyGBHYgpKQiG4JzrABZWxjHf9b1/b09PMCqTRb"
    "R2GQecFz9Z0v5/3e5zzv+z3nhVWs4iFDJPLS9iPSpGqUT41eK5ia9l5VAqKt95uSycUkctfJHCmokYLrp98Tx+b2DfEC"
    "KuukxajyPFAjNHZIsIUj4Q4hRZk0oRe/1dsriZxLQm9rc340bVHu5CHxq4Zwp9hz3gtQ9ZnM1CXVUlAjYTMgBHw8P08M"
    "gR2HpUPXqQZ2CihDYgSQd/NTJdK1zjTuOpR77IBVuRPVUw8btO4vPvz5kP9gig5PIRD3k1mZ/xDReVtAg4CtMJs8HmyG"
    "v/kyr4Eiy1BsTQVJG9Xuyt3JjU8nUmJlqc2zy9FOunECgJa+aV75aoC9J4fx+sKzahq/x6HcWPAcw1IJPGPrA2BkUuON"
    "bz2E9NlCzQQlR3dloaJTovbQHHGvjAJWNQCAZyIUTQ7Q7w1E1xbFx4qVYEyzAuDMNJPvMEX3a0vt0fVExL58JVAElOQm"
    "W7v+ACnhov9xiqxDmAyCM++sp+3yDOm2JDZmmaMxl/WCByeQlgKVhVCcBaNjKRl7yq00No/S6imm1tEBgNmo4C60xcQN"
    "6rmMRDLuOs9sQl2QgEEFZzaU5YPVBK2X4Ltf4NbIrT+N0ud4fWsaBdvK6W8/zXq1/57ET4VejbFbZw5UOWFwjKIT8QgU"
    "ZmAvzYX8ddB9BRrbYcIfe/CkL8znp66Tbjeyc8NrPBE+iBCxVnUlsoEOrQIhoDQHqlwwdBuOtMDtGX6Lq0DmGmw9g3C0"
    "857uF4Mb4yEaulNJzd7Ey491/WdmQpFfa/uF2yl47knoGYBPfgRfIIEeOPM7A4u9DYevudli62Ptv6Z00fzC+O7teWta"
    "L8EHxyGsr9A1nINfN/GppxqA4eCj1N+s/evA8dm+WSj5AznhfHROFvDrVB4nvZu5aTaGzNbEY5eFAEDd8EsMBtOwmxcX"
    "t2wEBgKOJcUpPGQkqoAOdCmqsQkIgngWZPJSelaCJ1ECASQXgCZN5cTZfWLu516/aV/XIwFf8paIQoVAqZBI532Gj6tC"
    "cE5Ck26g5ae9Ihh3KH2xXr6LgktIfghGaG55X/gT+SzXmz1ZGmqlIthmX5ttsKSkagjOSmhu2i9GVmf/Vfyv8Q/HGTle"
    "VS9RBgAAAABJRU5ErkJggg=="
)
LOGO_96 = (
    "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"
)


class Cancelled(Exception):
    pass


# --- tile ids (PMTiles v3: zoom levels in order, Hilbert curve within a level) ---------
def _base(z: int) -> int:
    return ((1 << (2 * z)) - 1) // 3          # tiles in all lower zoom levels


def _rotate(n: int, x: int, y: int, rx: int, ry: int):
    if ry == 0:
        if rx == 1:
            x, y = n - 1 - x, n - 1 - y
        x, y = y, x
    return x, y


def zxy_to_id(z: int, x: int, y: int) -> int:
    n = 1 << z
    d, s = 0, n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        x, y = _rotate(n, x, y, rx, ry)
        s >>= 1
    return _base(z) + d


def _d_to_xy(z: int, d: int):
    n = 1 << z
    x = y = 0
    s, t = 1, d
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        x, y = _rotate(s, x, y, rx, ry)
        x += s * rx
        y += s * ry
        t //= 4
        s <<= 1
    return x, y


def id_to_zxy(tid: int):
    z = 0
    while _base(z + 1) <= tid:
        z += 1
    x, y = _d_to_xy(z, tid - _base(z))
    return z, x, y


def tile_rect(bbox, z: int):
    """Inclusive tile x/y ranges covering bbox at zoom z."""
    w, s, e, n = bbox
    size = 1 << z

    def tx(lon):
        return min(size - 1, max(0, int((lon + 180.0) / 360.0 * size)))

    def ty(lat):
        lat = max(-MAX_LAT, min(MAX_LAT, lat))
        r = math.radians(lat)
        return min(size - 1, max(0, int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * size)))
    return tx(w), ty(n), tx(e), ty(s)


def _range_hits(lo: int, hi: int, rects: dict, maxz: int) -> bool:
    """True when some tile id in [lo, hi) lies inside the box at a zoom up to maxz."""
    for z in range(0, maxz + 1):
        a, b = max(lo, _base(z)), min(hi, _base(z + 1))
        if a >= b:
            continue
        if _block_hits(z, a - _base(z), b - _base(z), 0, z, rects[z]):
            return True
    return False


def _block_hits(z, lo, hi, start, k, rect) -> bool:
    """Hilbert block [start, start + 4**k) of zoom z, which covers an aligned 2**k
    square: does it hold an id in [lo, hi) inside rect?"""
    size = 1 << (2 * k)
    if start >= hi or start + size <= lo:
        return False
    x, y = _d_to_xy(z, start)
    side = 1 << k
    bx, by = (x >> k) << k, (y >> k) << k
    x0, y0, x1, y1 = rect
    if bx > x1 or bx + side - 1 < x0 or by > y1 or by + side - 1 < y0:
        return False
    if (lo <= start and start + size <= hi and x0 <= bx and bx + side - 1 <= x1
            and y0 <= by and by + side - 1 <= y1):
        return True
    if k == 0:
        return True
    q = size >> 2
    return any(_block_hits(z, lo, hi, start + i * q, k - 1, rect) for i in range(4))


# --- directories ------------------------------------------------------------------------
def _uvarint(buf: bytes, pos: int):
    v = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, pos
        shift += 7


def _put_uvarint(out: bytearray, v: int) -> None:
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _decompress(raw: bytes, comp: int) -> bytes:
    if comp in (0, 1):
        return raw
    if comp == 2:
        return gzip.decompress(raw)
    raise ValueError(f"unsupported directory compression {comp}")


def decode_dir(raw: bytes, comp: int):
    buf = _decompress(raw, comp)
    n, pos = _uvarint(buf, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        d, pos = _uvarint(buf, pos)
        last += d
        ids.append(last)
    for _ in range(n):
        v, pos = _uvarint(buf, pos)
        runs.append(v)
    for _ in range(n):
        v, pos = _uvarint(buf, pos)
        lens.append(v)
    for i in range(n):
        v, pos = _uvarint(buf, pos)
        offs.append(offs[i - 1] + lens[i - 1] if v == 0 and i > 0 else v - 1)
    return list(zip(ids, offs, lens, runs))


def encode_dir(entries) -> bytes:
    out = bytearray()
    _put_uvarint(out, len(entries))
    last = 0
    for tid, _, _, _ in entries:
        _put_uvarint(out, tid - last)
        last = tid
    for e in entries:
        _put_uvarint(out, e[3])
    for e in entries:
        _put_uvarint(out, e[2])
    for i, (_, off, ln, _) in enumerate(entries):
        prev = entries[i - 1] if i else None
        _put_uvarint(out, 0 if prev and off == prev[1] + prev[2] else off + 1)
    return gzip.compress(bytes(out), mtime=0)


# --- the remote planet -----------------------------------------------------------------
class Remote:
    def __init__(self, url: str, cancel: threading.Event | None = None):
        self.url = url
        self.cancel = cancel or threading.Event()
        self.bytes_read = 0
        self._lock = threading.Lock()

    def read(self, start: int, length: int) -> bytes:
        for attempt in range(5):
            if self.cancel.is_set():
                raise Cancelled()
            req = urllib.request.Request(self.url, headers={
                "Range": f"bytes={start}-{start + length - 1}", "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    if r.status != 206:
                        raise IOError(f"server ignored the byte range (HTTP {r.status})")
                    data = r.read()
                if len(data) != length:
                    raise IOError(f"short read: {len(data)} of {length} bytes")
                with self._lock:
                    self.bytes_read += length
                return data
            except (urllib.error.URLError, IOError, TimeoutError) as exc:
                if attempt == 4:
                    raise IOError(f"could not read {self.url}: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
        raise AssertionError("unreachable")


def latest_build() -> str:
    req = urllib.request.Request(BUILDS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        builds = json.load(r)
    keys = sorted(b["key"] for b in builds if b.get("key", "").endswith(".pmtiles"))
    if not keys:
        raise IOError("no builds listed at " + BUILDS_URL)
    return BUILD_BASE + keys[-1]


def read_header(buf: bytes) -> dict:
    if buf[:7] != b"PMTiles" or buf[7] != 3:
        raise ValueError("not a PMTiles version 3 archive")
    f = struct.unpack("<11Q", buf[8:96])
    keys = ("root_off", "root_len", "meta_off", "meta_len", "leaf_off", "leaf_len",
            "data_off", "data_len", "addressed", "entries", "contents")
    h = dict(zip(keys, f))
    h.update(clustered=buf[96], internal=buf[97], tile_comp=buf[98], tile_type=buf[99],
             minz=buf[100], maxz=buf[101])
    return h


def _merged(ranges):
    """(start, length) ranges sorted by start -> requests (start, length, [ranges])."""
    out = []
    for s, ln in ranges:
        if out and s - (out[-1][0] + out[-1][1]) <= MERGE_GAP and \
                s + ln - out[-1][0] <= MAX_REQUEST:
            cur = out[-1]
            cur[1] = max(cur[1], s + ln - cur[0])
            cur[2].append((s, ln))
        else:
            out.append([s, ln, [(s, ln)]])
    return out


# --- planning ---------------------------------------------------------------------------
# One file past these sizes is not practical: sizing North America to zoom 15 (215 million
# tile positions) held 7 GB of memory and was nowhere near done, and the file would be
# tens of gigabytes. Past WARN_POSITIONS the window asks first; past MAX_POSITIONS the
# tool refuses and suggests a smaller area or a lower zoom.
WARN_POSITIONS = 2_000_000
MAX_POSITIONS = 25_000_000


def positions(bbox, maxz: int) -> int:
    """Tile positions inside the box at zoom 0..maxz: arithmetic only, no network."""
    total = 0
    for z in range(maxz + 1):
        x0, y0, x1, y1 = tile_rect(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def too_big_message(bbox, maxz: int) -> str | None:
    n = positions(bbox, maxz)
    if n <= MAX_POSITIONS:
        return None
    return (f"This area at zoom {maxz} covers {n:,} tile positions "
            f"({100 * n / _base(16):.1f}% of the planet), too large for one file. "
            f"Pick a lower zoom, or a smaller area for street-level detail (a state or a "
            f"metro area at zoom 15, a country at zoom 12 or 13).")


def _clip(lo: int, hi: int, rects: dict, maxz: int, out: list) -> None:
    """Append the (start, end) id intervals of [lo, hi) that lie inside the box."""
    for z in range(0, maxz + 1):
        a, b = max(lo, _base(z)), min(hi, _base(z + 1))
        if a < b:
            _clip_block(z, a - _base(z), b - _base(z), 0, z, rects[z], _base(z), out)


def _clip_block(z, lo, hi, start, k, rect, base, out) -> None:
    size = 1 << (2 * k)
    if start >= hi or start + size <= lo:
        return
    x, y = _d_to_xy(z, start)
    side = 1 << k
    bx, by = (x >> k) << k, (y >> k) << k
    x0, y0, x1, y1 = rect
    if bx > x1 or bx + side - 1 < x0 or by > y1 or by + side - 1 < y0:
        return
    if k == 0 or (x0 <= bx and bx + side - 1 <= x1 and y0 <= by and by + side - 1 <= y1):
        a, b = max(lo, start), min(hi, start + size)
        if out and out[-1][1] == base + a:
            out[-1] = (out[-1][0], base + b)
        else:
            out.append((base + a, base + b))
        return
    q = size >> 2
    for i in range(4):
        _clip_block(z, lo, hi, start + i * q, k - 1, rect, base, out)


class Plan:
    """Which tiles an area and zoom need, and how big the result will be.

    Kept as compact arrays: segments of consecutive tile ids that share one tile's
    contents (seg_id, seg_n, seg_off, seg_len), and the distinct contents sorted by
    their offset in the planet (u_off, u_len)."""

    def __init__(self, url, bbox, maxz, *, cancel=None, status=None):
        import array
        self.url, self.bbox = url, bbox
        self.remote = Remote(url, cancel)
        self.cancel = self.remote.cancel
        self.status = status or (lambda msg: None)
        msg = too_big_message(bbox, maxz)
        if msg:
            raise ValueError(msg)
        head = self.remote.read(0, 16384)
        self.h = read_header(head)
        if self.h["internal"] != 2:       # the directories this writes are always gzip
            raise ValueError(f"unsupported internal compression {self.h['internal']} "
                             "(only gzip builds are supported)")
        self.maxz = min(maxz, self.h["maxz"])
        self.rects = {z: tile_rect(bbox, z) for z in range(self.maxz + 1)}
        self.top = _base(self.maxz + 1)
        root = decode_dir(head[self.h["root_off"]:self.h["root_off"] + self.h["root_len"]],
                          self.h["internal"])
        self.meta_raw = self.remote.read(self.h["meta_off"], self.h["meta_len"])
        self.seg_id, self.seg_n = array.array("Q"), array.array("Q")
        self.seg_off, self.seg_len = array.array("Q"), array.array("L")
        self._collect(root)
        self.addressed = sum(self.seg_n)
        self._distinct()
        self.data_bytes = sum(self.u_len)

    def _distinct(self):
        """The distinct tile contents, sorted by planet offset (u_off, u_len). The planet
        is clustered: contents are stored in the order of their first tile id, so walking
        the box in tile-id order meets new contents at rising offsets, except contents
        first used outside the box (water, empty land), which are few and kept apart."""
        import array
        import bisect
        main_off, main_len, back = array.array("Q"), array.array("L"), {}
        for off, ln in zip(self.seg_off, self.seg_len):
            if not main_off or off > main_off[-1]:
                main_off.append(off)
                main_len.append(ln)
            elif off not in back:
                k = bisect.bisect_left(main_off, off)
                if k == len(main_off) or main_off[k] != off:
                    back[off] = ln
        if not back:
            self.u_off, self.u_len = main_off, main_len
            return
        self.u_off, self.u_len = array.array("Q"), array.array("L")
        extra = sorted(back.items())
        i = j = 0
        while i < len(main_off) or j < len(extra):
            if j == len(extra) or (i < len(main_off) and main_off[i] < extra[j][0]):
                self.u_off.append(main_off[i])
                self.u_len.append(main_len[i])
                i += 1
            else:
                self.u_off.append(extra[j][0])
                self.u_len.append(extra[j][1])
                j += 1

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _collect(self, root):
        """Read only the leaf directories that can hold tiles in the box, and keep the
        parts of each entry that lie inside it. Each directory's finds go into compact
        arrays of their own; directories cover separate tile-id ranges, so sorting them
        by their first id and joining them gives the whole box in tile-id order."""
        import array
        from concurrent.futures import as_completed
        groups = []
        pending = [root]              # the root decoded; leaf directories still compressed
        dirs_read = tiles = 0
        while pending:
            self._check()
            ents = pending.pop()
            if isinstance(ents, bytes):   # decoded one at a time: decoded, a large area's
                ents = decode_dir(ents, self.h["internal"])  # index held 10x the memory
            wanted = []
            g_id, g_n = array.array("Q"), array.array("Q")
            g_off, g_len = array.array("Q"), array.array("L")

            def add(a, b, off, ln):
                if g_id and g_id[-1] + g_n[-1] == a and g_off[-1] == off:
                    g_n[-1] += b - a
                else:
                    g_id.append(a)
                    g_n.append(b - a)
                    g_off.append(off)
                    g_len.append(ln)
            for i, (tid, off, ln, run) in enumerate(ents):
                if tid >= self.top:
                    break
                hi = ents[i + 1][0] if i + 1 < len(ents) else self.top
                if run == 0:
                    if _range_hits(tid, min(hi, self.top), self.rects, self.maxz):
                        wanted.append((self.h["leaf_off"] + off, ln))
                    continue
                if run <= 16:                 # short: test each tile; long: clip by blocks
                    for t in range(tid, min(tid + run, self.top)):
                        z, x, y = id_to_zxy(t)
                        x0, y0, x1, y1 = self.rects[z]
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            add(t, t + 1, off, ln)
                    continue
                spans = []
                _clip(tid, min(tid + run, self.top), self.rects, self.maxz, spans)
                for a, b in spans:
                    add(a, b, off, ln)
            if g_id:
                groups.append((g_id[0], g_id, g_n, g_off, g_len))
                tiles += sum(g_n)
            if not wanted:
                continue
            wanted.sort()
            reqs = _merged(wanted)

            def fetch(req):
                start, length, parts = req
                blob = self.remote.read(start, length)
                return [blob[s - start:s - start + n] for s, n in parts]
            with ThreadPoolExecutor(WORKERS) as ex:
                futs = [ex.submit(fetch, r) for r in reqs]
                for fut in as_completed(futs):
                    dirs = fut.result()
                    pending.extend(dirs)
                    dirs_read += len(dirs)
                    self.status(f"Reading the map index… {dirs_read:,} directories, "
                                f"{tiles:,} tiles so far")
        self._check()
        groups.sort(key=lambda g: g[0])
        for _, g_id, g_n, g_off, g_len in groups:
            for k in range(len(g_id)):
                a, n, off = g_id[k], g_n[k], g_off[k]
                if (self.seg_id and self.seg_id[-1] + self.seg_n[-1] == a
                        and self.seg_off[-1] == off):
                    self.seg_n[-1] += n
                else:
                    self.seg_id.append(a)
                    self.seg_n.append(n)
                    self.seg_off.append(off)
                    self.seg_len.append(g_len[k])


# --- writing ----------------------------------------------------------------------------
def _dir_entries(plan: Plan, new_pos):
    """The output directory entries, in tile-id order: (id, offset, length, run)."""
    import bisect
    last = None
    for k in range(len(plan.seg_id)):
        u = bisect.bisect_left(plan.u_off, plan.seg_off[k])
        e = (plan.seg_id[k], new_pos[u], plan.seg_len[k], plan.seg_n[k])
        if last and last[0] + last[3] == e[0] and last[1] == e[1]:
            last = (last[0], last[1], last[2], last[3] + e[3])
        else:
            if last:
                yield last
            last = e
    if last:
        yield last


def _build_dirs(plan: Plan, new_pos):
    """Root directory bytes, leaf directory bytes and the entry count. Entries are
    streamed into leaf directories, never held all at once."""
    if len(plan.seg_id) <= 20_000:
        entries = list(_dir_entries(plan, new_pos))
        root = encode_dir(entries)
        if len(root) <= ROOT_LIMIT:
            return root, b"", len(entries)
    leaf_size = 4096
    while True:
        leaves, root_ents, chunk, count = bytearray(), [], [], 0

        def flush():
            blob = encode_dir(chunk)
            root_ents.append((chunk[0][0], len(leaves), len(blob), 0))
            leaves.extend(blob)
        for e in _dir_entries(plan, new_pos):
            chunk.append(e)
            count += 1
            if len(chunk) == leaf_size:
                flush()
                chunk = []
        if chunk:
            flush()
        root = encode_dir(root_ents)
        if len(root) <= ROOT_LIMIT:
            return root, bytes(leaves), count
        leaf_size *= 2


def _requests(u_off, u_len):
    """Download requests over the distinct contents (planet order), as index ranges:
    (start offset, length, first index, end index). Streamed, not listed up front."""
    k, n = 0, len(u_off)
    while k < n:
        start, end, j = u_off[k], u_off[k] + u_len[k], k + 1
        while j < n and u_off[j] - end <= MERGE_GAP and u_off[j] + u_len[j] - start <= MAX_REQUEST:
            end = max(end, u_off[j] + u_len[j])
            j += 1
        yield start, end - start, k, j
        k = j


def download(plan: Plan, out_path: str, *, progress=None, part_cb=None) -> dict:
    """Download the planned tiles and write out_path. Writes <out_path>.part and renames
    it at the end, so a stopped or failed download never leaves a file that looks whole.
    Tile contents go in planet order, which for a cut-out is not always the order of
    first use by tile id, so the file says it is not clustered."""
    import array
    from concurrent.futures import FIRST_COMPLETED, wait
    progress = progress or (lambda done, total: None)
    new_pos, pos = array.array("Q"), 0
    for n in plan.u_len:
        new_pos.append(pos)
        pos += n
    data_len = pos
    root, leaves, n_entries = _build_dirs(plan, new_pos)
    meta = plan.meta_raw
    root_off = 127
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    w, s, e, n = plan.bbox
    header = bytearray(b"PMTiles") + bytes([3])
    header += struct.pack("<11Q", root_off, len(root), meta_off, len(meta), leaf_off,
                          len(leaves), data_off, data_len, plan.addressed, n_entries,
                          len(plan.u_off))
    header += bytes([0, 2, plan.h["tile_comp"], plan.h["tile_type"],
                     0, plan.maxz])
    header += struct.pack("<4i", int(w * 1e7), int(s * 1e7), int(e * 1e7), int(n * 1e7))
    header += bytes([max(0, plan.maxz - 4)])
    header += struct.pack("<2i", int((w + e) / 2 * 1e7), int((s + n) / 2 * 1e7))
    assert len(header) == 127

    part = out_path + ".part"
    if part_cb:
        part_cb(part)
    done = 0
    lock = threading.Lock()
    try:
        with open(part, "wb") as fh:
            fh.write(header)
            fh.write(root)
            fh.write(meta)
            fh.write(leaves)
            fh.truncate(data_off + data_len)

            def fetch(req):
                nonlocal done
                start, length, k0, k1 = req
                blob = plan.remote.read(plan.h["data_off"] + start, length)
                with lock:
                    for k in range(k0, k1):
                        s0 = plan.u_off[k] - start
                        fh.seek(data_off + new_pos[k])
                        fh.write(blob[s0:s0 + plan.u_len[k]])
                        done += plan.u_len[k]
                    progress(done, data_len)
            with ThreadPoolExecutor(WORKERS) as ex:
                inflight = set()
                for req in _requests(plan.u_off, plan.u_len):
                    inflight.add(ex.submit(fetch, req))
                    if len(inflight) >= 4 * WORKERS:
                        finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
                        for f in finished:
                            f.result()
                for f in inflight:
                    f.result()
        os.replace(part, out_path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return {"path": out_path, "size": os.path.getsize(out_path), "tiles": plan.addressed,
            "contents": len(plan.u_off), "maxzoom": plan.maxz, "build": plan.url}


def human(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


# --- command line -----------------------------------------------------------------------
def _parse_bbox(text: str):
    parts = [float(p) for p in text.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox needs four numbers: W,S,E,N")
    w, s, e, n = parts
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("--bbox must be W,S,E,N with W < E and S < N")
    return w, s, e, n


def main_cli(argv) -> int:
    ap = argparse.ArgumentParser(description="Download an area of the Protomaps basemap "
                                 "as one .pmtiles file for GLEAPP.")
    ap.add_argument("--region", help="a named area (see --list-regions)")
    ap.add_argument("--bbox", help="an exact box: W,S,E,N in degrees")
    ap.add_argument("--maxzoom", type=int, default=10, help="detail level, 0-15 (default 10)")
    ap.add_argument("--out", help="the .pmtiles file to write")
    ap.add_argument("--build", help="a build URL (default: the newest on build.protomaps.com)")
    ap.add_argument("--size-only", action="store_true", help="report the size and stop")
    ap.add_argument("--list-regions", action="store_true")
    ap.add_argument("--version", action="version", version=f"GLEAPP-MapDownloader {__version__}")
    a = ap.parse_args(argv)
    if a.list_regions:
        for key, (name, box) in REGIONS.items():
            print(f"  {key:18} {name:28} {box}")
        return 0
    if bool(a.region) == bool(a.bbox):
        ap.error("give either --region or --bbox")
    if a.region and a.region not in REGIONS:
        ap.error(f"unknown region {a.region!r}; see --list-regions")
    bbox = REGIONS[a.region][1] if a.region else _parse_bbox(a.bbox)
    if not a.size_only and not a.out:
        ap.error("--out is required unless --size-only")
    msg = too_big_message(bbox, a.maxzoom)
    if msg:
        raise ValueError(msg)
    n = positions(bbox, a.maxzoom)
    if n > WARN_POSITIONS:
        print(f"Large area: {n:,} tile positions; sizing can take several minutes.")
    url = a.build or latest_build()
    print(f"Build: {url}")
    print("Note: build.protomaps.com sees the area you ask for.")
    t0 = time.time()
    plan = Plan(url, bbox, a.maxzoom, status=lambda m: print("  " + m, end="\r", flush=True))
    print(f"\nArea {bbox}, zoom 0-{plan.maxz}: {plan.addressed:,} tiles, "
          f"download {human(plan.data_bytes)} (index read in {time.time() - t0:.0f} s)")
    if a.size_only:
        return 0
    t1 = time.time()
    res = download(plan, a.out, progress=lambda d, t: print(
        f"  {100 * d / max(t, 1):5.1f}%  {human(d)} of {human(t)}", end="\r", flush=True))
    print(f"\nWrote {res['path']} ({human(res['size'])}) in {time.time() - t1:.0f} s.")
    print("Copy it to the offline machine and import it in GLEAPP: Maps -> Import.")
    print(ATTRIBUTION)
    return 0


# --- window -----------------------------------------------------------------------------
def main_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    if sys.platform == "win32":
        try:                          # sharp text on a scaled display, not a blurry bitmap
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    if sys.platform == "win32":
        try:                          # the taskbar shows this window's icon, not Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GLEAPP.MapDownloader")
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.title("Map Downloader for GLEAPP")
    icons = [tk.PhotoImage(master=root, data=ICON_64), tk.PhotoImage(master=root, data=ICON_32)]
    root.iconphoto(True, *icons)
    root._icons = icons               # keep a reference, or Tk drops the images
    px = root.winfo_fpixels("1i") / 96          # the display's scale; 1.0 at 100%
    root.minsize(int(620 * px), 0)
    # GLEAPP's own palette (gleapp/web/templates/index.html :root)
    bg, panel, panel2, line = "#14161a", "#1c1f26", "#242833", "#333a46"
    fg, muted, accent = "#e6e8ec", "#8b93a3", "#4c8dff"
    font = ("Segoe UI", 10) if sys.platform == "win32" else ("TkDefaultFont", 10)
    root.configure(bg=bg)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=bg, foreground=fg, font=font, bordercolor=line,
                 lightcolor=panel2, darkcolor=panel2, troughcolor=panel, focuscolor=accent)
    st.configure("TFrame", background=bg)
    st.configure("Head.TFrame", background=panel)
    st.configure("TLabel", background=bg, foreground=fg)
    st.configure("Head.TLabel", background=panel, foreground=fg, font=(font[0], 12, "bold"))
    st.configure("Muted.TLabel", background=bg, foreground=muted, font=(font[0], 9))
    st.configure("Info.TLabel", background=panel, foreground=fg, padding=(10, 8))
    for w in ("TEntry", "TCombobox"):
        st.configure(w, fieldbackground=panel2, foreground=fg, insertcolor=fg,
                     arrowcolor=fg, background=panel2, padding=4)
        st.map(w, fieldbackground=[("readonly", panel2), ("disabled", panel)],
               foreground=[("disabled", muted)], selectbackground=[("readonly", panel2)],
               selectforeground=[("readonly", fg)])
    st.configure("TButton", background=panel2, foreground=fg, padding=(12, 5))
    st.map("TButton", background=[("disabled", panel), ("active", line)],
           foreground=[("disabled", muted)])
    st.configure("Accent.TButton", background=accent, foreground="#ffffff")
    st.map("Accent.TButton", background=[("disabled", panel), ("active", "#6aa1ff")],
           foreground=[("disabled", muted)])
    st.configure("Horizontal.TProgressbar", background=accent, troughcolor=panel2,
                 bordercolor=line, lightcolor=accent, darkcolor=accent, thickness=10)
    # the drop-down list of a combobox is a plain Tk listbox
    root.option_add("*TCombobox*Listbox.background", panel2)
    root.option_add("*TCombobox*Listbox.foreground", fg)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    head = ttk.Frame(root, style="Head.TFrame", padding=(14, 10))
    head.grid(row=0, column=0, sticky="ew")
    logo = tk.PhotoImage(master=root, data=LOGO_96)
    if px < 1.5:                      # 48 px, or all 96 on a display scaled 150% or more
        logo = logo.subsample(2)
    icons.append(logo)
    ttk.Label(head, image=logo, style="Head.TLabel").pack(side="left", padx=(0, 10))
    ttk.Label(head, text="Map Downloader", style="Head.TLabel").pack(side="left")
    ttk.Label(head, text="  offline basemaps for GLEAPP", style="Head.TLabel",
              font=(font[0], 10)).pack(side="left")
    tk.Frame(root, bg=line, height=1).grid(row=1, column=0, sticky="ew")
    frm = ttk.Frame(root, padding=14)
    frm.grid(row=2, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    frm.columnconfigure(1, weight=1)

    names = [v[0] for v in REGIONS.values()] + ["Exact box…"]
    keys = list(REGIONS) + ["*"]
    region = tk.StringVar(value=REGIONS["north-america"][0])
    boxtxt = tk.StringVar()
    zoom = tk.StringVar(value=DETAIL[10])
    outp = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads",
                                           "north-america-z10.pmtiles"))
    info = tk.StringVar(value="Pick an area and a detail level, then Check size.")
    prog = tk.DoubleVar(value=0)
    state = {"plan": None, "cancel": None, "busy": False}

    frm.columnconfigure(0, minsize=int(110 * px))
    ttk.Label(frm, text="Area").grid(row=0, column=0, sticky="w", pady=4)
    cb = ttk.Combobox(frm, textvariable=region, values=names, state="readonly")
    cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(frm, text="Box W,S,E,N").grid(row=1, column=0, sticky="w", pady=4)
    box = ttk.Entry(frm, textvariable=boxtxt)
    box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(frm, text="Detail").grid(row=2, column=0, sticky="w", pady=4)
    ttk.Combobox(frm, textvariable=zoom, values=list(DETAIL.values()), state="readonly").grid(
        row=2, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(frm, text="Save to").grid(row=3, column=0, sticky="w", pady=4)
    ttk.Entry(frm, textvariable=outp).grid(row=3, column=1, sticky="ew", pady=4)

    def browse():
        p = filedialog.asksaveasfilename(defaultextension=".pmtiles",
                                         filetypes=[("PMTiles", "*.pmtiles")],
                                         initialfile=os.path.basename(outp.get()))
        if p:
            outp.set(p)
    ttk.Button(frm, text="Browse…", command=browse).grid(row=3, column=2, padx=(6, 0))
    info_lbl = ttk.Label(frm, textvariable=info, justify="left", style="Info.TLabel")
    info_lbl.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 4))
    ttk.Progressbar(frm, variable=prog, maximum=100).grid(row=5, column=0, columnspan=3,
                                                         sticky="ew", pady=4)
    btns = ttk.Frame(frm)
    btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(8, 0))
    b_size = ttk.Button(btns, text="Check size")
    b_go = ttk.Button(btns, text="Download", style="Accent.TButton")
    b_stop = ttk.Button(btns, text="Stop", state="disabled")
    for b in (b_size, b_go, b_stop):
        b.pack(side="left", padx=4)
    note = ttk.Label(frm, text="Downloads from build.protomaps.com, which sees the area you ask "
              "for. " + ATTRIBUTION + " Copy the file to the offline machine and use "
              "Maps → Import in GLEAPP.", style="Muted.TLabel")
    note.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def rewrap(event):                  # wrap the two text blocks at the window's width
        info_lbl.configure(wraplength=max(200, event.width - int(30 * px)))
        note.configure(wraplength=max(200, event.width - int(10 * px)))
    frm.bind("<Configure>", rewrap)

    def zoom_level():
        return next(z for z, t in DETAIL.items() if t == zoom.get())

    def slug():
        k = keys[names.index(region.get())]
        return "area" if k == "*" else k

    def on_region(*_):
        k = keys[names.index(region.get())]
        if k != "*":
            boxtxt.set(",".join(f"{v:g}" for v in REGIONS[k][1]))
            box.state(["readonly"])
        else:
            box.state(["!readonly"])
        folder = os.path.dirname(outp.get()) or "."
        outp.set(os.path.join(folder, f"{slug()}-z{zoom_level()}.pmtiles"))
        state["plan"] = None
    cb.bind("<<ComboboxSelected>>", on_region)
    zoom.trace_add("write", on_region)
    on_region()

    def ui(fn):
        try:
            root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass                          # the window has closed

    def busy(on):
        state["busy"] = on
        b_size.state(["disabled"] if on else ["!disabled"])
        b_go.state(["disabled"] if on else ["!disabled"])
        b_stop.state(["!disabled"] if on else ["disabled"])

    def run(work):
        cancel = threading.Event()
        state["cancel"] = cancel
        busy(True)

        def target():
            try:
                work(cancel)
            except Cancelled:
                ui(lambda: info.set("Stopped. Nothing was saved."))
            except ValueError as exc:         # a message for the user, e.g. too large
                text = str(exc)
                ui(lambda: info.set(text))
            except Exception as exc:          # noqa: BLE001 - shown to the user
                msg = f"{type(exc).__name__}: {exc}"
                ui(lambda: info.set("Failed: " + msg))
            finally:
                ui(lambda: busy(False))
        th = threading.Thread(target=target, daemon=True)
        state["thread"] = th
        th.start()

    def plan_now(cancel, bbox, z):
        ui(lambda: info.set("Finding the newest build…"))
        url = latest_build()
        p = Plan(url, bbox, z, cancel=cancel,
                 status=lambda m: ui(lambda: info.set(m)))
        state["plan"] = (bbox, z, p)      # reused only while the box and zoom still match
        name = url.rsplit("/", 1)[-1].split(".")[0]
        ui(lambda: info.set(f"Download size: {human(p.data_bytes)} · {p.addressed:,} tiles · "
                            f"zoom 0-{p.maxz} · build {name[:4]}-{name[4:6]}-{name[6:8]}"))
        return p

    def size_ok() -> bool:
        """Refuse an area too large for one file, and ask before a large one; this is
        arithmetic on the box, so it answers at once, before anything is downloaded."""
        try:
            bbox = _parse_bbox(boxtxt.get())
        except ValueError as exc:
            info.set(str(exc))
            return False
        msg = too_big_message(bbox, zoom_level())
        if msg:
            info.set(msg)
            return False
        n = positions(bbox, zoom_level())
        if n > WARN_POSITIONS:
            return messagebox.askyesno(
                "Map Downloader", f"This area at zoom {zoom_level()} covers {n:,} tile "
                "positions. Working out its size can take several minutes, and the file "
                "can be several gigabytes. Continue?")
        return True

    def do_size():
        if size_ok():
            bbox, z = _parse_bbox(boxtxt.get()), zoom_level()
            run(lambda cancel: plan_now(cancel, bbox, z))

    def current_plan(bbox, z):
        """The plan from Check size, only if it was made for this box and zoom."""
        saved = state["plan"]
        return saved[2] if saved and saved[0] == bbox and saved[1] == z else None

    def do_go():
        out = outp.get().strip()
        if not out:
            return messagebox.showinfo("Map Downloader", "Choose where to save the file.")
        if os.path.exists(out) and not messagebox.askyesno(
                "Map Downloader", f"{out} exists. Replace it?"):
            return
        try:
            bbox, z = _parse_bbox(boxtxt.get()), zoom_level()
        except ValueError as exc:
            info.set(str(exc))
            return
        saved = current_plan(bbox, z)
        if not saved and not size_ok():
            return

        def work(cancel):
            p = saved or plan_now(cancel, bbox, z)
            p.remote.cancel = p.cancel = cancel
            t0 = time.time()

            def prg(d, t):
                ui(lambda: (prog.set(100 * d / max(t, 1)),
                            info.set(f"Downloading… {100 * d / max(t, 1):.0f}% · "
                                     f"{human(d)} of {human(t)}")))
            res = download(p, out, progress=prg,
                           part_cb=lambda path: state.__setitem__("part", path))
            ui(lambda: (prog.set(100), info.set(
                f"Saved {res['path']} ({human(res['size'])}) in {time.time() - t0:.0f} s. "
                "Copy it to the offline machine and use Maps → Import in GLEAPP.")))
        prog.set(0)
        run(work)

    def do_stop():
        if state["cancel"]:
            state["cancel"].set()
            info.set("Stopping…")
    def on_close():
        """Closing the window ends the program: stop the work, remove a half-written
        file, and exit even if a worker thread is busy (a daemon thread in the middle of
        reading the index otherwise kept the process alive)."""
        if state["cancel"]:
            state["cancel"].set()
        root.destroy()
        th = state.get("thread")
        if th is not None and th.is_alive():
            # a download notices the stop at its next read, closes its file and deletes
            # it; Windows cannot delete a file that is still open, so wait for that
            th.join(timeout=10)
        part = state.get("part")
        if part and os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
        os._exit(0)
    root.protocol("WM_DELETE_WINDOW", on_close)
    b_size.configure(command=do_size)
    b_go.configure(command=do_go)
    b_stop.configure(command=do_stop)
    root.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        try:
            return main_cli(sys.argv[1:])
        except (ValueError, IOError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("\nStopped. Nothing was saved.", file=sys.stderr)
            return 130
    return main_gui()


if __name__ == "__main__":
    sys.exit(main())
