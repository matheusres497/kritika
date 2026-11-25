import functools
import os
import random

import lua51

SCRIPT = __import__("config").ALP_DIR + os.sep + "Script"
HIT_TYPES = {"HIT_TYPE_CRITICAL": 2.0, "HIT_TYPE_BACK_ATTACK": 4.0, "HIT_TYPE_BLEED_ATTACK": 8.0,
             "HIT_TYPE_DAMAGE_REDUCE": 64.0}


@functools.lru_cache(maxsize=1)
def vm():
    v = lua51.LuaVM()
    for f in ("Constants.luac", "Core.luac", "Formula/RequiredValueTable.luac", "Formula/GetBackAttackDamageRate.luac",
              "Formula/GetCriticalDamageRate.luac", "Formula/VerifyHit.luac", "Formula/GetDamage.luac",
              "Formula/PlayerStatus.luac", "Formula/NpcStatus.luac", "Formula/UpdateStatus.luac"):
        v.run_file(os.path.join(SCRIPT, f))
    for k, val in HIT_TYPES.items():
        v.globals.set(k, val)
    v.globals.set("DBL_EPSILON", 2.220446049250313e-16)

    v.globals.set("GetAttackPowerAdjustmentRateByTierAttackPower", lambda *a: [1.0])
    v.globals.set("InfoOut", lambda *a: [])
    return v


class Actor:

    def __init__(self, stats, object_type, cls="WARRIOR", rank="NORMAL", race="", rng=None):
        self.s = dict(stats)
        self.meta = {"GetObjectType": object_type, "GetClassType": cls, "GetRank": rank, "GetRace": race}
        self.rng = rng or random

    def __getattr__(self, name):
        s, m = self.s, self.meta

        def f(_self, *a):
            if name in m:
                return [m[name]]
            if name == "IsNil":
                return [False]


            if name == "IsRequestFromPlayer":
                return [False]
            if name == "GetLevel":
                return [float(s.get("Level", 1))]
            if name in ("GetValidAttackPower", "GetBasicAttackPower"):
                return [float(s.get("AttackPower", 0))]
            if name == "GetHp":
                return [float(s.get("Hp", s.get("MaxHp", 1)))]
            if name == "GetValidArmor":
                return [float(s.get("Armor", 0))]
            if name in ("GetTierAttackPower", "GetFixedAttackPower", "GetActiveBuffDuplicationCount", "GetBuffLevel",
                        "GetSkillLevel"):
                return [0.0]
            if name == "InternalRandom":
                return [self.rng.random()]
            if name in ("GetFloat", "GetInt", "GetInt64"):
                v = s.get(a[0], 0)
                return [float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0]
            if name == "GetString":
                v = s.get(a[0])
                return [v if isinstance(v, str) else ("NONE" if a[0] == "DefenceState" else "")]
            if name == "GetBoolean":
                return [bool(s.get(a[0], False))]
            if name in ("HasStatus", "HasDefaultStatus"):
                return [a[0] in s]
            if name == "HasBuff":
                return [False]
            if name.startswith("Set"):
                s[a[0] if len(a) > 1 else name[3:]] = a[-1]
                return []
            return [None]
        return f


class Projectile:
    def __init__(self, fixed=-1):
        self.fixed = fixed

    def __getattr__(self, name):
        return lambda _self, *a: {"IsNil": [False], "GetFixedDamage": [float(self.fixed)], "IsReflectDamage": [False],
                                  "IsAlwaysCritical": [False], "HasDefaultStatus": [False]}.get(name, [None])


def player_stats(level=1, cls="WARRIOR"):
    a = Actor({"Level": level}, "PLAYER", cls)
    vm().call(vm().globals.get("CalculatePlayerStatus"), a)
    out = dict(a.s)
    out["Level"] = level
    return out


def hit(att, dfn, rate=1.0, hit_type=0, att_type="PLAYER", dfn_type="NPC", att_meta=None, dfn_meta=None,
        counter=False, super_armor=False, rng=None):
    am, dm = att_meta or {}, dfn_meta or {}
    A = Actor(att, att_type, am.get("cls", "WARRIOR"), am.get("rank", "NORMAL"), am.get("race", ""), rng)
    D = Actor(dfn, dfn_type, dm.get("cls", "WARRIOR"), dm.get("rank", "NORMAL"), dm.get("race", ""), rng)
    opts = lua51.LuaTable({"isPvp": False, "isMyAttacker": att_type == "PLAYER", "isHitSuperArmor": super_armor,
                           "isCounterAttack": counter})
    proj_info = lua51.LuaTable({"damagePlusRateToSuperArmor": 0.0, "damagePlusRateForCounter": 0.0})
    attrs = lua51.LuaTable()

    r = vm().call(vm().globals.get("GetDamage"), A, None, D, proj_info, float(hit_type), opts, Projectile(), float(rate),
                  attrs)
    return int(r[0]) if r else 0


if __name__ == "__main__":
    import npc_status
    p = player_stats(1)
    t = npc_status.stats("Troll_Ba", 1)
    print("player lv1:", {k: p.get(k) for k in ("MaxHp", "AttackPower", "Armor", "HitRating", "DamageFloatingRange")})
    print("troll  lv1:", {k: t.get(k) for k in ("MaxHp", "AttackPower", "Armor", "HitRating", "DamageFloatingRange")})
    rng = random.Random(1)
    print("player -> troll (100%):", [hit(p, t, 1.0, rng=rng) for _ in range(8)])
    print("player -> troll (133%):", [hit(p, t, 1.33, rng=rng) for _ in range(8)])
    print("player -> troll crit  :", [hit(p, t, 1.0, hit_type=2, rng=rng) for _ in range(4)])
    print("troll -> player (100%):", [hit(t, p, 1.0, att_type="NPC", dfn_type="PLAYER", rng=rng) for _ in range(8)])
