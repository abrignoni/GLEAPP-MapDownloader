"""A small PMTiles v3 "planet" built in memory, an independent reader for it, and a local
HTTP server that answers byte-range requests the way build.protomaps.com does.

Nothing here imports mapdownloader: the tile ids, the directory encoding and the reader
are written again from the PMTiles v3 specification (protomaps/PMTiles spec/v3/spec.md),
with the Hilbert walk in the form go-pmtiles uses (rotation by the current block size),
so a test that compares the two is comparing two implementations.

The planet has every tile from zoom 0 to MAXZ. Tiles in the western half of the world
from zoom 2 up share one "ocean" content, so the directories carry long runs and the
data section repeats nothing; every other tile has content of its own. As in a real
build, contents are stored in the order of their first tile id (clustered), and the
root directory points at leaf directories.
"""

from __future__ import annotations

import contextlib
import gzip
import http.server
import json
import re
import struct
import threading

MAXZ = 8
OCEAN = gzip.compress(b"ocean", mtime=0)


def base(z: int) -> int:
    return ((1 << (2 * z)) - 1) // 3


def tile_id(z: int, x: int, y: int) -> int:
    acc = base(z)
    for a in range(z - 1, -1, -1):
        s = 1 << a
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        acc += ((3 * rx) ^ ry) << (2 * a)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
    return acc


def is_ocean(z: int, x: int, y: int) -> bool:
    return z >= 2 and x < (1 << z) // 2


def content(z: int, x: int, y: int) -> bytes:
    if is_ocean(z, x, y):
        return OCEAN
    return gzip.compress(f"tile {z}/{x}/{y}".encode(), mtime=0)


def _uvarint(out: bytearray, v: int) -> None:
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _read_uvarint(buf: bytes, pos: int):
    v = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, pos
        shift += 7


def encode_directory(entries, compress: bool = True) -> bytes:
    out = bytearray()
    _uvarint(out, len(entries))
    last = 0
    for tid, _o, _l, _r in entries:
        _uvarint(out, tid - last)
        last = tid
    for e in entries:
        _uvarint(out, e[3])
    for e in entries:
        _uvarint(out, e[2])
    for i, (_t, off, ln, _r) in enumerate(entries):
        if i and off == entries[i - 1][1] + entries[i - 1][2]:
            _uvarint(out, 0)
        else:
            _uvarint(out, off + 1)
    return gzip.compress(bytes(out), mtime=0) if compress else bytes(out)


def decode_directory(raw: bytes, compressed: bool = True):
    buf = gzip.decompress(raw) if compressed else raw
    n, pos = _read_uvarint(buf, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        d, pos = _read_uvarint(buf, pos)
        last += d
        ids.append(last)
    for _ in range(n):
        v, pos = _read_uvarint(buf, pos)
        runs.append(v)
    for _ in range(n):
        v, pos = _read_uvarint(buf, pos)
        lens.append(v)
    for i in range(n):
        v, pos = _read_uvarint(buf, pos)
        offs.append(offs[i - 1] + lens[i - 1] if v == 0 and i else v - 1)
    return list(zip(ids, offs, lens, runs))


def build_planet(maxz: int = MAXZ, *, internal: int = 2, leaf_size: int = 64) -> bytes:
    """The planet as bytes. internal=1 writes uncompressed directories and metadata."""
    compress = internal == 2
    by_id = {}
    for z in range(maxz + 1):
        for x in range(1 << z):
            for y in range(1 << z):
                by_id[tile_id(z, x, y)] = content(z, x, y)
    assert len(by_id) == base(maxz + 1)
    data, where, entries = bytearray(), {}, []
    for tid in sorted(by_id):
        blob = by_id[tid]
        if blob not in where:                     # clustered: first use decides the offset
            where[blob] = len(data)
            data += blob
        off = where[blob]
        last = entries[-1] if entries else None
        if last and last[0] + last[3] == tid and last[1] == off:
            entries[-1] = (last[0], last[1], last[2], last[3] + 1)
        else:
            entries.append((tid, off, len(blob), 1))
    leaves, root = bytearray(), []
    for i in range(0, len(entries), leaf_size):
        chunk = entries[i:i + leaf_size]
        blob = encode_directory(chunk, compress)
        root.append((chunk[0][0], len(leaves), len(blob), 0))
        leaves += blob
    root_bytes = encode_directory(root, compress)
    meta = json.dumps({"name": "synthetic planet", "attribution": "synthetic test data"}).encode()
    if compress:
        meta = gzip.compress(meta, mtime=0)
    root_off = 127
    meta_off = root_off + len(root_bytes)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    assert data_off > root_off and root_off + len(root_bytes) <= 16384
    header = bytearray(b"PMTiles") + bytes([3])
    header += struct.pack("<11Q", root_off, len(root_bytes), meta_off, len(meta), leaf_off,
                          len(leaves), data_off, len(data), len(by_id), len(entries), len(where))
    header += bytes([1, internal, 2, 1, 0, maxz])
    header += struct.pack("<4i", -1800000000, -850000000, 1800000000, 850000000)
    header += bytes([0]) + struct.pack("<2i", 0, 0)
    assert len(header) == 127
    return bytes(header + root_bytes + meta + leaves + data)


def read_header(buf: bytes) -> dict:
    assert buf[:7] == b"PMTiles" and buf[7] == 3
    f = struct.unpack_from("<11Q", buf, 8)
    keys = ("root_off", "root_len", "meta_off", "meta_len", "leaf_off", "leaf_len",
            "data_off", "data_len", "addressed", "entries", "contents")
    h = dict(zip(keys, f))
    h.update(clustered=buf[96], internal=buf[97], tile_comp=buf[98], tile_type=buf[99],
             minz=buf[100], maxz=buf[101],
             bounds=[v / 1e7 for v in struct.unpack_from("<4i", buf, 102)])
    return h


def _find(entries, tid):
    found = None
    for e in entries:                              # entries are sorted by tile id
        if e[0] <= tid:
            found = e
        else:
            break
    if found is None:
        return None
    if found[3] == 0:
        return found
    return found if tid < found[0] + found[3] else None


def read_tile(buf: bytes, z: int, x: int, y: int):
    """The stored (compressed) bytes of one tile, or None. Root plus one leaf level."""
    h = read_header(buf)
    comp = h["internal"] == 2
    tid = tile_id(z, x, y)
    root = decode_directory(buf[h["root_off"]:h["root_off"] + h["root_len"]], comp)
    e = _find(root, tid)
    if e and e[3] == 0:
        start = h["leaf_off"] + e[1]
        e = _find(decode_directory(buf[start:start + e[2]], comp), tid)
    if not e or e[3] == 0:
        return None
    start = h["data_off"] + e[1]
    return buf[start:start + e[2]]


def metadata(buf: bytes) -> dict:
    h = read_header(buf)
    raw = buf[h["meta_off"]:h["meta_off"] + h["meta_len"]]
    return json.loads(gzip.decompress(raw) if h["internal"] == 2 else raw)


@contextlib.contextmanager
def serve(files: dict, *, honour_range: bool = True):
    """Serve {name: bytes} on 127.0.0.1 with byte-range support; yields the base URL."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                          # noqa: N802 (http.server's name)
            body = files.get(self.path.lstrip("/"))
            if body is None:
                self.send_error(404)
                return
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            if m and honour_range:
                a, b = int(m.group(1)), min(int(m.group(2)), len(body) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(body)}")
                body = body[a:b + 1]
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
