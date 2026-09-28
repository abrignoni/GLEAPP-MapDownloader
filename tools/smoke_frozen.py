"""Check that a built GLEAPP-MapDownloader behaves as the Python file it was built from.

    python tools/smoke_frozen.py <source folder> <executable>

<source folder> is a checkout holding mapdownloader.py and tests/ (the build workflow checks
out the tag being released there). A synthetic planet from tests/synthetic_planet.py is
served on 127.0.0.1, and the same area is downloaded once through `python mapdownloader.py`
and once through the executable: the two files must be byte-identical. --version and
--list-regions must agree too. Last, the executable is started with no arguments and must
still be running, with its window open, a few seconds later; on Linux run this under
xvfb-run so there is a display.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
    if r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {' '.join(cmd)}\n{r.stdout[-2000:]}\n{r.stderr[-4000:]}")
    return r.stdout


def window_stays_open(exe: str, seconds: float = 8.0) -> None:
    proc = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(seconds)
        if proc.poll() is not None:
            out, err = proc.communicate()
            sys.exit(f"the window closed on its own (exit {proc.returncode})\n{out[-2000:]}\n{err[-4000:]}")
    finally:
        if proc.poll() is None:
            if sys.platform == "win32":        # a one-file build runs as a parent and a child
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, check=False)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(f"the executable's window was still open after {seconds:.0f} s")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit(__doc__)
    src = Path(argv[0]).resolve()
    exe = str(Path(argv[1]).resolve())
    py = [sys.executable, str(src / "mapdownloader.py")]
    sys.path.insert(0, str(src / "tests"))
    import synthetic_planet as sp               # pylint: disable=import-outside-toplevel

    source_version = run(py + ["--version"]).strip()
    built_version = run([exe, "--version"]).strip()
    print("source:", source_version, "| built:", built_version)
    assert built_version == source_version, (built_version, source_version)
    assert run(py + ["--list-regions"]) == run([exe, "--list-regions"])

    with tempfile.TemporaryDirectory() as tmp, sp.serve({"planet.pmtiles": sp.build_planet()}) as base:
        url = base + "planet.pmtiles"
        area = ["--build", url, "--bbox=-40,-30,40,30", "--maxzoom", "8"]
        a, b = Path(tmp) / "source.pmtiles", Path(tmp) / "built.pmtiles"
        run(py + area + ["--out", str(a)])
        run([exe] + area + ["--out", str(b)])
        want, got = a.read_bytes(), b.read_bytes()
        tiles = sp.read_header(want)["addressed"]
        print(f"source wrote {len(want):,} bytes ({tiles:,} tiles), the executable {len(got):,}")
        if want != got:
            print("the files differ")
            return 1
        assert tiles > 1000, tiles
    window_stays_open(exe)
    print("the executable wrote the same file as the source")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
