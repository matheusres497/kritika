import json
import os
import struct
import sys

import protocol

HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(HERE, "..", "tools")


NO_EXPIRY_TICKS = 271389657600000000


NO_LOCK_PENDING_TICKS = 15778454400000000

_INFO = {}


def item_info(item_id):
    if item_id not in _INFO:
        if _TOOLS not in sys.path:
            sys.path.insert(0, _TOOLS)
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_ItemInfo.json"))))
        rec = sdb_decode.record("ItemList.db", item_id)
        if rec is None:
            raise KeyError(f"item {item_id} not in ItemList.db")
        out = {}
        sdb_decode.fields(bytes(rec), 0, sch["fields"], out, 10 ** 6)
        _INFO[item_id] = out
    return _INFO[item_id]


def item_hash(item_id):
    h = 0
    for c in struct.unpack("<%dH" % len(item_id), item_id.encode("utf-16-le")):
        h = (h * 0x1003F + c) & 0xFFFFFFFF
    return h


def write_item(w, tag, item_id, serial, grade=None, stack=1):
    info = item_info(item_id)
    eq = info.get("EquipInfo") or {}
    if eq.get("BaseStatusId"):
        raise ValueError(f"{item_id}: BaseStatusId set, rolled base stats are not implemented")
    dur = float(eq.get("MaxDurability") or 0.0)
    w.field_tag(tag).begin(protocol.h16("Apple::Item"))
    w.u16(0)
    w.u32(item_hash(item_id))
    w.i64(serial)
    w.u32(stack)
    w.wstr(grade or info["Grade"])
    w.raw(struct.pack("<f", dur))
    w.empty_container("Status")
    w.empty_container("EnhanceFactors")
    w.empty_container("IncreaseStatusList", "array")
    w.empty_container("Categories")
    import eventoptions
    eventoptions.write_options(w, eventoptions.item_options(info, serial))
    w.u32(0)
    w.empty_container("IdentifiedOptions", "array")
    w.empty_container("Essences", "array")
    w.empty_container("Enchants")
    w.u8(1 if eq.get("Sealed") else 0)
    w.u8(0)


    w.u8(1 if (eq.get("EquipPart") not in (None, "", "NONE") and not eq.get("Identified", 1)) else 0)
    w.u8(1)
    w.u32(0)
    w.u8(0)
    w.datetime("ItemLockExpirationTime", NO_LOCK_PENDING_TICKS)
    w.wstr("")
    w.wstr(info["BindType"])
    w.datetime("ExpirationTime", NO_EXPIRY_TICKS)
    w.u8(0)
    w.wstr("")
    w.wstr("")
    w.empty_container("DyePartColors")
    w.raw(struct.pack("<f", dur))
    w.u8(0)
    w.wstr("NONE")


EQUIP_PART_SLOT_TAB0 = {"MAIN_WEAPON": 0, "HEAD": 1, "UPPER": 2, "LOWER": 3, "HAND": 4, "FOOT": 5, "NECK": 6, "EAR": 7,
                        "FINGER": 8}
EQUIP_SLOTS_PER_TAB = [22, 14, 14, 8]


def default_equipment(class_enum):
    if _TOOLS not in sys.path:
        sys.path.insert(0, _TOOLS)
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_ClassInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("ClassList.db", class_enum), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    return {part: tuple(v) for part, v in (out.get("DefaultEquipment") or [])}


def player_equipment(player, class_enum):
    items = player.setdefault("items", {})
    if "equip" not in items:
        eq = {}
        for part, (item_id, grade) in default_equipment(class_enum).items():
            slot = EQUIP_PART_SLOT_TAB0[part]
            items["next_serial"] = items.get("next_serial", 1)
            eq[f"0,{slot}"] = [item_id, items["next_serial"], grade]
            items["next_serial"] += 1
        items["equip"] = eq
    return {tuple(int(x) for x in k.split(",")): tuple(v) for k, v in items["equip"].items()}


def equip_items(player, class_enum):
    import schema_writer
    eq = player_equipment(player, class_enum)
    w = protocol.Writer()
    w.field_tag("EquipItems").begin(protocol.h16("array")).u32(len(EQUIP_SLOTS_PER_TAB))
    for t, n in enumerate(EQUIP_SLOTS_PER_TAB):
        w.field_tag(f"item_{t}").begin(protocol.h16("array")).u32(n)
        for s in range(n):
            if (t, s) in eq:
                item_id, serial, grade = eq[(t, s)]
                write_item(w, f"item_{s}", item_id, serial, grade)
            else:
                schema_writer.empty_item(w, f"item_{s}")
    return bytes(w.buf)


ITEM_GRADE_BONUS = {"LOW": 0, "MIDDLE": 1, "HIGH": 4, "RARE": 6}


def item_stats(item_id, grade=None):
    if _TOOLS not in sys.path:
        sys.path.insert(0, _TOOLS)
    import itemstatus
    info = item_info(item_id)
    eq = info.get("EquipInfo") or {}
    out = {}
    if not eq.get("Status"):
        return out
    level = int(info.get("ItemLevel") or 1) + ITEM_GRADE_BONUS.get(grade or info.get("Grade"), 0)
    table = itemstatus.by_level().get(level) or {}
    for stat, mult in eq["Status"]:


        v = table[stat] * float(mult) if stat in table else float(mult)
        out[stat] = round(v, 4)
    return out


_MASTERY = {}


def mastery_info(mastery_id):
    if mastery_id not in _MASTERY:
        if _TOOLS not in sys.path:
            sys.path.insert(0, _TOOLS)
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_Apple_Sdb_MasteryStatusInfo.json"))))
        rec = sdb_decode.record("MasteryStatusInfo.db", mastery_id)
        out = None
        if rec is not None:
            out = {}
            try:
                sdb_decode.fields(bytes(rec), 0, sch["fields"], out, 10 ** 6)
            except Exception as e:
                out["__stopped__"] = repr(e)
        _MASTERY[mastery_id] = out
    return _MASTERY[mastery_id]


def mastery_stats(player, class_enum):
    equip = player_equipment(player, class_enum)
    total = {}
    for (_tab, _slot), (item_id, _serial, _grade) in equip.items():
        for mid in ((item_info(item_id).get("EquipInfo") or {}).get("MasteryStatusIds") or []):
            m = mastery_info(mid)
            el = (m or {}).get("EquipmentElement") or {}
            if not el.get("AddStatuses"):
                continue
            slot = EQUIP_PART_SLOT_TAB0.get(el.get("EquipPart"))
            target = next((v for (t, sl), v in equip.items() if t == 0 and sl == slot), None) if slot is not None else None
            if target is None:
                continue
            t_info = item_info(target[0])
            t_grade = target[2] if len(target) > 2 and target[2] else t_info.get("Grade")
            if el.get("EquipCategories") and (t_info.get("EquipInfo") or {}).get("EquipCategory") not in el["EquipCategories"]:
                continue
            if el.get("ItemGrades") and t_grade not in el["ItemGrades"]:
                continue
            for st in el["AddStatuses"]:
                if st.get("IncreaseType") not in (None, "ADD"):
                    continue
                total[st["Id"]] = round(total.get(st["Id"], 0.0) + float(st["Value"]), 4)
    return total


def set_stats(player, class_enum):
    import eventoptions
    counts = {}
    for (_tab, _slot), (item_id, _serial, _grade) in player_equipment(player, class_enum).items():
        sid = (item_info(item_id).get("EquipInfo") or {}).get("EquipmentSetInfoId")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    total = {}
    for sid, n in counts.items():
        for eff in (eventoptions.set_info(sid) or {}).get("EquipEffectInfos") or []:
            if n >= int(eff.get("MinEquipedCount") or 0):
                for st in eff.get("IncreaseStatusList") or []:
                    if st.get("IncreaseType") in (None, "ADD"):
                        total[st["Id"]] = round(total.get(st["Id"], 0.0) + float(st["Value"]), 4)
    return total


def equipment_stats(player, class_enum):
    total = {}
    for (_tab, _slot), (item_id, _serial, grade) in player_equipment(player, class_enum).items():
        for stat, v in item_stats(item_id, grade).items():
            total[stat] = round(total.get(stat, 0.0) + v, 4)
    for extra in (mastery_stats(player, class_enum), set_stats(player, class_enum)):
        for stat, v in extra.items():
            total[stat] = round(total.get(stat, 0.0) + v, 4)
    return total
