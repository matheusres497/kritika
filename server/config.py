import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


SUPPORTED_EXE_SHA256 = "e74d3fb488ad6a46eb1eec463d855b13a34830bcfbe75c1b698371d84d4564fe"


def _game_dir():
    d = os.environ.get("KRITIKA_DIR", "").strip().strip('"')
    if not d:
        f = os.path.join(ROOT, "game_dir.txt")
        if os.path.exists(f):
            with open(f, encoding="utf-8-sig") as fh:
                d = fh.readline().strip().strip('"')
    if not d or not os.path.isfile(os.path.join(d, "KRITIKA_Client.exe")):
        import find_game
        d = find_game.find() or d
    if not d or not os.path.isfile(os.path.join(d, "KRITIKA_Client.exe")):
        raise SystemExit("Game folder not set or KRITIKA_Client.exe not found in it. Set KRITIKA_DIR or put the folder path "
                         "on the first line of game_dir.txt (start.bat does this for you). Current value: %r" % d)
    return os.path.abspath(d)


GAME_DIR = _game_dir()
CLIENT_EXE = os.path.join(GAME_DIR, "KRITIKA_Client.exe")
SDB_DIR = os.path.join(GAME_DIR, "resource", "Common", "database", "sdb")
ALP_DIR = os.path.join(ROOT, "cache", "alp")
os.environ["KRITIKA_SDB"] = SDB_DIR


def _check_exe():
    h = hashlib.sha256()
    with open(CLIENT_EXE, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    if h.hexdigest() != SUPPORTED_EXE_SHA256:
        print("WARNING: KRITIKA_Client.exe (sha256 %s...) is not the build this server was written for (%s...); "
              "message ids and data layouts may differ." % (h.hexdigest()[:12], SUPPORTED_EXE_SHA256[:12]), flush=True)


def _extract(archive, sub):
    src = os.path.join(GAME_DIR, "resource", "Common", archive)
    out = os.path.join(ALP_DIR, sub)
    st = os.stat(src)
    stamp = "%d %d" % (st.st_size, int(st.st_mtime))
    marker = os.path.join(out, ".extracted")
    try:
        if open(marker).read() == stamp:
            return
    except OSError:
        pass
    print("First start: extracting %s (one time, takes a moment) ..." % archive, flush=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "alp.py"), "extract", src, out],
                       capture_output=True, text=True)
    if r.returncode != 0 or "0 failed" not in r.stderr + r.stdout:
        raise SystemExit("Extraction of %s failed:\n%s%s" % (archive, r.stdout, r.stderr))
    with open(marker, "w") as f:
        f.write(stamp)


_check_exe()
_extract("Database.alph", "Database")
_extract("Script.alph", "Script")
