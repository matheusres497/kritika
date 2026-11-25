import json
import os
import struct

import hit_damage

HERE = os.path.dirname(os.path.abspath(__file__))


@hit_damage.functools.lru_cache(maxsize=256)
def learn_info(skill_id):
    sdb = hit_damage._sdb()
    sch = sdb.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_SkillInfo.json"), encoding="utf-8")))
    out = {}
    try:
        sdb.fields(sdb.record("SkillList.db", skill_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        return None
    d = out.get("SkillDetailInfo") or {}
    over = {}
    for o in d.get("OverrideSkillInfoList") or []:

        lo, hi = (struct.unpack("<I", struct.pack("<f", float(v)))[0] for v in o["SkillLevelRange"])
        for lv in range(lo, hi + 1):
            over[lv] = int(o["SpToLearn"])
    return {"MaxLevel": int(d.get("MaxLevel", 1)), "MinLevelToLearn": int(d.get("MinLevelToLearn", 1)),
            "SpToLearn": int(d.get("SpToLearn", 0)), "Override": over,
            "Classes": list(out.get("UsableClassTypeList") or [])}


def sp_to_learn(info, level):
    if level > 1 and info["Override"].get(level, -1) != -1:
        return info["Override"][level]
    return info["SpToLearn"]


def cost(info, current, target):
    return sum(sp_to_learn(info, lv) for lv in range(current + 1, target + 1))


def parse_request(plain):
    skill_set = struct.unpack_from("<I", plain, 6)[0]
    count = struct.unpack_from("<I", plain, 14)[0]
    o, out = 18, []
    for _ in range(count):
        h = struct.unpack_from("<I", plain, o)[0]
        lv, second = struct.unpack_from("<ih", plain, o + 8)
        out.append((h, lv, second))
        o += 14
    return skill_set, out
