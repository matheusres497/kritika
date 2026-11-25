import json
import os
import random
import struct
import sys
import time

import protocol

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")

PARENT_ITEM = "ITEM"
RESULT_OK, RESULT_FAILED = 0, 1

_INFO = {}
_SETS = {}


def x65599(s):
    h = 0
    for c in struct.unpack("<%dH" % len(s), s.encode("utf-16-le")):
        h = (h * 0x1003F + c) & 0xFFFFFFFF
    return h


def _decode(db, key, schema_file, cache):
    if key not in cache:
        if TOOLS not in sys.path:
            sys.path.insert(0, TOOLS)
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", schema_file))))
        rec = sdb_decode.record(db, key)
        out = None
        if rec is not None:
            out = {}
            try:
                sdb_decode.fields(bytes(rec), 0, sch["fields"], out, 10 ** 6)
            except Exception as e:
                out["__stopped__"] = repr(e)
        cache[key] = out
    return cache[key]


def option_info(option_id):
    return _decode("EventOptionInfoList.db", option_id, "schema_EventOptionInfo.json", _INFO)


def set_info(set_id):
    return _decode("EquipmentSetInfoList.db", set_id, "schema_Apple_Sdb_EquipmentSetInfo.json", _SETS)


def _roll_params(option_id, seed):
    info = option_info(option_id) or {}
    rng = random.Random(f"{seed}:{option_id}")
    out = {}
    for name, rng_pair in (info.get("Script") or {}).get("Params") or []:
        lo, hi = float(rng_pair[0]), float(rng_pair[1])
        out[name] = round(lo + rng.random() * (hi - lo), 4)
    return out


def make_option(option_id, serial, seed):
    return {"id": option_id, "hash": x65599(option_id), "serial": serial, "params": _roll_params(option_id, seed), "parent": PARENT_ITEM}


def item_options(item_info, item_serial):
    ids = ((item_info or {}).get("EquipInfo") or {}).get("EventOptionList") or []
    return [make_option(oid, (int(item_serial) << 5) | i, item_serial) for i, oid in enumerate(ids) if option_info(oid)]


def write_option(w, tag, o):
    w.field_tag(tag).begin(protocol.h16("Apple::EventOption"))
    w.u32(o["hash"])
    w.wstr("")
    w.u32(0)
    w.field_tag("Params").begin(protocol.h16("associative_array")).u32(len(o["params"]))
    for k in sorted(o["params"]):
        w.wstr(k).raw(struct.pack("<f", o["params"][k]))
    w.wstr(o.get("parent", PARENT_ITEM))
    w.u32(o["serial"])
    w.u8(0)
    w.wstr("")
    w.raw(struct.pack("<f", 0.0))


def write_options(w, options, field="EventOptions"):
    w.field_tag(field).begin(protocol.h16("array")).u32(len(options))
    for i, o in enumerate(options):
        write_option(w, f"item_{i}", o)


def equipped(player, class_enum):
    import items
    out = []
    for (_tab, _slot), (item_id, serial, _grade) in items.player_equipment(player, class_enum).items():
        try:
            out.append((item_id, serial, items.item_info(item_id)))
        except Exception:
            pass
    return out


def active_options(player, class_enum):
    gear = equipped(player, class_enum)
    active = {}
    for item_id, serial, info in gear:
        for o in item_options(info, serial):
            active[o["serial"]] = o
    counts = {}
    for item_id, serial, info in gear:
        sid = ((info or {}).get("EquipInfo") or {}).get("EquipmentSetInfoId")
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    blocked = set()
    for sid, n in counts.items():
        sinfo = set_info(sid)
        if not sinfo:
            continue
        for i, eff in enumerate(sinfo.get("EquipEffectInfos") or []):
            if n >= int(eff.get("MinEquipedCount") or 0):
                for j, oid in enumerate(eff.get("EventOptionIds") or []):
                    if option_info(oid):
                        ser = 0x40000000 | (x65599(f"{sid}:{i}:{j}") & 0x3FFFFFFF)
                        active[ser] = make_option(oid, ser, ser)
                blocked |= set(eff.get("BlockedEventOptionIds") or [])
    if blocked:
        active = {s: o for s, o in active.items() if o["id"] not in blocked}
    return active


def change_packet(actor_serial, old_serials, new_options):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::ActorSystem::EventOptionChangePacket"))
    w.u32(actor_serial)
    w.field_tag("OldEventOptionSerials").begin(protocol.h16("array")).u32(len(old_serials))
    for s in sorted(old_serials):
        w.u32(s)
    write_options(w, new_options, "NewEventOptions")
    return protocol.packet(bytes(w.buf))


def diff_packets(actor_serial, before, after):
    removed = [s for s in before if s not in after]
    added = [after[s] for s in after if s not in before]
    if not removed and not added:
        return []
    return [change_packet(actor_serial, removed, added)]


def parse_verify_request(plain):
    off = 6 + 4
    n = struct.unpack_from("<I", plain, off)[0]
    off += 4
    out = []
    for _ in range(n):
        off += 4
        serial = struct.unpack_from("<I", plain, off)[0]
        oid, off = protocol.read_wstr(plain, off + 4)
        out.append((serial, oid))
    return out


def verify_result_packet(results):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::ActionSystem::VerifyEventOptionResult"))
    w.field_tag("Results").begin(protocol.h16("array")).u32(len(results))
    for i, (serial, result) in enumerate(results):
        w.field_tag(f"item_{i}").begin(protocol.h16("pair")).u32(serial).raw(struct.pack("<i", result))
    return protocol.packet(bytes(w.buf))


def verify(plain, log, state, now=None, rng=None):
    now = time.monotonic() if now is None else now
    rng = rng or random
    reqs = parse_verify_request(plain)
    active = state.get("event_options", {})
    cooldown = state.setdefault("event_option_ready", {})
    results = []
    for serial, oid in reqs:
        o = active.get(serial)
        info = option_info(oid) if o and o["id"] == oid else None
        if info is None:
            results.append((serial, RESULT_FAILED))
            log.write(f"VerifyEventOption {serial} {oid}: not active -> refused (extra)")
            continue
        cond = info.get("Condition") or {}
        if now < cooldown.get(serial, 0.0):
            results.append((serial, RESULT_FAILED))
            continue
        if rng.random() < float(cond.get("Probability") if cond.get("Probability") is not None else 1.0):
            cooldown[serial] = now + float(cond.get("Duration") or 0.0)
            results.append((serial, RESULT_OK))
            log.write(f"VerifyEventOption {serial} {oid}: verified, cooldown {cond.get('Duration')} s (extra)")
        else:
            results.append((serial, RESULT_FAILED))
    return [verify_result_packet(results)]


_BUFFS = {}
_CHANGE_HP = {}


def _buff_info(buff_id):
    return _decode("BuffList.db", buff_id, "schema_Apple_Sdb_BuffInfo.json", _BUFFS)


def buff_id_for_hash(state, buff_hash):
    for o in (state.get("event_options") or {}).values():
        for b in (option_info(o["id"]) or {}).get("Buffs") or []:
            if x65599(b["BuffId"]) == buff_hash:
                return b["BuffId"]
    return None


def buff_status_delta(buff_id):
    sdi = (_buff_info(buff_id) or {}).get("StatusDeltaInfoId")
    if not sdi:
        return None
    d = _decode("StatusDeltaInfo.db", sdi, "schema_Apple_Sdb_StatusDeltaInfo.json", {})
    if not d or d.get("StatusDeltaType") != "ADD":
        return None
    return {k: float(v) for k, v in (d.get("NumberParams") or [])} or None


def buff_change_hp(buff_id):
    if buff_id in _CHANGE_HP:
        return _CHANGE_HP[buff_id]
    out = None
    info_id = ((_buff_info(buff_id) or {}).get("ChangeHpValidation") or {}).get("ChangeHpInfoId")
    if info_id:
        ch = _decode("ChangeHpInfo.db", info_id, "schema_Apple_Sdb_ChangeHpInfo.json", {})
        if ch and ch.get("ChangeHpType") == "MAX_HP":
            rate = dict(ch.get("NumberParams") or []).get("addDamageRate")
            if rate:
                out = (info_id, float(rate))
    _CHANGE_HP[buff_id] = out
    return out
