import os
import re
import sqlite3
import struct

import config

SDB_DIR = config.SDB_DIR


def _wstr(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    if n == 0 or n > 400 or o + 4 + 2 * n > len(b):
        return None, o
    raw = b[o + 4:o + 4 + 2 * n]
    if raw[-2:] != b"\0\0":
        return None, o
    try:
        return raw[:-2].decode("utf-16-le"), o + 4 + 2 * n
    except UnicodeDecodeError:
        return None, o


def stage_record(stage_id):
    db = sqlite3.connect(f"file:{os.path.join(SDB_DIR, 'StageList.db')}?mode=ro", uri=True)
    key = struct.pack("<I", len(stage_id) + 1) + (stage_id + "\0").encode("utf-16-le")
    for _, blob in db.execute("select Hash, SerializedBinary from StageList"):
        i = blob.find(key)
        if 0 <= i < 64:
            return blob
    return None


def sectors(stage_id):
    b = stage_record(stage_id)
    if b is None:
        return {}
    marks = []
    maps_tag = "/Maps/".encode("utf-16-le")
    o = b.find(maps_tag)
    while o >= 0:
        marks.append(o - 4)
        o = b.find(maps_tag, o + 2)
    out = {}
    for k, start in enumerate(marks):

        sid = None
        for back in range(start - 4, max(0, start - 200), -2):
            s, e = _wstr(b, back)
            if s and re.fullmatch(r"Sector\w*", s):
                sid = s
                break
        end = marks[k + 1] if k + 1 < len(marks) else len(b)
        mp, _ = _wstr(b, start)
        pops = []
        o = start
        while o < end - 24:
            npc, o2 = _wstr(b, o + 20)
            if npc and re.fullmatch(r"[A-Za-z][\w]*", npc):
                act, o3 = _wstr(b, o2)
                loc, o4 = _wstr(b, o3) if act else (None, o3)
                team, o5 = _wstr(b, o4) if loc is not None else (None, o4)
                if act and loc is not None and team is not None and re.fullmatch(r"Location_\w+|NONE|", loc):
                    px, pz = struct.unpack_from("<2f", b, o)
                    d = struct.unpack_from("<3f", b, o + 8)
                    n = struct.unpack_from("<I", b, o5)[0]
                    days, o6 = [], o5 + 4
                    for _ in range(n if n < 20 else 0):
                        s, o6 = _wstr(b, o6)
                        days.append(s)
                    if npc != "NONE" and team != "UNKNOWN" and all(abs(v) < 1e5 for v in (px, pz) + d):
                        pops.append({"NpcId": npc, "DefaultActionId": act, "LocationId": loc, "TeamId": team,
                                     "Position": (px, pz), "Direction": d, "PopDay": days})
                        o = o6
                        continue
            o += 1
        out[sid or f"#{k}"] = {"map": mp, "pops": pops}
    return out


if __name__ == "__main__":
    import json, sys
    print(json.dumps(sectors(sys.argv[1] if len(sys.argv) > 1 else "Ep01Stage01"), indent=1))


_STAGE_INFO = {}


def stage_info(stage_id):
    if stage_id not in _STAGE_INFO:
        _STAGE_INFO[stage_id] = _stage_info(stage_id)
    return _STAGE_INFO[stage_id]


def _stage_info(stage_id):
    import json, sys
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(root, "server", "schemas", "schema_StageInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("StageList.db", stage_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    return out


def warp_target(stage_id, sector_id, warp_id):
    info = stage_info(stage_id)
    for link in info.get("WarpGateLinkInfos", []):
        gates = link.get("Warpgates", [])
        for i, g in enumerate(gates):
            if g["SectorId"] == sector_id and g["WarpGateId"] == warp_id and len(gates) == 2:
                o = gates[1 - i]
                for sec in info.get("Sectors", []):
                    if sec["Id"] == o["SectorId"]:
                        for wg in sec["WarpGates"]:
                            if wg["Id"] == o["WarpGateId"]:
                                return o["SectorId"], o["WarpGateId"], wg["Position"], wg["Direction"]
    return None


def village_for_stage(stage_id):
    import json, sqlite3, sys
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(root, "server", "schemas", "schema_RegionInfo.json"))))
    db = sqlite3.connect(f"file:{os.path.join(SDB_DIR, 'RegionList.db')}?mode=ro", uri=True)
    for (blob,) in db.execute("select SerializedBinary from RegionList"):
        out = {}
        try:
            sdb_decode.fields(bytes(blob), 0, sch["fields"], out, 10 ** 6)
        except Exception:
            continue
        for _gate, entries in out.get("StageIds", []):
            ids = [e["Id"] for e in entries]
            if stage_id in ids:
                for i in ids:
                    if stage_info(i).get("StageType") == "VILLAGE":
                        return i
    return None


def next_region_stage(stage_id):
    import json, sqlite3, sys
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(root, "server", "schemas", "schema_RegionInfo.json"))))
    db = sqlite3.connect(f"file:{os.path.join(SDB_DIR, 'RegionList.db')}?mode=ro", uri=True)
    for (blob,) in db.execute("select SerializedBinary from RegionList"):
        out = {}
        try:
            sdb_decode.fields(bytes(blob), 0, sch["fields"], out, 10 ** 6)
        except Exception:
            continue
        for _gate, entries in out.get("StageIds", []):
            cur = next((e for e in entries if e["Id"] == stage_id), None)
            if cur is None or cur["SortIndex"] < 0:
                continue
            above = sorted((e["SortIndex"], e["Id"]) for e in entries if e["SortIndex"] > cur["SortIndex"])
            return above[0][1] if above else None
    return None


_GENERAL = {}


def general_info(name):
    if not _GENERAL:
        import json
        import config
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "schema_GeneralInfo.json"))))
        try:
            sdb_decode.fields(open(os.path.join(config.ALP_DIR, "Database", "GeneralInfo.b"), "rb").read(), 0, sch["fields"], _GENERAL, 10 ** 6)
        except Exception:
            pass
        _GENERAL["__loaded__"] = True
    return _GENERAL[name]


def sector_warp_offset():
    return float(general_info("SectorWarpOffset"))
