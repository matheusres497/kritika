import functools
import os

import lua51

SCRIPT = __import__("config").ALP_DIR + os.sep + "Script"


@functools.lru_cache(maxsize=1)
def _vm():
    vm = lua51.LuaVM()
    for f in ("Constants.luac", "Core.luac", "Formula/NpcStatus.luac", "Formula/UpdateStatus.luac"):
        vm.run_file(os.path.join(SCRIPT, f))
    return vm


class _Actor:

    def __init__(self, level, rank, cls, race):
        self.v = {"GetLevel": float(level), "GetRank": rank, "GetClassType": cls, "GetRace": race}
        self.out = {}

    def __getattr__(self, name):
        def method(_self, *a):
            if name.startswith("Set"):
                self.out[a[0] if len(a) > 1 else name[3:]] = a[-1]
                return []
            if name.startswith("Has"):
                return [False]
            return [self.v.get(name)]
        return method


@functools.lru_cache(maxsize=512)
def _calc(level, rank, cls, race, difficulty):
    a = _Actor(level, rank, cls, race)
    _vm().call(_vm().globals.get("CalculateNpcStatus"), a, difficulty)
    return dict(a.out)


def stats(npc_id, level=1, difficulty="NORMAL"):
    import npc_ai
    info = npc_ai.npc_info(npc_id)
    lv = max(1, int(level) + int(info.get("DeltaLevel") or 0))
    out = _calc(lv, info.get("Rank") or "NORMAL", info.get("ClassType") or "WARRIOR", info.get("RaceId") or "",
                difficulty)
    out["Level"] = lv
    return out


if __name__ == "__main__":
    import sys
    print(stats(sys.argv[1] if len(sys.argv) > 1 else "Troll_Ba", int(sys.argv[2]) if len(sys.argv) > 2 else 1))


import struct as _struct


def _f32(x):
    return _struct.unpack("<f", _struct.pack("<f", x))[0]


@functools.lru_cache(maxsize=None)
def _sdb_rates(db, key, schema):
    import json, sys
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import sdb_decode
    sch = json.load(open(os.path.join(root, "server", "schemas", schema)))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record(db, key), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    return dict(out.get("StatusIncreaseRate") or [])


@functools.lru_cache(maxsize=None)
def _stage_fields(stage_id):
    import json, sys
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sys.path.insert(0, os.path.join(root, "tools"))
    import sdb_decode
    sch = json.load(open(os.path.join(root, "server", "schemas", "schema_StageInfo_full.json")))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("StageList.db", stage_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    rl = out.get("RecommandLevel")
    if rl:
        rl = _struct.unpack("<2I", _struct.pack("<2f", *rl))
    return dict(out.get("MonsterStatusBonus") or []), rl, int(out.get("Level") or 0)


def stage_npc_level(stage_id):
    rl = _stage_fields(stage_id)[1]
    return (rl[0] + rl[1]) // 2 if rl and rl[1] >= rl[0] > 0 else None


def client_npc_level(stage_id):
    lv = _stage_fields(stage_id)[2]
    return lv if lv > 0 else None


def scaled_max_hp(npc_id, stage_id, difficulty="NORMAL"):
    import npc_ai
    level = client_npc_level(stage_id) if stage_id else None
    if not level:
        return None
    hp = int(stats(npc_id, level, difficulty)["MaxHp"])
    race = _sdb_rates("RaceList.db", npc_ai.npc_info(npc_id).get("RaceId") or "", "schema_RaceInfo.json")
    diff = _sdb_rates("StageDifficultySettingList.db", difficulty, "schema_StageDifficultySetting.json")
    bonus = _stage_fields(stage_id)[0]
    for f in (race.get("MaxHp", 1.0), diff.get("MaxHp", 1.0), 1.0,
              bonus.get("MaxHp", 1.0)):
        hp = int(hp * _f32(f))
    return destructible_hp(npc_id, hp)


def destructible_hp(npc_id, hp):
    import npc_ai
    import sdb_stage
    info = npc_ai.npc_info(npc_id)
    if info.get("RaceId") != "Destructibleobject" or hp < int(sdb_stage.general_info("DestructibleObjectActivationHp")):
        return hp
    status = dict(info.get("DefaultStatus") or [])
    if "Destructibleobject_hp" in status:
        return int(float(status["Destructibleobject_hp"]))
    return int(sdb_stage.general_info("DestructibleObjectDefaultHp"))


def scaled_stats(npc_id, stage_id, difficulty="NORMAL"):
    import npc_ai
    level = client_npc_level(stage_id) if stage_id else None
    if not level:
        return {}
    base = stats(npc_id, level, difficulty)
    race = _sdb_rates("RaceList.db", npc_ai.npc_info(npc_id).get("RaceId") or "", "schema_RaceInfo.json")
    diff = _sdb_rates("StageDifficultySettingList.db", difficulty, "schema_StageDifficultySetting.json")
    bonus = _stage_fields(stage_id)[0]
    out = {}
    for name in ("AttackPower", "Armor"):
        v = int(base[name])
        for f in (race.get(name, 1.0), diff.get(name, 1.0), 1.0, bonus.get(name, 1.0)):
            v = int(v * _f32(f))
        out[name] = v
    return out


def npc_exp(npc_id, stage_id, difficulty="NORMAL"):
    level = client_npc_level(stage_id) if stage_id else None
    if not level:
        return 0
    base = int(stats(npc_id, level, difficulty).get("Exp") or 0)
    diff = _sdb_rates("StageDifficultySettingList.db", difficulty, "schema_StageDifficultySetting.json")
    return int(base * _f32(diff.get("Exp", 1.0)))
