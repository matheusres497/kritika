import functools
import json
import os
import random
import sqlite3
import struct
import sys

import damage
import npc_status

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")

PVE_PLAYER_BODY_RATE = 100.0


LIVE_PROJECTILE_RATES = {
    "WarriorAtk_AAAA_Splash": 166,
}

UNMAPPED_PROJECTILE_RATE = 100


def x65599(s):
    v = 0
    for c in s.encode():
        v = (v * 65599 + c) & 0xFFFFFFFF
    return v


def _sdb():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import sdb_decode
    return sdb_decode


def _table(db):
    sdb_decode = _sdb()
    c = sqlite3.connect(f"file:{os.path.join(sdb_decode.SDB, db)}?mode=ro", uri=True)
    t = c.execute("select name from sqlite_master where type='table'").fetchone()[0]
    return c, t


@functools.lru_cache(maxsize=1)
def _projectiles():
    c, t = _table("ProjectileList.db")
    by = {}
    for h, b in c.execute(f"select Hash, SerializedBinary from {t}"):
        b = bytes(b)
        n = struct.unpack_from("<I", b, 0)[0]
        if 0 < n < 120:
            by[h & 0xFFFFFFFF] = b[4:4 + 2 * n].decode("utf-16le").rstrip("\0")
    return sorted(by), by


def projectile_from_serial(serial):
    index = (serial >> 8) & 0xFFFF
    hashes, by = _projectiles()
    if not index & 0x8000 or not 0 < (index & 0x7FFF) <= len(hashes):
        return None
    return by[hashes[(index & 0x7FFF) - 1]]


@functools.lru_cache(maxsize=2048)
def projectile_rate(projectile_id):
    sdb_decode = _sdb()
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_ProjectileInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("ProjectileList.db", projectile_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    if out.get("AttackPowerRate") is not None:
        return float(out["AttackPowerRate"]), "sdb"
    if projectile_id in LIVE_PROJECTILE_RATES:
        return float(LIVE_PROJECTILE_RATES[projectile_id]), "live"
    return float(UNMAPPED_PROJECTILE_RATE), "UNMAPPED"


@functools.lru_cache(maxsize=1)
def _skill_ids():
    c, t = _table("SkillList.db")
    out = {}
    for (b,) in c.execute(f"select SerializedBinary from {t}"):
        b = bytes(b)
        n = struct.unpack_from("<I", b, 0)[0]
        if 0 < n < 80:
            sid = b[4:4 + 2 * n].decode("utf-16le").rstrip("\0")
            out[x65599(sid)] = sid
    return out


@functools.lru_cache(maxsize=256)
def skill_detail(skill_id):
    sdb_decode = _sdb()
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_SkillInfo.json"))))
    out = {}
    sdb_decode.fields(sdb_decode.record("SkillList.db", skill_id), 0, sch["fields"], out, 10 ** 6)
    d = out["SkillDetailInfo"]
    return float(d["AttackPowerRate"]), float(d["AttackPowerRatePerLevel"])


def skill_value(skill_id, level):
    base, per_level = skill_detail(skill_id)
    return base + (max(1, level) - 1) * per_level


def rate_for(hit):
    skill_id = _skill_ids().get(hit["SkillHash"])
    if skill_id is None:
        return None, None, None
    pid = projectile_from_serial(hit["ProjectileSerial"])
    if pid is None:
        proj_rate, src = float(UNMAPPED_PROJECTILE_RATE), "UNMAPPED"
    else:
        proj_rate, src = projectile_rate(pid)
    return proj_rate * skill_value(skill_id, hit.get("SkillLevel", 1)) * PVE_PLAYER_BODY_RATE / 1e6, pid, src


def player_hit(player_stats, npc_id, npc_level, hit, rng=None):
    rate, pid, src = rate_for(hit)
    if rate is None:
        return 1, f"skill hash {hit['SkillHash']:#x} not in SkillList -> minimum damage 1"
    t = npc_status.stats(npc_id, npc_level)
    dmg = damage.hit(player_stats, t, rate, hit_type=hit["HitType"], rng=rng or random)
    return max(1, dmg), f"rate {rate:.4f} projectile {pid} ({src})"


@functools.lru_cache(maxsize=256)
def consume_mp(skill_id):
    sdb_decode = _sdb()
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_SkillInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("SkillList.db", skill_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        return 0.0
    return float((out.get("SkillDetailInfo") or {}).get("ConsumeMp") or 0.0)


def mp_cost(skill_id, level=1):
    c = consume_mp(skill_id)
    if c == 0.0:
        return 0
    base, per = skill_detail(skill_id)
    x = (base + (max(1, level) - 1) * per) * -0.01 * c
    return 1 - int(x)
