import damage


def _g(stats, name, default=0.0):
    return float(stats.get(name, default) or 0.0)


def combat_power(stats, level):
    vm = damage.vm()
    pc = float(vm.call(vm.globals.get("GetCriticalRateForPlayerInfoUI"), _g(stats, "CriticalRating"), float(level), _g(stats, "CriticalBonusRate"))[0])
    valid_ap = int(_g(stats, "AttackPower") * _g(stats, "AttackSpeed", 1.0))
    crit = pc * (_g(stats, "CriticalDamageRate", 1.5) + _g(stats, "CriticalDamageRatePlusToMonster") - 1.0) + 1.0
    return int(crit * valid_ap * (1.0 + _g(stats, "DamagePlusRate")))


def survivability(stats, level):
    vm = damage.vm()
    evade_crit = float(vm.call(vm.globals.get("GetEvadeCriticalRateForPlayerInfoUI"), _g(stats, "EvadeCriticalRating"), float(level))[0])
    resist = sum(_g(stats, k) for k in ("Resist_Fire", "Resist_Ice", "Resist_Elec", "Resist_Poison", "Resist_Dark")) / 5.0
    factor = (2.0 * _g(stats, "DamageMinusRate") + 1.0 + 0.25 * _g(stats, "DamageMinusRateFromSuperiorMonster")
              + 0.5 * _g(stats, "DamageMinusRateFromBossMonster") + 0.5 * _g(stats, "CriticalDamageRateMinusFromMonster") + 0.5 * evade_crit + resist)
    return _g(stats, "MaxHp") * factor + _g(stats, "Armor")
