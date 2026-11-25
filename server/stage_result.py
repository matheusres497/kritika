import math
import os
import re
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = __import__("config").ALP_DIR + os.sep + "Database"
ALL_SECTOR_CLEAR_BONUS_RATE = 0.03
GRADE_ORDER = ["D", "C", "B", "A", "S", "SS"]

_SCORE = {}
_VAL = {}


def score_info():
    if _SCORE:
        return _SCORE
    b = open(os.path.join(DB_DIR, "ScoreInfo.b"), "rb").read()
    o = 0
    n = struct.unpack_from("<I", b, o)[0]
    o += 4
    ranks = {}
    for _ in range(n):
        k = struct.unpack_from("<I", b, o)[0]
        s = b[o + 4:o + 4 + 2 * k].decode("utf-16-le").rstrip("\0")
        o += 4 + 2 * k
        ranks[s] = struct.unpack_from("<i", b, o)[0]
        o += 4

    def vmap(o, fmt):
        n = struct.unpack_from("<I", b, o)[0]
        o += 4
        out = []
        for _ in range(n):
            lo, hi = struct.unpack_from("<ii", b, o)
            o += 8
            out.append((lo, hi, struct.unpack_from("<" + fmt, b, o)[0]))
            o += 4
        return out, o
    multi, o = vmap(o, "f")
    chain, o = vmap(o, "f")
    chain_kill, o = vmap(o, "i")
    dead, o = vmap(o, "f")
    n = struct.unpack_from("<I", b, o)[0]
    o += 4
    perfect = {}
    for _ in range(n):
        k, v = struct.unpack_from("<ii", b, o)
        o += 8
        perfect[k] = v
    perfect_bonus = struct.unpack_from("<f", b, o)[0]
    o += 4
    kill_bonus = dict(zip(("BackAttack", "SpecialAttack", "SingleExSkill", "DoubleExSkill", "OneKill"), struct.unpack_from("<5f", b, o)))
    _SCORE.update(ranks=ranks, multi=multi, chain=chain, chain_kill=chain_kill, dead=dead, perfect=perfect, perfect_bonus=perfect_bonus, kill_bonus=kill_bonus)
    return _SCORE


def _lookup(table, n, default):
    for lo, hi, v in table:
        if lo <= n <= hi:
            return v
    return default


def valuation(vid="Default"):
    if vid in _VAL:
        return _VAL[vid]
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import sdb_decode
    db = os.path.join(sdb_decode.SDB, "ValuationInfo.db")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = {}
    for (b,) in c.execute("select SerializedBinary from ValuationInfo"):
        b = bytes(b)
        k = struct.unpack_from("<I", b, 0)[0]
        name = b[4:4 + 2 * k].decode("utf-16-le").rstrip("\0")
        o = 4 + 2 * k
        n = struct.unpack_from("<I", b, o)[0]
        o += 4
        grades = []
        for _ in range(n):
            sc = struct.unpack_from("<f", b, o)[0]
            o += 4
            k = struct.unpack_from("<I", b, o)[0]
            g = b[o + 4:o + 4 + 2 * k].decode("utf-16-le").rstrip("\0")
            o += 4 + 2 * k
            gr, er = struct.unpack_from("<ff", b, o)
            o += 8
            grades.append((g, sc, gr, er))
        rows[name] = grades
    _VAL.update(rows)
    return _VAL.get(vid) or _VAL["Default"]


def grade_for(value, vid="Default"):
    g = valuation(vid)
    for rank, limit, gold, exp in g:
        if limit >= value:
            return rank, gold, exp
    return g[-1][0], g[-1][2], g[-1][3]


def new_run(start):
    return {"start": start, "kills": [], "cleared": False, "reward_taken": False, "score": 0, "kill_count": 0, "hit_chain": 0, "kill_chain": 0,
            "hits_taken": 0, "deaths": 0, "counted": set(), "last_bonus": {}}


MAX_CHAIN_TIME = 1.3
CHAIN_TIME_BONUS_PER_HIT = 1.3


def player_hit_npc(run, npc_serial, hit_type, now=None):
    si = score_info()
    if now is not None:
        left = run.get("chain_left", 0.0) - (now - run.get("chain_t", now))
        if left <= 0.0:
            run["hit_chain"] = 0
            left = 0.0
        run["chain_left"], run["chain_t"] = min(MAX_CHAIN_TIME, left + CHAIN_TIME_BONUS_PER_HIT), now
    run["hit_chain"] += 1
    bonus = _lookup(si["chain"], run["hit_chain"], 0.0)
    if hit_type & 4:
        bonus += si["kill_bonus"]["BackAttack"]
    run["last_bonus"][npc_serial] = bonus


def player_got_hit(run):
    run["hits_taken"] += 1
    run["kill_chain"] = 0
    run["hit_chain"] = 0


def player_killed_npc(run, serial, rank):
    if serial in run["counted"]:
        return 0
    run["counted"].add(serial)
    si = score_info()
    run["kill_count"] += 1
    run["kill_chain"] += 1
    mult = _lookup(si["chain_kill"], run["kill_chain"], 1)
    pts = int((1.0 + run["last_bonus"].get(serial, 0.0)) * si["ranks"].get(rank, 0) * mult)
    run["score"] += pts
    return pts


def countable(npc_info):
    return npc_info.get("RaceId") != "Destructibleobject"


def _difficulty_rate(name):
    import json
    root = os.path.join(HERE, "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    sys.path.insert(0, HERE)
    import npc_status
    return npc_status._sdb_rates("StageDifficultySettingList.db", name, "schema_StageDifficultySetting.json").get("Exp", 1.0)


def result(run, clear_exp, enemy_total, difficulty="NORMAL", party_size=1):
    si = score_info()
    kills = run["kill_count"]
    avg = run["score"] / kills if kills else 0.0
    perfect = run["hits_taken"] <= si["perfect"].get(party_size, 0) and run["deaths"] == 0
    bonus_avg = avg
    if perfect:
        bonus_avg *= 1.0 + si["perfect_bonus"]
    bonus_avg *= 1.0 - _lookup(si["dead"], run["deaths"], 0.0) if run["deaths"] else 1.0
    rank, gold_rate, exp_rate = grade_for(bonus_avg)
    normal, rate = _difficulty_rate("NORMAL"), _difficulty_rate(difficulty)
    base = math.ceil(clear_exp * normal)
    diff = math.floor(clear_exp * (rate - normal))
    rank_exp = math.floor(clear_exp * exp_rate)
    all_clear = math.floor(ALL_SECTOR_CLEAR_BONUS_RATE * base) if enemy_total and kills >= enemy_total else 0
    return {"Score": run["score"], "KillCount": kills, "GotHitCount": run["hits_taken"], "DeadCount": run["deaths"], "IsPerfect": 1 if perfect else 0,
            "AverageScore": avg, "BonusAverageScore": bonus_avg, "Grade": rank, "GoldRate": gold_rate,
            "TotalExp": base + diff + rank_exp + all_clear, "BaseExp": base, "StageDiffExp": diff, "RankExp": rank_exp, "AllSectorClearExp": all_clear}


def enemy_total(stage_id):
    sys.path.insert(0, HERE)
    import npc_ai
    import sdb_stage
    n = 0

    def ok(npc):
        i = npc_ai.npc_info(npc)
        return countable(i) and not i.get("Ignorable")
    for sec in sdb_stage.stage_info(stage_id).get("Sectors", []):
        for p in sec.get("PopInfos") or []:
            if ok(p["NpcId"]):
                n += 1
        for trig in sec.get("AdditionalNpcPopInfos") or []:
            for inf in trig.get("Infos", []):
                groups = inf.get("PopGroupInfos") or []
                if groups:
                    rep = (inf.get("Repeat") or {}).get("Count", 1)
                    n += rep * sum(1 for p in groups[0]["PopInfos"] if ok(p["NpcId"]))
    return n
