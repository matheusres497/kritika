import protocol
import items
import loot

EQUIP_TABS = {"BASIC": 0}
EQUIP_PART_BY_SLOT = {v: k for k, v in items.EQUIP_PART_SLOT_TAB0.items()}
CHANGE_EQUIP, CHANGE_UNEQUIP = "EQUIP", "UNEQUIP"


def _result_packet(name, result=0):
    w = protocol.Writer().begin(protocol.registered_id(f"Apple::Protocol::FromServer::InventorySystem::{name}"))
    w.u32(result)
    w.datetime("BlockExpiration", items.NO_LOCK_PENDING_TICKS)
    return protocol.packet(bytes(w.buf))


def _set_equipment(player_name, serial, change, tab, slot, rec):
    import schema_writer
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::InventorySystem::SetEquipment"))
    w.wstr(player_name).u32(serial).wstr(change)
    w.field_tag("EquipInfo").begin(protocol.h16("associative_array")).u32(1)
    w.field_tag("item_first_0").begin(protocol.h16("pair")).u16(tab).u16(slot)
    if rec:
        items.write_item(w, "item_second_0", rec[0], rec[1], grade=rec[2])
    else:
        schema_writer.empty_item(w, "item_second_0")
    w.empty_container("OldSubAvatarSlots")
    w.empty_container("NewSubAvatarSlots")
    w.empty_container("AvatarSetVisualEffectInfos", "array")
    return protocol.packet(bytes(w.buf))


def parse(plain):
    equip_tab, off = protocol.read_wstr(plain, 6)
    import struct
    slot = struct.unpack_from("<H", plain, off)[0]
    inv_tab, off = protocol.read_wstr(plain, off + 2)
    inv_slot = struct.unpack_from("<H", plain, off)[0]
    return equip_tab, slot, inv_tab, inv_slot


def equip(player, class_enum, serial, equip_tab, slot, inv_tab, inv_slot):
    if equip_tab not in EQUIP_TABS or inv_tab not in loot.INVENTORY_TABS:
        return None
    tab = loot.INVENTORY_TABS.index(inv_tab)
    slots = loot.inventory(player).setdefault(str(tab), {})
    rec = slots.get(str(inv_slot))
    if rec is None:
        return None
    part = (items.item_info(rec[0]).get("EquipInfo") or {}).get("EquipPart")
    if items.EQUIP_PART_SLOT_TAB0.get(part) != slot:
        return None
    eq = items.player_equipment(player, class_enum)
    store = player["items"]["equip"]
    old = store.get(f"0,{slot}")
    grade = rec[3] if len(rec) > 3 else items.item_info(rec[0])["Grade"]
    store[f"0,{slot}"] = [rec[0], rec[1], grade]
    if old:
        slots[str(inv_slot)] = [old[0], old[1], 1, old[2]]
        inv_pkt = loot._set(tab, inv_slot, slots[str(inv_slot)], loot.REASON_EQUIP, rec[0])
    else:
        del slots[str(inv_slot)]
        inv_pkt = loot.clear_slot_packet(tab, inv_slot, rec[0], rec[2], loot.REASON_EQUIP)
    return [_result_packet("EquipItemResult"), _set_equipment(player["name"], serial, CHANGE_EQUIP, 0, slot, store[f"0,{slot}"]), inv_pkt]


def unequip(player, class_enum, serial, equip_tab, slot, inv_tab, inv_slot):
    if equip_tab not in EQUIP_TABS or inv_tab not in loot.INVENTORY_TABS:
        return None
    tab = loot.INVENTORY_TABS.index(inv_tab)
    slots = loot.inventory(player).setdefault(str(tab), {})
    items.player_equipment(player, class_enum)
    store = player["items"]["equip"]
    old = store.get(f"0,{slot}")
    if old is None or str(inv_slot) in slots or not (0 <= inv_slot < loot.INVENTORY_SLOTS):
        return None
    del store[f"0,{slot}"]
    slots[str(inv_slot)] = [old[0], old[1], 1, old[2]]
    return [_result_packet("UnequipItemResult"), _set_equipment(player["name"], serial, CHANGE_UNEQUIP, 0, slot, None),
            loot._set(tab, inv_slot, slots[str(inv_slot)], loot.REASON_EQUIP)]
