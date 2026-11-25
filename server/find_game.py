import os
import re
import sys

APP_ID = "439350"
EXE = "KRITIKA_Client.exe"


def _steam_root():
    try:
        import winreg
    except ImportError:
        return None
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
        for name in ("SteamPath", "InstallPath"):
            try:
                with winreg.OpenKey(hive, key) as k:
                    return os.path.normpath(winreg.QueryValueEx(k, name)[0])
            except OSError:
                pass
    return None


def _libraries(root):
    libs = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def find():
    root = _steam_root()
    if not root:
        return None
    for lib in _libraries(root):
        manifest = os.path.join(lib, "steamapps", "appmanifest_%s.acf" % APP_ID)
        try:
            with open(manifest, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
        except OSError:
            continue
        if m:
            d = os.path.join(lib, "steamapps", "common", m.group(1))
            if os.path.isfile(os.path.join(d, EXE)):
                return d
    return None


if __name__ == "__main__":
    d = find()
    if d:
        sys.stdout.write(d)
