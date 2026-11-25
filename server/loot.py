import json
import os
import random
import struct
import sys
import threading

import protocol
import items

HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(HERE, "..", "tools")
DATA = os.path.join(HERE, "data")

INVENTORY_TABS = ["EQUIPMENT_TAB", "CONSUME_TAB", "QUEST_TAB", "MATERIAL_TAB", "AVATAR_TAB", "JEWEL_TAB", "PET_TAB",
                  "PET_CONSUME_TAB", "PET_MATERIAL_TAB", "PET_EQUIPMENT_TAB", "PET_AVATAR_TAB", "CRYSTAL_TAB"]


REASON_DROP, REASON_STAGE, REASON_EQUIP, REASON_MOVE, REASON_SWAP = 2, 6, 100, 101, 102

INVENTORY_SLOTS = 24


def _int_of_float(v):
    return struct.unpack("<i", struct.pack("<f", v))[0] if isinstance(v, float) and 0 < abs(v) < 1e-30 else int(v)


_RULES = {}


def drop_rule(rule_id, rank, difficulty="NORMAL"):
    key = (rule_id, rank, difficulty)
    if key not in _RULES:
        if _TOOLS not in sys.path:
            sys.path.insert(0, _TOOLS)
        import sqlite3
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_Apple_Sdb_ItemDropRuleInfo.json"))))
        c = sqlite3.connect(f"file:{os.path.join(sdb_decode.SDB, 'DropRuleInfos.db')}?mode=ro", uri=True)
        k = struct.pack("<I", len(rule_id) + 1) + (rule_id + "\0").encode("utf-16-le")
        _RULES[key] = None
        for (b,) in c.execute("select SerializedBinary from DropRuleInfos"):
            b = bytes(b)
            if not b.startswith(k):
                continue
            out = {}
            try:
                sdb_decode.fields(b, 0, sch["fields"], out, 10 ** 6)
            except Exception:
                continue
            if out.get("Rank") == rank and out.get("Difficulty") == difficulty:
                _RULES[key] = out
                break
    return _RULES[key]


_GOLD = {}


def gold_range(stage_level, rank):
    if not _GOLD:
        b = open(os.path.join(__import__("config").ALP_DIR, "Database", "StageRewardGoldInfo.b"), "rb").read()
        n = struct.unpack_from("<I", b, 0)[0]
        o = 4
        for _ in range(n):
            lvl = struct.unpack_from("<I", b, o)[0]
            o += 4
            k = struct.unpack_from("<I", b, o)[0]
            rk = b[o + 4:o + 4 + 2 * k].decode("utf-16-le").rstrip("\0")
            o += 4 + 2 * k
            lo, hi = struct.unpack_from("<ff", b, o)
            o += 8
            _GOLD[(lvl, rk)] = (lo, hi)
    return _GOLD.get((stage_level, rank))


def _equipment_pool():
    path = os.path.join(DATA, "item_pool.json")
    if os.path.exists(path):
        return json.load(open(path))
    if _TOOLS not in sys.path:
        sys.path.insert(0, _TOOLS)
    import sqlite3
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_ItemInfo.json"))))
    c = sqlite3.connect(f"file:{os.path.join(sdb_decode.SDB, 'ItemList.db')}?mode=ro", uri=True)
    out = []
    marker = "EQUIPMENT_TAB".encode("utf-16-le")
    for (b,) in c.execute("select SerializedBinary from ItemList"):
        b = bytes(b)
        if marker not in b:
            continue
        o = {}
        try:
            sdb_decode.fields(b, 0, sch["fields"], o, 60)
        except Exception:
            continue
        eq = o.get("EquipInfo") or {}
        out.append([o.get("Id"), eq.get("EquipPart"), o.get("Grade"), o.get("ItemLevel"), o.get("NoDropFromStageClear"),
                    eq.get("EquipCategory")])
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(path, "w"))
    return out


_POOL_LOCK = threading.Lock()


def equipment_pool():
    with _POOL_LOCK:
        return _equipment_pool()


def class_equip_categories(class_enum):
    if _TOOLS not in sys.path:
        sys.path.insert(0, _TOOLS)
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_ClassInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("ClassList.db", class_enum), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    return set(out.get("EquipCategories") or [])


def pick_equipment(rule_part, grade, level_range, class_enum, rng):
    cats = class_equip_categories(class_enum)
    lo, hi = level_range
    pool = [x[0] for x in equipment_pool()
            if x[1] == rule_part and x[2] == grade and x[3] is not None and lo <= x[3] <= hi and not x[4] and x[5] in cats]
    return rng.choice(pool) if pool else None


def _pick_weighted(options, rng):
    tot = sum(max(0.0, w) for _, w in options)
    if tot <= 0:
        return None
    r = rng.uniform(0, tot)
    for v, w in options:
        r -= max(0.0, w)
        if r <= 0:
            return v
    return options[-1][0]


def _equip_drop(eq, level_range, class_enum, rng):
    part = _pick_weighted([(p["EquipPart"], p["Probability"]) for p in eq.get("EquipPartProbabilities", [])], rng)
    grade = _pick_weighted([(g["ItemGrade"], g["Probability"]) for g in eq.get("GradeProbability", [])], rng)
    return pick_equipment(part, grade, level_range, class_enum, rng) if part and grade else None


def roll_reward(rule_id, rank, stage_level, level_range, class_enum, rng=random):
    rule = drop_rule(rule_id, rank)
    out = []
    if rule is None:
        return out
    sure = rule.get("SurelyDropInfo") or {}
    seq = sure.get("Equipable") or {}
    for _ in range(int(_int_of_float(seq.get("DropCount", [0, 0])[0]) if seq.get("DropCount") else 0)):
        it = _equip_drop(seq, level_range, class_enum, rng)
        if it:
            out.append(("item", it))
    rnd = rule.get("RandomDropInfo") or {}
    for _ in range(int(rnd.get("DropCount") or 0)):
        pick = _pick_weighted([("nothing", rnd.get("DropNothingProbability") or 0.0),
                               ("gold", (rnd.get("Gold") or {}).get("Probability") or 0.0),
                               ("equip", (rnd.get("Equipable") or {}).get("Probability") or 0.0)], rng)
        if pick == "gold":
            gr = gold_range(stage_level, rank)
            if gr:
                out.append(("gold", int(rng.randint(int(gr[0]), int(gr[1])))))
        elif pick == "equip":
            it = _equip_drop(rnd["Equipable"], level_range, class_enum, rng)
            if it:
                out.append(("item", it))
    return out


def inventory(player):
    inv = player.setdefault("items", {}).setdefault("inventory", {})
    player["items"].setdefault("gold", 0)
    return inv


def add_item(player, item_id, stack=1):
    info = items.item_info(item_id)
    tab = INVENTORY_TABS.index(info["InventoryTab"])
    inv = inventory(player)
    slots = inv.setdefault(str(tab), {})
    slot = next(s for s in range(INVENTORY_SLOTS) if str(s) not in slots)
    ser = player["items"]["next_serial"] = player["items"].get("next_serial", 1) + 1
    slots[str(slot)] = [item_id, ser, stack]
    return tab, slot, ser, info["Grade"]


def set_item_packet(tab, slot, item_id, serial, stack=1, reason=REASON_STAGE, old_item_id="", grade=None):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::InventorySystem::SetItem"))
    w.u32(tab)
    items.write_item(w, "ItemInfo", item_id, serial, grade=grade, stack=stack)
    w.u16(slot).u32(stack).u32(reason).wstr(old_item_id)
    return protocol.packet(bytes(w.buf))


def clear_slot_packet(tab, slot, old_item_id, stack, reason=REASON_MOVE):
    import schema_writer
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::InventorySystem::SetItem"))
    w.u32(tab)
    schema_writer.empty_item(w, "ItemInfo")
    w.u16(slot).u32((-stack) & 0xFFFFFFFF).u32(reason).wstr(old_item_id)
    return protocol.packet(bytes(w.buf))


def _set(tab, slot, rec, reason, old=""):
    return set_item_packet(tab, slot, rec[0], rec[1], rec[2], reason, old, rec[3] if len(rec) > 3 else None)


def _with(rec, stack):
    return [rec[0], rec[1], stack] + list(rec[3:])


def move_item(player, tab, src, dst):
    slots = inventory(player).setdefault(str(tab), {})
    if str(src) not in slots or not (0 <= dst < INVENTORY_SLOTS) or src == dst:
        return None
    s = slots[str(src)]
    if str(dst) not in slots:
        del slots[str(src)]
        slots[str(dst)] = s
        return [clear_slot_packet(tab, src, s[0], s[2]), _set(tab, dst, s, REASON_MOVE)]
    d = slots[str(dst)]
    room = int(items.item_info(s[0]).get("MaxStackCount") or 1) - d[2]
    if d[0] == s[0] and room > 0:
        moved = min(room, s[2])
        slots[str(dst)] = _with(d, d[2] + moved)
        if moved == s[2]:
            del slots[str(src)]
            first = clear_slot_packet(tab, src, s[0], s[2])
        else:
            slots[str(src)] = _with(s, s[2] - moved)
            first = _set(tab, src, slots[str(src)], REASON_MOVE)
        return [first, _set(tab, dst, slots[str(dst)], REASON_MOVE)]
    slots[str(src)], slots[str(dst)] = d, s
    return [_set(tab, src, d, REASON_SWAP, s[0]), _set(tab, dst, s, REASON_SWAP, d[0])]


def remove_item(player, tab, slot, count):
    slots = inventory(player).setdefault(str(tab), {})
    if str(slot) not in slots:
        return None
    rec = slots[str(slot)]
    count = min(max(count, 1), rec[2])
    if count >= rec[2]:
        del slots[str(slot)]
        return [clear_slot_packet(tab, slot, rec[0], rec[2], REASON_DROP)]
    slots[str(slot)] = _with(rec, rec[2] - count)
    return [_set(tab, slot, slots[str(slot)], REASON_DROP)]


def write_gold_item(w, tag, amount):
    w.field_tag(tag).begin(protocol.h16("Apple::Item"))
    w.u16(0).u32(items.item_hash("Gold")).i64(0).u32(1).wstr("LOW").raw(struct.pack("<f", 0.0))
    for n, k in (("Status", "associative_array"), ("EnhanceFactors", "associative_array"), ("IncreaseStatusList", "array"),
                 ("Categories", "associative_array"), ("EventOptions", "array")):
        w.empty_container(n, k)
    w.u32(0)
    for n, k in (("IdentifiedOptions", "array"), ("Essences", "array"), ("Enchants", "associative_array")):
        w.empty_container(n, k)
    w.u8(0).u8(0).u8(0).u8(0).u32(amount).u8(0)
    w.datetime("ItemLockExpirationTime", items.NO_LOCK_PENDING_TICKS).wstr("").wstr("UNDEFINED")
    w.datetime("ExpirationTime", items.NO_EXPIRY_TICKS)
    w.u8(0).wstr("").wstr("")
    w.empty_container("DyePartColors")
    w.raw(struct.pack("<f", 0.0)).u8(0).wstr("NONE")


def stage_reward_packet(player_name, rewards, index, serials):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::StageReward"))
    w.field_tag("RewardItems").begin(protocol.h16("array")).u32(1)


    w.field_tag("item_0").begin(protocol.h16("pair"))
    w.wstr(player_name)
    w.field_tag("value").begin(protocol.h16("array")).u32(len(rewards))
    for i, (kind, val) in enumerate(rewards):
        w.field_tag(f"item_{i}").begin(protocol.h16("Apple::StageRewardItem")).u32(0)
        if kind == "gold":
            write_gold_item(w, "Item", val)
        else:
            items.write_item(w, "Item", val, serials[i])
    w.wstr(player_name).u32(index)
    return protocol.packet(bytes(w.buf))


def _write_tab_items(w, tab, player):
    slots = inventory(player).get(str(tab), {})
    import schema_writer
    for s in range(INVENTORY_SLOTS):
        if str(s) in slots:
            item_id, serial, stack = slots[str(s)][:3]
            grade = slots[str(s)][3] if len(slots[str(s)]) > 3 else None
            items.write_item(w, f"item_{s}", item_id, serial, grade=grade, stack=stack)
        else:
            schema_writer.empty_item(w, f"item_{s}")


def inventories_nested(player, tabs):
    w = protocol.Writer()
    w.field_tag("Inventories").begin(protocol.h16("array")).u32(tabs)
    for t in range(tabs):
        w.field_tag(f"item_{t}").begin(protocol.h16("array")).u32(INVENTORY_SLOTS)
        _write_tab_items(w, t, player)
    return bytes(w.buf)


def inventory_part_items(player, tab):
    w = protocol.Writer()
    w.field_tag("Inventories").begin(protocol.h16("array")).u32(INVENTORY_SLOTS)
    _write_tab_items(w, tab, player)
    return bytes(w.buf)
