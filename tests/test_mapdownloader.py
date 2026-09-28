"""Offline tests: every download is served from a synthetic planet on 127.0.0.1, and every
result is read back with the independent reader in synthetic_planet.py."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

import mapdownloader as md
import synthetic_planet as sp

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "mapdownloader.py"
BOX = (-40.0, -30.0, 40.0, 30.0)          # half ocean, half land in the synthetic planet


@pytest.fixture(scope="module")
def planet() -> bytes:
    return sp.build_planet()


@pytest.fixture()
def url(planet):
    with sp.serve({"planet.pmtiles": planet}) as base:
        yield base + "planet.pmtiles"


def box_tiles(box, maxz):
    for z in range(maxz + 1):
        x0, y0, x1, y1 = md.tile_rect(box, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


# --- tile ids and the box --------------------------------------------------------------
def test_tile_ids_match_an_independent_implementation():
    rnd = random.Random(7)
    cases = [(z, x, y) for z in range(8) for x in range(1 << z) for y in range(1 << z)]
    for _ in range(20000):
        z = rnd.randint(8, 15)
        cases.append((z, rnd.randrange(1 << z), rnd.randrange(1 << z)))
    for z, x, y in cases:
        tid = md.zxy_to_id(z, x, y)
        assert tid == sp.tile_id(z, x, y), (z, x, y)
        assert md.id_to_zxy(tid) == (z, x, y)


def test_the_tile_id_comparison_can_fail():
    # the same comparison with x and y swapped must disagree, or it proves nothing
    assert any(md.zxy_to_id(4, x, y) != sp.tile_id(4, y, x) for x in range(16) for y in range(16))


@pytest.mark.parametrize("box, maxz", [
    (BOX, 8), (md.REGIONS["florida"][1], 11), (md.REGIONS["puerto-rico"][1], 13),
    (md.REGIONS["united-kingdom"][1], 10), ((-180.0, -85.0, 180.0, 85.0), 5),
])
def test_clipping_by_blocks_matches_brute_force(box, maxz):
    want = {sp.tile_id(z, x, y) for z, x, y in box_tiles(box, maxz)}
    rects = {z: md.tile_rect(box, z) for z in range(maxz + 1)}
    spans = []
    md._clip(0, md._base(maxz + 1), rects, maxz, spans)       # pylint: disable=protected-access
    assert {t for a, b in spans for t in range(a, b)} == want
    assert md.positions(box, maxz) == len(want)
    rnd = random.Random(maxz)
    for _ in range(2000):
        lo = rnd.randrange(md._base(maxz + 1))                  # pylint: disable=protected-access
        hi = lo + rnd.randint(1, 5000)
        assert md._range_hits(lo, hi, rects, maxz) == any(lo <= t < hi for t in want)  # pylint: disable=protected-access


# --- downloading ------------------------------------------------------------------------
@pytest.mark.parametrize("layout", ["root", "leaves"])
def test_download_holds_exactly_the_box_as_the_planet_has_it(planet, url, tmp_path, monkeypatch, layout):
    if layout == "leaves":
        monkeypatch.setattr(md, "ROOT_LIMIT", 200)             # force leaf directories
    plan = md.Plan(url, BOX, 8)
    assert max(plan.seg_n) > 16                                 # long ocean runs were clipped
    assert len(plan.u_off) < plan.addressed                     # ocean content stored once
    out = tmp_path / "area.pmtiles"
    res = md.download(plan, str(out))
    buf = out.read_bytes()
    h = sp.read_header(buf)
    assert (h["leaf_len"] > 0) == (layout == "leaves")
    assert h["internal"] == 2 and h["clustered"] == 0 and (h["minz"], h["maxz"]) == (0, 8)
    assert h["bounds"] == pytest.approx(list(BOX))
    assert h["addressed"] == res["tiles"] == md.positions(BOX, 8)
    assert sp.metadata(buf) == sp.metadata(planet)             # attribution travels with it
    n = 0
    for z, x, y in box_tiles(BOX, 8):
        assert sp.read_tile(buf, z, x, y) == sp.read_tile(planet, z, x, y) == sp.content(z, x, y)
        n += 1
    assert n == h["addressed"]
    for z in range(9):                                          # nothing outside the box
        x0, y0, x1, y1 = md.tile_rect(BOX, z)
        for x, y in ((x0 - 1, y0), (x1 + 1, y1), (x0, y0 - 1), (x1, y1 + 1)):
            if 0 <= x < (1 << z) and 0 <= y < (1 << z):
                assert sp.read_tile(buf, z, x, y) is None, (z, x, y)
    assert not Path(str(out) + ".part").exists()


def test_zoom_is_capped_at_the_planets(url):
    assert md.Plan(url, BOX, 12).maxz == 8


def test_a_stopped_download_leaves_no_file(url, tmp_path):
    plan = md.Plan(url, BOX, 8)
    real, calls = plan.remote.read, [0]

    def stop_on_second(start, length):
        calls[0] += 1
        if calls[0] == 2:
            plan.remote.cancel.set()
        return real(start, length)
    plan.remote.read = stop_on_second
    out = tmp_path / "stopped.pmtiles"
    with pytest.raises(md.Cancelled):
        md.download(plan, str(out))
    assert not out.exists() and not Path(str(out) + ".part").exists()


def test_a_planet_with_uncompressed_directories_is_refused():
    with sp.serve({"p.pmtiles": sp.build_planet(internal=1)}) as base:
        with pytest.raises(ValueError, match="only gzip"):
            md.Plan(base + "p.pmtiles", BOX, 8)


def test_a_server_that_ignores_byte_ranges_is_an_error(planet, monkeypatch):
    monkeypatch.setattr(md.time, "sleep", lambda s: None)
    with sp.serve({"p.pmtiles": planet}, honour_range=False) as base:
        with pytest.raises(IOError, match="ignored the byte range"):
            md.Plan(base + "p.pmtiles", BOX, 8)


def test_an_area_too_large_is_refused_before_anything_is_read():
    with pytest.raises(ValueError, match="too large for one file"):
        md.Plan("http://127.0.0.1:9/never-contacted.pmtiles", md.REGIONS["north-america"][1], 15)


# --- command line -----------------------------------------------------------------------
def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          check=False, timeout=300)


def test_command_line_writes_the_same_file_as_the_library(url, tmp_path):
    lib = tmp_path / "lib.pmtiles"
    md.download(md.Plan(url, BOX, 8), str(lib))
    cli = tmp_path / "cli.pmtiles"
    r = run_cli("--build", url, "--bbox=-40,-30,40,30", "--maxzoom", "8", "--out", str(cli))
    assert r.returncode == 0, r.stderr
    assert cli.read_bytes() == lib.read_bytes()
    assert "sees the area you ask for" in r.stdout and md.ATTRIBUTION in r.stdout


def test_command_line_size_only_writes_nothing(url, tmp_path):
    r = run_cli("--build", url, "--bbox=-40,-30,40,30", "--maxzoom", "8", "--size-only")
    assert r.returncode == 0, r.stderr
    assert f"{md.positions(BOX, 8):,} tiles" in r.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args, message", [
    (["--maxzoom", "10"], "give either --region or --bbox"),
    (["--bbox=-76,38,-77,39", "--size-only"], "W < E"),
    (["--region", "atlantis", "--size-only"], "unknown region"),
    (["--region", "north-america", "--maxzoom", "15", "--size-only"], "too large for one file"),
    (["--region", "florida"], "--out is required"),
])
def test_command_line_refusals(args, message):
    r = run_cli(*args)
    assert r.returncode == 2 and message in r.stderr


@pytest.mark.parametrize("name, size", [("ICON_64", 64), ("ICON_32", 32), ("LOGO_96", 96)])
def test_the_inline_logos_are_pngs_of_their_size(name, size):
    import base64
    import struct
    png = base64.b64decode("".join(getattr(md, name)))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    assert struct.unpack(">II", png[16:24]) == (size, size)


def test_version():
    assert run_cli("--version").stdout.strip() == f"GLEAPP-MapDownloader {md.__version__}"


# --- the window -------------------------------------------------------------------------
def test_window_downloads_the_box_shown_not_an_earlier_size_check(url, tmp_path, monkeypatch):
    """Check size on one box, edit the box, Download: the file must hold the edited box.
    A second Download with nothing changed must reuse the size check."""
    tk = pytest.importorskip("tkinter")
    from tkinter import ttk
    # No throwaway Tk() to probe for a display: on macOS, Tk 9.0.3 aborted the process
    # (SIGTRAP in Tk_MacOSXGetTkWindow) when a second root ran its event loop after a
    # first one had been destroyed. The app only ever makes one root.
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("no display for Tk (run under xvfb-run)")

    monkeypatch.setattr(md, "latest_build", lambda: url)
    plans = []
    real_plan = md.Plan

    class CountingPlan(real_plan):
        def __init__(self, *a, **k):
            plans.append(a[1])
            super().__init__(*a, **k)
    monkeypatch.setattr(md, "Plan", CountingPlan)
    roots = []

    class HiddenTk(tk.Tk):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.withdraw()
            roots.append(self)
    monkeypatch.setattr(tk, "Tk", HiddenTk)

    first, edited = "-40,-30,0,0", "0,0,40,30"
    out = tmp_path / "window.pmtiles"
    seen = {}

    def script(root):
        ws, stack = [], [root]
        while stack:
            w = stack.pop()
            ws.append(w)
            stack.extend(w.winfo_children())
        ws.reverse()
        area = next(w for w in ws if isinstance(w, ttk.Combobox))
        entries = [w for w in ws if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox)]
        entries.sort(key=lambda w: int(w.grid_info()["row"]))
        box, save_to = entries
        button = {w.cget("text"): w for w in ws if isinstance(w, ttk.Button)}

        def put(entry, text):
            entry.delete(0, "end")
            entry.insert(0, text)

        def idle():
            return button["Download"].instate(["!disabled"])

        def when(cond, then, tries=0):
            if cond():
                root.after(50, then)
            elif tries > 1200:
                seen["timeout"] = True
                root.quit()
            else:
                root.after(25, lambda: when(cond, then, tries + 1))

        seen["header logo"] = [str(w.cget("image")) for w in ws
                               if isinstance(w, ttk.Label) and str(w.cget("image"))]
        root.update_idletasks()
        seen["width"] = root.winfo_reqwidth() / max(root.winfo_fpixels("1i") / 96, 1.0)

        def check_size():
            area.set("Exact box…")
            area.event_generate("<<ComboboxSelected>>")
            root.update()
            put(box, first)
            put(save_to, str(out))
            button["Check size"].invoke()
            when(lambda: len(plans) == 1 and idle(), edit_then_download)

        def edit_then_download():
            put(box, edited)
            button["Download"].invoke()
            when(lambda: out.exists() and idle(), download_again)

        def download_again():
            seen["first file"] = sp.read_header(out.read_bytes())["bounds"]
            out.unlink()
            button["Check size"].invoke()
            when(lambda: len(plans) == 3 and idle(), lambda: finish(len(plans)))

        def finish(before):
            button["Download"].invoke()
            when(lambda: out.exists() and idle(), lambda: done(before))

        def done(before):
            seen["plans made by the last download"] = len(plans) - before
            root.quit()
        check_size()

    real_mainloop = tk.Misc.mainloop

    def mainloop(self, n=0):
        self.after(100, lambda: script(self))
        real_mainloop(self, n)
    monkeypatch.setattr(tk.Misc, "mainloop", mainloop)
    try:
        md.main_gui()
    finally:
        for r in roots:
            try:
                r.destroy()
            except tk.TclError:
                pass
    assert "timeout" not in seen, seen
    assert len(seen["header logo"]) == 1                        # the map logo in the header
    assert seen["width"] < 800, seen["width"]       # fits a small screen; unwrapped it was 1,011
    assert seen["first file"] == pytest.approx([0.0, 0.0, 40.0, 30.0])
    assert seen["plans made by the last download"] == 0
