import os
import sys

import protocol
import schema_writer


SEND_STAGE_CREATE_PLAYER = True
SEND_ALL_LOAD_END = True
SEND_SECTOR_NPCS = True


JOIN_VILLAGE_FIRST_PLAY = 1


def _jsonl(name):
    import json, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", name)
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.startswith("{")]


def class_info(class_enum):
    return next((c for c in _jsonl("classinfo_runtime.jsonl") if c["Id"] == class_enum), None)


def stage_starting_sector(stage_id):
    st = next((s for s in _jsonl("stageinfo_runtime.jsonl") if s["Id"] == stage_id), None)
    return st["StartingSectorId"] if st else None


def stage_id_from_hash(stage_hash):
    import sqlite3
    import sdb_stage
    db = sqlite3.connect(f"file:{os.path.join(sdb_stage.SDB_DIR, 'StageList.db')}?mode=ro", uri=True)
    row = db.execute("select SerializedBinary from StageList where Hash = ?",
                     (stage_hash - (1 << 32) if stage_hash >= (1 << 31) else stage_hash,)).fetchone()
    if not row:
        return None
    b = bytes(row[0])
    _, o = sdb_stage._wstr(b, 0)
    sid, _ = sdb_stage._wstr(b, o)
    return sid


def selected_player(state):
    import stub_server
    players = stub_server.load_players(state.get("account_id", ""))
    return next((x for x in players if x["name"] == state.get("player")), players[0] if players else None)


def msg(name):
    return protocol.msg_id(name)


CLASS_ACTOR_IDS = {"SIDECHARACTER1": "SideCharacter1", "SIDECHARACTER2": "SideCharacter2",
                   "SIDECHARACTER3": "SideCharacter3", "SIDECHARACTER4": "SideCharacter4"}


def class_actor_id(class_enum):
    return CLASS_ACTOR_IDS.get(class_enum, class_enum.title().replace("_", ""))


def one_empty_title():
    w = protocol.Writer()

    w.field_tag("SelectedTitleIds").begin(protocol.h16("array")).u32(3).wstr("").wstr("").wstr("")
    return bytes(w.buf)


_CLASS_DEFAULTS = {}


def _class_defaults(class_enum):
    if class_enum not in _CLASS_DEFAULTS:
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        tools = os.path.join(here, "..", "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(here, "schemas", "schema_ClassInfo.json"))))
        out = {}
        try:
            sdb_decode.fields(sdb_decode.record("ClassList.db", class_enum), 0, sch["fields"], out, 10 ** 6)
        except Exception:
            pass
        skills = list(out.get("DefaultSkills") or [])
        quick = {int(k): (v["QuickSlotType"], v["Command"]) for k, v in (out.get("DefaultQuickSlot") or [])}
        _CLASS_DEFAULTS[class_enum] = (skills, quick)
    return _CLASS_DEFAULTS[class_enum]


def default_skills(class_enum):
    return _class_defaults(class_enum)[0] if class_enum else []


_MASTERY_CLASSES = {}


def normal_atk_mastery_for(class_enum):
    if not _MASTERY_CLASSES:
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        tools = os.path.join(here, "..", "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(here, "schemas", "schema_SkillInfo.json"))))
        out = {}
        try:
            sdb_decode.fields(sdb_decode.record("SkillList.db", "NormalAtkMastery"), 0, sch["fields"], out, 10 ** 6)
        except Exception:
            pass
        _MASTERY_CLASSES["ok"] = set(out.get("UsableClassTypeList") or [])
    return ["NormalAtkMastery"] if class_enum in _MASTERY_CLASSES["ok"] else []


def default_quickslots(class_enum):
    return _class_defaults(class_enum)[1] if class_enum else {}


def quick_slots(class_enum=None, tabs=4, player=None):
    w = protocol.Writer()
    w.field_tag("QuickSlots").begin(protocol.h16("array")).u32(tabs)
    for t in range(tabs):
        saved = (player or {}).get("quickslots", {}).get(str(t))
        if saved is not None:
            slots = sorted((int(k), tuple(v)) for k, v in saved.items())
        else:
            slots = sorted(default_quickslots(class_enum).items()) if t == 0 else []
        w.field_tag(f"item_{t}").begin(protocol.h16("associative_array")).u32(len(slots))
        for i, (slot, (kind, cmd)) in enumerate(slots):
            w.u32(slot)
            w.field_tag(f"item_second_{i}").begin(protocol.h16("Apple::QuickSlot")).wstr(kind).wstr(cmd)
    return bytes(w.buf)


def write_skill(w, tag, skill_id, level=1):
    import struct
    w.field_tag(tag).begin(protocol.h16("Apple::Skill")).wstr(skill_id).u32(level)
    w.field_tag("EventOptions").begin(protocol.h16("array")).u32(0)
    w.u32(0).i64(0).raw(struct.pack("<f", 0.0)).u32(0).raw(struct.pack("<h", 0))


def skill_levels(class_enum, p=None):
    out = {sid: 1 for sid in (default_skills(class_enum) + normal_atk_mastery_for(class_enum))} if class_enum else {}
    out.update((p or {}).get("skills", {}))
    return out


def skills_one_tab(class_enum=None, p=None):
    w = protocol.Writer()
    sets = (0, 1, 8, 9)
    w.field_tag("Skills").begin(protocol.h16("associative_array")).u32(len(sets))
    for i, k in enumerate(sets):
        w.u32(k)
        levels = skill_levels(class_enum, p) if k == 0 else {}
        w.field_tag(f"item_second_{i}").begin(protocol.h16("array")).u32(len(levels))
        for j, (sid, lv) in enumerate(levels.items()):
            write_skill(w, f"item_{j}", sid, lv)
    return bytes(w.buf)


def spawn_position(state):
    import struct
    import sdb_stage
    info = sdb_stage.stage_info(state.get("stage_id", "Ep01Stage01"))
    a, b = info.get("StartingPosition", [0.0, 0.0])
    rect = info.get("StartingRectangle") or [0.0, 0.0, 0.0, 0.0]
    if (a, b) == (0.0, 0.0) and any(rect):


        a, b = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return struct.pack("<3f", a, 0.0, b)


def spawn_direction(state):
    import struct
    import sdb_stage
    return struct.pack("<3f", *sdb_stage.stage_info(state.get("stage_id", "Ep01Stage01")).get("StartingDirection", [1.0, 0.0, 0.0]))


def status_hash(name):
    h = 0
    for c in name.lower().encode():
        h = (h * 65599 + c) & 0xFFFFFFFF
    return h


STATUS_FLOATS = {"MoveSpeed": 1.0, "AttackSpeed": 1.0}


def status_container(floats=None, typed=None):
    import struct
    items = {}
    for n, v in (STATUS_FLOATS if floats is None and typed is None else (floats or {})).items():
        items[n] = ("Float", v)
    items.update(typed or {})
    arrays = {"Float": [], "Int": [], "Int64": [], "Bool": [], "String": []}
    keys = []
    for name, (kind, v) in items.items():
        arrays[kind].append(v)
        keys.append((status_hash(name), kind.upper(), len(arrays[kind]) - 1))
    keys.sort()
    w = protocol.Writer()
    w.field_tag("Status").begin(protocol.h16("Apple::Logic::EncryptedStatusContainer"))


    w.field_tag("StringContainer").begin(protocol.h16("array")).u32(len(arrays["String"]))
    for v in arrays["String"]:
        w.str8(str(v))
    w.field_tag("FloatContainer").begin(protocol.h16("array")).u32(len(arrays["Float"]))
    for v in arrays["Float"]:
        w.u32(~struct.unpack("<I", struct.pack("<f", float(v)))[0] & 0xFFFFFFFF)
    w.field_tag("IntContainer").begin(protocol.h16("array")).u32(len(arrays["Int"]))
    for v in arrays["Int"]:
        w.u32(~int(v) & 0xFFFFFFFF)


    w.field_tag("BoolContainer").begin(protocol.h16("array")).u32(len(arrays["Bool"]))
    for v in arrays["Bool"]:
        w.u8(1 if v else 0)
    w.field_tag("VectorContainer").begin(protocol.h16("array")).u32(0)
    w.field_tag("Int64Container").begin(protocol.h16("array")).u32(len(arrays["Int64"]))
    for v in arrays["Int64"]:
        w.raw(struct.pack("<Q", ~int(v) & 0xFFFFFFFFFFFFFFFF))
    w.field_tag("KeyDataContainer").begin(protocol.h16("array")).u32(len(keys))
    for i, (h, kind, idx) in enumerate(keys):
        w.field_tag(f"item_{i}").begin(protocol.h16("Apple::Logic::KeyData")).u32(h).wstr(kind).u32(idx)
    return bytes(w.buf)


def player_statuses(level=1, cls="WARRIOR"):
    import damage

    class Rec:
        def __init__(self):
            self.out = {}

        def __getattr__(self, n):
            def f(_s, *a):
                if n.startswith("Set") and len(a) > 1:
                    self.out[a[0]] = (n[3:], a[-1])
                    return []
                if n.startswith("Set"):
                    return []
                return {"GetLevel": [float(level)], "GetClassType": [cls], "GetObjectType": ["PLAYER"]}.get(n, [None])
            return f
    vm = damage.vm()
    r = Rec()
    vm.call(vm.globals.get("CalculatePlayerStatus"), r)
    vm.call(vm.globals.get("CommonStatusInit"), r)
    return {k: v for k, v in r.out.items() if v[0] in ("Float", "Int", "Int64")}


_STATUS_TYPES = {}


def declared_status_type(name):
    if not _STATUS_TYPES:
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(__import__("config").ALP_DIR, "Database", "StatusType.xml")
        t = open(path, encoding="utf-8-sig").read()
        for k, v in re.findall(r'<Key type="wstring" value="([^"]*)" />\s*<StatusType type="StatusType" value="(\w+)" />', t):
            _STATUS_TYPES[k] = v
    return _STATUS_TYPES.get(name)


def class_default_statuses(cls):
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    tools = os.path.join(here, "..", "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sdb_decode
    sch = sdb_decode.full_form(json.load(open(os.path.join(here, "schemas", "schema_ClassInfo.json"))))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("ClassList.db", cls), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    res = {}
    for name, val in (out.get("DefaultStatus") or []):
        kind = declared_status_type(name)
        if kind == "Boolean":
            kind = "Bool"
        if kind is None:
            kind = "Bool" if val in ("true", "false") else "Int" if val.lstrip("-").isdigit() else "String"
        if kind == "Bool":
            res[name] = ("Bool", val == "true")
        elif kind == "Int":
            res[name] = ("Int", int(float(val)))
        elif kind == "Float":
            res[name] = ("Float", float(val))
        elif kind == "Int64":
            res[name] = ("Int64", int(float(val)))
        else:
            res[name] = ("String", val)
    return res


def stage_create_player(stub, state):
    import stub_server
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), players[0] if players else None)
    if p is None:
        return []
    en = schema_writer.enums()
    import damage
    cls_name = en["classType"]["values"].get(str(p["class"]), "WARRIOR")
    level = int(p.get("level", 1))


    base = damage.player_stats(level, cls_name)
    max_hp, max_mp = player_max_hp(p, cls_name, level), player_max_mp(p, cls_name, level)
    state["player_serial"], state["player_hp"], state["player_mp"] = 1, max_hp, max_mp
    import eventoptions
    state["event_options"] = eventoptions.active_options(p, cls_name)
    state.pop("buff_deltas", None)
    try:
        ps = {k: float(v) for k, v in damage.player_stats(level, cls_name).items() if isinstance(v, (int, float))}
        for k, (kind, v) in equipment_status_totals(p, cls_name, level).items():
            ps[k] = float(v)
        ps["MaxHp"] = float(max_hp)
        _TEST_PLAYER_STATS.clear()
        _TEST_PLAYER_STATS.update(ps)
    except Exception:
        pass
    ov = {
        "Player.Serial": 1,


        "Player.ActorId": p["name"],

        "Player.Team": "PLAYER",

        "Player.ActionListId": (class_info(en["classType"]["values"].get(str(p["class"]), "")) or {}).get("ActionListId", ""),
        "Player.DefaultActionId": (class_info(en["classType"]["values"].get(str(p["class"]), "")) or {}).get("StageDefaultActionId", ""),
        "Player.Level": p.get("level", 1),

        "Player.Hp": max_hp, "Player.MaxHp": max_hp, "Player.Mp": max_mp, "Player.MaxMp": max_mp,
        "Player.IsControllable": 1,
        "SelectedSkillTabIndex": 0,


        "Skills": skills_one_tab(en["classType"]["values"].get(str(p["class"]), ""), p),


        "Player.SelectedTitleIds": one_empty_title(),
        "Player.Scale.value": bytes.fromhex("0000803f0000803f0000803f"),
        "Player.Position.value": spawn_position(state),

        "Status": status_container(typed=dict(dict(player_statuses(level, cls_name), **equipment_status_totals(p, cls_name, level)),
                                              **class_default_statuses(cls_name))),

        "Player.Direction.value": spawn_direction(state),
        "Player.PlayerSerial": p.get("serial", p.get("slot", 0) + 1),
        "Player.GameSps": game_sps_container(p),
        "Player.GameOverClockSps": game_overclock_sps_container(),
        "Player.ClassType": en["classType"]["values"].get(str(p["class"]), "WARRIOR"),
        "Player.CustomizingData": bytes.fromhex(p["customizing"]),
    }
    out = [schema_writer.build_message("CreatePlayer_StageSystem_FromServer",
                                       "Apple::Protocol::FromServer::StageSystem::CreatePlayer", ov)]


    if state.get("event_options"):
        import eventoptions
        out.append(eventoptions.change_packet(1, [], list(state["event_options"].values())))
    return out


QUICKSLOT_TYPES = {0: "ITEM", 1: "SKILL", 2: "EVENT_OPTION"}


NPC_HP, NPC_LEVEL = 300, 1


CLIENT_AI = os.environ.get("KRITIKA_CLIENT_AI", "1") != "0"


def npc_max_hp(npc_id, level=NPC_LEVEL, stage_id=None):
    if os.environ.get("KRITIKA_NPC_HP"):
        return int(os.environ["KRITIKA_NPC_HP"])
    try:
        import npc_status


        scaled = npc_status.scaled_max_hp(npc_id, stage_id) if stage_id else None
        if scaled:
            return scaled
        return max(1, npc_status.destructible_hp(npc_id, int(npc_status.stats(npc_id, level)["MaxHp"])))
    except Exception:
        return NPC_HP


def set_stage_master(serial=1):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::SetStageMaster"))
    return protocol.packet(bytes(w.u32(serial).buf))


_TEST_PLAYER_STATS = {}


def test_enemy_attack_power(npc_id, stage_id, target):
    import random
    import damage
    import npc_status
    level = npc_status.client_npc_level(stage_id)
    if not level or not _TEST_PLAYER_STATS:
        return None
    att = dict(npc_status.stats(npc_id, level))
    probe_ap = 100000
    att["AttackPower"] = probe_ap
    rng = random.Random(7)
    dmg = sum(damage.hit(att, _TEST_PLAYER_STATS, 1.0, hit_type=0, att_type="NPC", dfn_type="PLAYER", att_meta={"cls": "WARRIOR"}, dfn_meta={"cls": "MAGICGUNNER"}, rng=rng)
              for _ in range(40)) / 40.0
    return int(probe_ap * float(target) / dmg * TEST_ENEMY_AP_FACTOR) if dmg > 0 else None


def create_npc(pop, serial, master_serial=0, owner_serial=0, stage_id=None):
    import struct, random, npc_ai
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::CreateNpc"))
    w.wstr(pop["NpcId"]).wstr(pop.get("TeamId") or "ENEMY").wstr(pop["DefaultActionId"]).wstr(pop["LocationId"])


    w.u32(serial).u32(master_serial).u32(0).u32(owner_serial)
    hp = npc_max_hp(pop["NpcId"], stage_id=stage_id)
    w.raw(struct.pack("<Q", hp)).u32(100).raw(struct.pack("<Q", hp)).u32(100)


    import npc_status


    lvl = (npc_status.client_npc_level(stage_id) if stage_id else None) or NPC_LEVEL
    lvl = max(1, lvl + int(npc_ai.npc_info(pop["NpcId"]).get("DeltaLevel") or 0))
    w.u32(random.getrandbits(32)).u32(lvl).u32(0xFFFFFFFF)
    w.raw(struct.pack("<2f", *pop["Position"])).raw(struct.pack("<3f", *pop["Direction"]))
    import npc_ai
    w.raw(struct.pack("<3f", *(npc_ai.npc_info(pop["NpcId"]).get("Scale") or (1.0, 1.0, 1.0))))


    ninfo = npc_ai.npc_info(pop["NpcId"])
    import sdb_stage
    is_obj = pop["NpcId"] in (sdb_stage.stage_info(stage_id).get("StageObjectNpcs") or []) if stage_id else False
    sdb_flags = os.environ.get("KRITIKA_NPC_SDB_FLAGS", "1") != "0"
    w.raw(bytes([1 if sdb_flags and ninfo.get("DropItemWhenDie") else 0, 1 if sdb_flags and ninfo.get("GiveExpWhenDie") else 0,
                 0, 0, 0, 1, 1 if is_obj else 0, 0, 0, 0, 0]))


    nst = npc_status.scaled_stats(pop["NpcId"], stage_id) if stage_id else {}
    if stage_id and os.path.exists(TEST_ENEMY_DAMAGE_FLAG):
        try:
            target = int(open(TEST_ENEMY_DAMAGE_FLAG).read().split()[0])
        except Exception:
            target = 100
        ap = test_enemy_attack_power(pop["NpcId"], stage_id, target)
        if ap:
            nst = dict(nst, AttackPower=ap)
    w.raw(status_container(typed={k: ("Int", v) for k, v in nst.items()} or None))
    w.field_tag("StatusBundle").begin(protocol.h16("associative_array")).u32(0)
    w.field_tag("Buffs").begin(protocol.h16("Apple::BuffContainer"))
    w.field_tag("List").begin(protocol.h16("array")).u32(0)
    w.u32(1)
    return protocol.packet(bytes(w.buf))


FIXED_NPC_SERIAL_OFFSET = 70


def create_fixed_npc(fx, serial):
    import struct
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::CreateNpc"))
    w.wstr(fx["Id"]).wstr("NPC").wstr("").wstr("")
    w.u32(serial).u32(0).u32(0).u32(0)
    w.raw(struct.pack("<Q", 1)).u32(100).raw(struct.pack("<Q", 1)).u32(100)
    w.u32(0).u32(1).u32(0xFFFFFFFF)
    w.raw(struct.pack("<2f", *fx["Position"])).raw(struct.pack("<3f", *fx["Direction"])).raw(struct.pack("<3f", *(fx.get("Scale") or (1.0, 1.0, 1.0))))


    w.raw(bytes([0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]))
    w.raw(status_container(floats={}, typed={}))
    w.field_tag("StatusBundle").begin(protocol.h16("associative_array")).u32(0)
    w.field_tag("Buffs").begin(protocol.h16("Apple::BuffContainer"))
    w.field_tag("List").begin(protocol.h16("array")).u32(0)
    w.u32(1)
    return protocol.packet(bytes(w.buf))


def fixed_npc_packets(state, stage_id, sector_id):
    import sdb_stage
    sec = next((s for s in sdb_stage.stage_info(stage_id).get("Sectors", []) if s["Id"] == sector_id), {})
    base = sector_serial_base(sector_id) + FIXED_NPC_SERIAL_OFFSET
    fixed = list(sec.get("FixedNpcs") or [])


    by_quest = {}
    entries = sec.get("FixedNpcsByQuest") or []
    if entries:
        import quests
        _players, p = quests._player(state)
        if p is not None:
            q = quests.state_of(p)
            lvl = p.get("level", 1)
            for e in entries:
                lo, hi = e.get("level") or [0, 999]
                kind, qid = e.get("QuestType"), e.get("QuestId")
                rec = q["accepted"].get(qid)
                ok = {"QUEST_ACCEPT": rec is not None, "QUEST_COMPLETABLE": bool(rec and rec["completable"]),
                      "QUEST_COMPLETE": qid in q["completed"]}.get(kind, False)
                if ok and lo <= lvl <= hi and e.get("MinimumDifficulty") in (None, "NONE", "NORMAL"):
                    for fx in e.get("FixedNpcs") or []:
                        by_quest[fx["Id"]] = fx if e.get("Visible") else None
    shown = [fx for fx in by_quest.values() if fx is not None]
    fixed += shown
    state["fixed_npcs"] = {base + i: fx["Id"] for i, fx in enumerate(fixed)}
    return [create_fixed_npc(fx, base + i) for i, fx in enumerate(fixed)]


def location_condition_achieve(plain, log, state):
    import sdb_stage
    event_id, _ = protocol.read_wstr(plain, 6)
    stage = state.get("stage_id", "Ep01Stage01")
    sec_id = state.get("sector_id", "Sector01")
    sec = next((s for s in sdb_stage.stage_info(stage).get("Sectors", []) if s["Id"] == sec_id), {})
    ev = next((e for e in sec.get("SectorSpecificEvents", []) if e["Id"] == event_id), None)
    done = state.setdefault("sector_events_done", {})
    out, result = [], 1
    if ev is not None and done.get(event_id, 0) < int((ev.get("Repeat") or {}).get("Count") or 1):
        done[event_id] = done.get(event_id, 0) + 1
        result = 0
        for mon in (ev.get("Achieve") or {}).get("CreateMonsterInfos") or []:
            serial = state.get("next_pop_serial", sector_serial_base(sec_id) + 50)
            pop = {"NpcId": mon["NpcId"], "TeamId": mon.get("TeamId") or "NPC", "DefaultActionId": mon.get("DefaultActionId") or "", "LocationId": "",
                   "Position": mon["Position"], "Direction": mon["Direction"]}
            out.append(create_npc(pop, serial, 0, 1 if CLIENT_AI else 0, stage))
            state.setdefault("npcs", {})[serial] = mon["NpcId"]
            state["next_pop_serial"] = serial + 1
        if (ev.get("Achieve") or {}).get("Buffs"):
            log.write(f"SectorEvent {event_id}: Achieve.Buffs not applied: {ev['Achieve']['Buffs']}")
    log.write(f"LocationConditionAchieve {event_id} -> result {result}, {len(out)} npc(s) (extra)")
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::SectorSpecificEventSystem::LocationConditionAchieveResult"))
    w.u32(result).wstr(event_id)
    return [protocol.packet(bytes(w.buf))] + out


def is_village(stage_id):
    import sdb_stage
    return (sdb_stage.stage_info(stage_id).get("StageType") or "") == "VILLAGE"


def sector_serial_base(sector_id):
    import re
    m = re.search(r"(\d+)$", sector_id or "")
    return 100 * (int(m.group(1)) if m else 1)


def sector_npcs(state, stage_id, sector_id):
    import sdb_stage
    import npc_ai


    sec = next((s for s in sdb_stage.stage_info(stage_id).get("Sectors", []) if s["Id"] == sector_id), {})
    pops = sec.get("PopInfos", [])
    out = []
    base = sector_serial_base(sector_id)


    mem = state.get("sector_memory", {}).get(sector_id)
    keep = [i for i in range(len(pops)) if mem is None or base + i in mem["alive"]]
    for i in keep:
        out.append(create_npc(pops[i], base + i, 0, 1 if CLIENT_AI else 0, stage_id))
    if CLIENT_AI:
        out.append(set_stage_master(1))
    state["npcs"] = {base + i: pops[i]["NpcId"] for i in keep}
    state["next_pop_serial"] = base + len(pops)
    state["sector_id"] = sector_id
    was_cleared = bool(mem and mem["cleared"])


    state["sector_monsters"] = {base + i for i in keep if not int(npc_ai.npc_info(pops[i]["NpcId"]).get("Ignorable") or 0)}


    state["pops_pending"] = set() if was_cleared else set(range(len(sec.get("AdditionalNpcPopInfos", []))))
    state["sector_cleared"] = was_cleared
    state["sector_got_hit"] = 0
    import npc_ai
    state["ai"] = {} if CLIENT_AI else {base + i: npc_ai.Npc(base + i, pops[i]["NpcId"], pops[i]["Position"]) for i in keep
                   if pops[i]["LocationId"]}
    state["npc_hp"] = {}
    state["sector_events_done"] = {}
    out += fixed_npc_packets(state, stage_id, sector_id)
    return out


def set_quick_slot(plain, log, state):
    import struct
    import stub_server
    index = struct.unpack_from("<I", plain, 6)[0]
    cmd, off = protocol.read_wstr(plain, 10)
    tab, kind = struct.unpack_from("<HI", plain, off)
    kind_name = QUICKSLOT_TYPES.get(kind, "SKILL")
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is not None:
        bar = p.setdefault("quickslots", {})
        if str(tab) not in bar:
            bar[str(tab)] = {str(k): list(v) for k, v in
                             (default_quickslots(schema_writer.enums()["classType"]["values"].get(str(p["class"]), ""))
                              .items() if tab == 0 else [])}
        if cmd:
            bar[str(tab)][str(index)] = [kind_name, cmd]
        else:
            bar[str(tab)].pop(str(index), None)
        stub_server.save_players(state.get("account_id", ""), players)
    log.write(f"SetQuickSlot tab={tab} index={index} {kind_name} {cmd!r} (extra)")
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::ActorSystem::SetQuickSlot"))
    w.u32(index).field_tag("QuickSlot").begin(protocol.h16("Apple::QuickSlot")).wstr(kind_name).wstr(cmd)
    w.u16(tab)
    return [protocol.packet(bytes(w.buf))]


def parse_projectile_hits(plain):
    import struct
    o = 6 + 2 + 2
    n = struct.unpack_from("<I", plain, o)[0]; o += 4
    hits = []
    for i in range(n):
        o += 2 + 2
        proj, dfn, att = struct.unpack_from("<III", plain, o); o += 12
        res, o = protocol.read_wstr(plain, o)
        htype, dhp = struct.unpack_from("<IQ", plain, o); o += 12
        o += 2 + 2 + 8
        o += 2 + 2
        cnt = struct.unpack_from("<I", plain, o)[0]; o += 4 + 4 * cnt
        reduce_rate = struct.unpack_from("<f", plain, o)[0]
        o += 4 + 3
        act, skh, lvl, _coll, _buff = struct.unpack_from("<IIiII", plain, o); o += 20
        o += 2
        o += 2 + 2
        cnt = struct.unpack_from("<I", plain, o)[0]; o += 4 + 4 * cnt
        hits.append({"ProjectileSerial": proj, "DefenderSerial": dfn, "AttackerSerial": att, "HitResult": res,
                     "HitType": htype, "DeltaHp": dhp, "ActionHash": act, "SkillHash": skh, "SkillLevel": lvl, "ReduceRate": reduce_rate})
    return hits


PLAYER_MAX_HP = 100
TEST_ENEMY_AP_FACTOR = 1.449
TEST_ENEMY_DAMAGE_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "TEST_ENEMY_DAMAGE.flag")
TEST_INFINITE_NPC_HP_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "TEST_INFINITE_NPC_HP.flag")


def change_hp_result(target, new_hp, attacker=0, is_hit=True, show_ui=True, info_hash=0):
    import struct
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::Both::StatusSystem::ChangeHpResult"))
    w.u32(target).raw(struct.pack("<Q", max(0, int(new_hp)))).raw(bytes([1 if is_hit else 0, 1 if show_ui else 0]))
    w.u32(attacker)
    if attacker:
        w.u32(info_hash)
    return protocol.packet(bytes(w.buf))


def destroy_actors_reply(plain, log, state):
    import struct
    import npc_ai
    o = 6 + 2 + 2
    n = struct.unpack_from("<I", plain, o)[0]
    serials = list(struct.unpack_from(f"<{n}I", plain, o + 4))
    last = state.get("last_attacker", {})
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::DestroyActors"))
    w.field_tag("Serials").begin(protocol.h16("array")).u32(len(serials))
    for s in sorted(serials):
        w.u32(s)
    w.field_tag("LastAttackedSerials").begin(protocol.h16("associative_array")).u32(len(serials))
    for s in sorted(serials):
        w.u32(s).u32(last.get(s, state.get("player_serial", 1)))
    killed = [state.get("npcs", {}).get(s) for s in serials]
    for s in serials:
        state.get("npcs", {}).pop(s, None)
        state.get("sector_monsters", set()).discard(s)
    log.write(f"DestroyActors {serials} (extra)")
    out = [protocol.packet(bytes(w.buf))]
    out += record_kills(state, killed, log)
    import quests
    out += quests.on_kills(state, list(zip(serials, killed)), log)
    if not state.get("sector_monsters") and not state.get("pops_pending") and not state.get("sector_cleared", True):
        state["sector_cleared"] = True
        out += sector_clear(state)
        log.write(f"SectorClear {state.get('sector_id')} + EnableWarpGate (extra)")
    return out


def record_kills(state, npc_ids, log):
    import npc_ai
    import sdb_stage
    run = state.get("run")
    if run is None or run.get("cleared"):
        return []
    boss = False
    objs = sdb_stage.stage_info(state.get("stage_id", "Ep01Stage01")).get("StageObjectNpcs") or []
    for nid in npc_ids:
        if not nid:
            continue
        info = npc_ai.npc_info(nid)
        if info.get("GiveExpWhenDie"):
            run["kills"].append(info.get("Rank") or "NORMAL")
        boss = boss or nid in objs
    if not boss:
        return []
    run["cleared"] = True
    killed_out = kill_remaining_enemies(state, log)
    log.write(f"StageClear {state.get('stage_id')} kills={len(run['kills'])} (extra)")
    return killed_out + stage_clear(state, log)


def kill_remaining_enemies(state, log):
    import npc_ai
    hp = state.setdefault("npc_hp", {})
    out = []
    for serial, npc_id in sorted(state.get("npcs", {}).items()):
        if int(npc_ai.npc_info(npc_id).get("Ignorable") or 0) or hp.get(serial, 1) <= 0:
            continue
        hp[serial] = 0
        state.setdefault("last_attacker", {})[serial] = 0
        out.append(change_hp_result(serial, 0, 0, is_hit=False, show_ui=False))
    if out:
        log.write(f"boss killed: {len(out)} remaining enemies die with it (extra)")
    return out


CLEAR_GRADE = "C"


def stage_clear(state, log):
    import time as _t
    import sdb_stage
    import stage_result
    run = state["run"]
    stage = state.get("stage_id", "Ep01Stage01")
    info = sdb_stage.stage_info(stage)
    res = stage_result.result(run, int(info.get("ClearExp", 0) or 0), stage_result.enemy_total(stage))
    run["grade"], run["result"] = res["Grade"], res
    p = selected_player(state)
    elapsed = _t.time() - run["start"]
    ov = {"PlayTime": elapsed, "PlayTimeByServer": elapsed, "Score": res["Score"], "AssistScore": 0, "AverageScore": float(res["AverageScore"]),
          "BonusAverageScore": float(res["BonusAverageScore"]), "KillCount": res["KillCount"], "GotHitCount": res["GotHitCount"],
          "DeadCount": res["DeadCount"], "IsPerfect": res["IsPerfect"], "Grade": res["Grade"], "Medal": "", "MvpId": p["name"] if p else "",
          "ExpReport.TotalExp": res["TotalExp"], "ExpReport.BaseExp": res["BaseExp"], "ExpReport.StageDiffExp": res["StageDiffExp"],
          "ExpReport.RankExp": res["RankExp"], "ExpReport.AllSectorClearExp": res["AllSectorClearExp"]}
    log.write(f"StageClear result {res} (extra)")
    out = [schema_writer.build_message("StageClear_StageSystem_FromServer",
                                       "Apple::Protocol::FromServer::StageSystem::StageClear", ov)]


    out += add_exp(state, res["TotalExp"], CAUSE_STAGE, f"StageClear {state.get('stage_id')} grade {res['Grade']}", log)
    return out


def licence_packets(pp, stage_ids, difficulty=1):
    import hit_damage
    import licenses
    out = [schema_writer.build_message("LicenseSet_StageSystem_FromServer", "Apple::Protocol::FromServer::StageSystem::LicenseSet",
                                       {"StageHash": hit_damage.x65599(sid), "Difficulty": difficulty, "Type": 0, "IsRemoved": 0}) for sid in stage_ids]
    return out + [licenses.set_licenses_packet(pp)]


def request_licenses(plain, log, state):
    import licenses
    pp = selected_player(state)
    log.write("RequestLicenses -> SetLicenses (extra)")
    return [licenses.set_licenses_packet(pp)] if pp else []


def equipment_status_totals(p, cls, level, stats=None):
    import items
    import damage
    base = dict(player_statuses(level, cls))
    lv = damage.player_stats(level, cls)
    base["MaxHp"], base["MaxMp"] = ("Float", float(lv["MaxHp"])), ("Float", float(lv["MaxMp"]))
    out = {}
    for stat, v in items.equipment_stats(p, cls).items():
        kind, b = base.get(stat, ("Float", 0.0))
        total = b + v
        out[stat] = (kind, int(round(total)) if kind in ("Int", "Int64") else total)
    for stat in (stats or ()):
        if stat not in out and stat in base:
            out[stat] = base[stat]
    for k in ("MaxHp", "MaxMp"):
        if k in out:
            out[k] = ("Float", float(int(out[k][1])))
    return out if stats is None else {k: v for k, v in out.items() if k in stats}


def player_max_mp(p, cls, level=None):
    import damage
    import items
    level = int(level if level is not None else p.get("level", 1))
    return int(float(damage.player_stats(level, cls)["MaxMp"]) + items.equipment_stats(p, cls).get("MaxMp", 0.0))


def shift_mp(state, old_max, new_max):
    if "player_mp" in state and old_max != new_max:
        state["player_mp"] = max(0, int(state["player_mp"]) - old_max + new_max)


def player_max_hp(p, cls, level=None):
    import damage
    import items
    level = int(level if level is not None else p.get("level", 1))
    return int(float(damage.player_stats(level, cls)["MaxHp"]) + items.equipment_stats(p, cls).get("MaxHp", 0.0))


def status_bundle_by_double(serial, values):
    import struct
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::Both::StatusSystem::ChangeStatusBundleByDouble"))
    w.u32(serial)
    w.field_tag("StatusBundle").begin(protocol.h16("associative_array")).u32(len(values))
    for key in sorted(values, key=status_hash):
        w.u32(status_hash(key)).raw(struct.pack("<d", float(values[key])))
    return protocol.packet(bytes(w.buf))


def inventory_equip(fn, plain, log, state):
    import equip
    import stub_server
    equip_tab, slot, inv_tab, inv_slot = equip.parse(plain)
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
    import copy
    import items as items_mod
    before = set(items_mod.equipment_stats(copy.deepcopy(p), cls))
    old_max_mp = player_max_mp(p, cls)
    import eventoptions
    eo_before = eventoptions.active_options(copy.deepcopy(p), cls)
    out = getattr(equip, fn)(p, cls, state.get("player_serial", 1), equip_tab, slot, inv_tab, inv_slot)
    if out is None:
        log.write(f"{fn} {equip_tab}[{slot}] <-> {inv_tab}[{inv_slot}]: not applicable (extra)")
        return []
    stub_server.save_players(state.get("account_id", ""), players)
    log.write(f"{fn} {equip_tab}[{slot}] <-> {inv_tab}[{inv_slot}] -> {len(out)} packets (extra)")


    touched = before | set(items_mod.equipment_stats(p, cls))
    totals = equipment_status_totals(p, cls, int(p.get("level", 1)), stats=touched)
    if totals:
        out = out + [status_bundle_by_double(state.get("player_serial", 1), {k: v[1] for k, v in totals.items()})]
        log.write(f"equipment statuses -> {({k: v[1] for k, v in totals.items()})} (extra)")
    if "MaxHp" in totals:


        log.write(f"MaxHp -> {int(totals['MaxHp'][1])} (the client rescales its current HP, no HP packet) (extra)")
    eo_after = eventoptions.active_options(p, cls)
    out += eventoptions.diff_packets(state.get("player_serial", 1), eo_before, eo_after)
    state["event_options"] = eo_after
    if eo_before != eo_after:
        log.write(f"event options: -{sorted(o['id'] for s2, o in eo_before.items() if s2 not in eo_after)} +{sorted(o['id'] for s2, o in eo_after.items() if s2 not in eo_before)} (extra)")
    if "MaxMp" in totals:
        shift_mp(state, old_max_mp, int(totals["MaxMp"][1]))
        log.write(f"MaxMp {old_max_mp} -> {int(totals['MaxMp'][1])}, server mp counter {state.get('player_mp')} (extra)")
    return out


def inventory_remove(plain, log, state):
    import struct
    import loot
    import stub_server
    tab_name, off = protocol.read_wstr(plain, 6)
    slot, count = struct.unpack_from("<HH", plain, off)
    if tab_name not in loot.INVENTORY_TABS:
        log.write(f"RemoveItem tab {tab_name}: not an inventory tab (extra)")
        return []
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    out = loot.remove_item(p, loot.INVENTORY_TABS.index(tab_name), slot, count) if p else None
    if out is None:
        log.write(f"RemoveItem {tab_name} slot={slot} count={count}: nothing to remove (extra)")
        return []
    stub_server.save_players(state.get("account_id", ""), players)
    log.write(f"RemoveItem {tab_name} slot={slot} count={count} -> {len(out)} SetItem (extra)")
    return out


def inventory_move(plain, log, state):
    import struct
    import loot
    import stub_server
    tab, src, src_count, dst, dst_count = struct.unpack_from("<IHHHH", plain, 6)
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    out = loot.move_item(p, tab, src, dst)
    if out is None:
        log.write(f"Move tab={tab} {src}->{dst} count={src_count}/{dst_count}: not applicable (extra)")
        return []
    stub_server.save_players(state.get("account_id", ""), players)
    log.write(f"Move tab={tab} {src}->{dst} count={src_count}/{dst_count} -> {len(out)} SetItem (extra)")
    return out


def stage_reward(plain, log, state):
    import struct
    import loot
    import sdb_stage
    import stub_server
    run = state.get("run") or {}
    if run.get("reward_taken") or not run.get("cleared"):
        return []
    run["reward_taken"] = True
    index = struct.unpack_from("<I", plain, 6)[0]
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    en = schema_writer.enums()
    cls = en["classType"]["values"].get(str(p["class"]), "WARRIOR")
    info = sdb_stage.stage_info(state.get("stage_id", "Ep01Stage01"))
    rule = info.get("StageClearRewardRule") or {}
    lo, hi = (loot._int_of_float(x) for x in rule.get("EquipableItemLevelRange", [1, 1]))
    cards = loot.roll_reward(rule.get("Id", ""), run.get("grade", CLEAR_GRADE), info.get("Level", 1), (lo, hi), cls)
    out, serials = [], []
    for kind, val in cards:
        if kind == "gold":
            p.setdefault("items", {})["gold"] = p["items"].get("gold", 0) + val
            serials.append(0)
        else:
            tab, slot, serial, _g = loot.add_item(p, val)
            serials.append(serial)
            out.append(loot.set_item_packet(tab, slot, val, serial))
    stub_server.save_players(state.get("account_id", ""), players)
    log.write(f"StageReward index={index} -> {cards} (extra)")
    return [loot.stage_reward_packet(p["name"], cards, index, serials)] + out


def sector_clear(state):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::SectorClear"))
    w.u32(state.get("sector_got_hit", 0)).wstr("").raw(b"\x00")
    return [protocol.packet(bytes(w.buf)), enable_warp_gate()]


def enable_warp_gate():
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::EnableWarpGate"))
    w.u32(0)
    return protocol.packet(bytes(w.buf))


def sector_load_end(log, state):
    import struct
    sector, pos, direction = state.pop("pending_sector")
    stage = state.get("stage_id", "Ep01Stage01")
    out = [schema_writer.build_message("LoadEnd_StageSystem_FromServer",
                                       "Apple::Protocol::FromServer::StageSystem::LoadEnd", {})]
    out += sector_npcs(state, stage, sector)
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::ChangeSectorLoadEnd"))
    w.u32(state.get("player_serial", 1)).raw(struct.pack("<3f", pos[0], 0.0, pos[1])).raw(struct.pack("<3f", *direction))
    out.append(protocol.packet(bytes(w.buf)))
    out.append(schema_writer.build_message("AllLoadEnd_StageSystem_FromServer",
                                           "Apple::Protocol::FromServer::StageSystem::AllLoadEnd",
                                           {"TimeStamp": 0, "ElapsedTimeForQuestTimer": 0.0, "PastSectorTime": 0.0}))
    import quests
    out += quests.on_enter_sector(state, log)
    if state.get("sector_cleared"):
        out.append(enable_warp_gate())
    log.write(f"sector LoadEnd {sector} -> {len(out)} replies (extra)")
    return out


def change_sector(plain, log, state):
    import struct, time
    import sdb_stage
    gate, _ = protocol.read_wstr(plain, 6)
    stage = state.get("stage_id", "Ep01Stage01")
    cur = state.get("sector_id", "Sector01")
    link = sdb_stage.warp_target(stage, cur, gate)
    log.write(f"ChangeSector request {cur}/{gate} -> {link} (extra)")
    if not link:
        return []
    sector, warp, pos, direction = link


    off, n = sdb_stage.sector_warp_offset(), (direction[0] ** 2 + direction[2] ** 2) ** 0.5
    if n:
        pos = (pos[0] + direction[0] / n * off, pos[1] + direction[2] / n * off)
    state.setdefault("sector_memory", {})[cur] = {"alive": set(state.get("npcs", {})), "cleared": bool(state.get("sector_cleared"))}
    state["pending_sector"] = (sector, pos, direction)
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::ChangeSector"))
    w.wstr(sector).u32(int(time.time()) & 0xFFFFFFFF).u32(state.get("player_serial", 1))
    w.raw(struct.pack("<3f", pos[0], 0.0, pos[1])).u32(0)
    return [protocol.packet(bytes(w.buf))]


def projectile_hit(plain, log, state):
    import struct
    hits = parse_projectile_hits(plain)
    hp = state.setdefault("npc_hp", {})
    me = state.get("player_serial", 1)
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::ActionSystem::ProjectileHit"))
    w.field_tag("HitInfos").begin(protocol.h16("array")).u32(len(hits))
    changed = []
    for i, h in enumerate(hits):
        delta = struct.unpack("<q", struct.pack("<Q", h["DeltaHp"]))[0]
        d = h["DefenderSerial"]


        dmg = max(0, -delta)
        state.setdefault("last_attacker", {})[d] = h["AttackerSerial"]
        try:
            run = state.get("run")
            if run is not None and not run.get("cleared"):
                import stage_result
                if d in state.get("npcs", {}) and h["AttackerSerial"] == me and hp.get(d, 1) > 0:
                    stage_result.player_hit_npc(run, d, h["HitType"], __import__("time").monotonic())
                elif d == me:
                    stage_result.player_got_hit(run)
        except Exception as e:
            log.write(f"score tracking failed on hit: {e!r} (extra)")
        if d in state.get("npcs", {}):
            hp[d] = max(0, hp.get(d, npc_max_hp(state["npcs"][d], stage_id=state.get("stage_id", "Ep01Stage01"))) - dmg)
            if os.path.exists(TEST_INFINITE_NPC_HP_FLAG):
                hp[d] = max(1, hp[d])
            changed.append((d, hp[d], h["AttackerSerial"]))
        elif d == me:
            state["sector_got_hit"] = state.get("sector_got_hit", 0) + 1
            floor_hp = 1 if os.path.exists(TEST_ENEMY_DAMAGE_FLAG) else 0
            state["player_hp"] = max(floor_hp, state.get("player_hp", PLAYER_MAX_HP) - dmg)
            changed.append((d, state["player_hp"], h["AttackerSerial"]))


        w.field_tag(f"item_{i}").begin(protocol.h16("Apple::HitInfo"))
        w.u32(h["ProjectileSerial"]).u32(h["DefenderSerial"]).u32(h["AttackerSerial"])
        w.wstr(h["HitResult"] or "HIT_TARGET").u32(h["HitType"]).raw(struct.pack("<q", 0))
    import npc_ai, time
    now = time.monotonic()
    out = [protocol.packet(bytes(w.buf))]
    for d, new_hp, att in changed:


        out.append(change_hp_result(d, new_hp, att, show_ui=False))
        if d in hp and new_hp == 0 and att == me:
            try:
                run = state.get("run")
                if run is not None and not run.get("cleared"):
                    import npc_ai as _na
                    import stage_result
                    _info = _na.npc_info(state["npcs"][d])
                    if stage_result.countable(_info):
                        pts = stage_result.player_killed_npc(run, d, _info.get("Rank") or "NORMAL")
                        if pts:
                            log.write(f"Score +{pts} for {state['npcs'][d]} #{d}: score {run['score']} kills {run['kill_count']} killchain {run['kill_chain']} hitchain {run['hit_chain']} (extra)")
            except Exception as e:
                log.write(f"score tracking failed on kill: {e!r} (extra)")
            out += award_kill_exp(state, d, log)
        if d in hp:
            out += npc_ai.on_death(state, d, now) if new_hp == 0 else (npc_ai.on_hit(state, d, now) or [])
    log.write(f"ProjectileHit {[(h['AttackerSerial'], h['DefenderSerial'], h['HitResult'], h['HitType'], h['DeltaHp']) for h in hits]} hp={hp} player_hp={state.get('player_hp')} (extra)", quiet=True)
    return out


def track_player(mid, plain, state):
    import struct
    try:
        if mid == msg("Apple::Protocol::Both::ActionSystem::DirectionSyncPacket"):
            serial = struct.unpack_from("<I", plain, 6)[0]
            if serial == 1:
                state["player_pos"] = struct.unpack_from("<3f", plain, 10)
        elif mid in (msg("Apple::Protocol::Both::ActionSystem::ActionSyncPacket"),
                     msg("Apple::Protocol::Both::ActionSystem::SkillActionSyncPacket")):
            serial = struct.unpack_from("<I", plain, 6)[0]
            o = 6 + 12
            has = plain[o]; o += 1
            if has:
                o += 4
                n = struct.unpack_from("<I", plain, o)[0]; o += 4
                for _ in range(2 * n):
                    _, o = protocol.read_wstr(plain, o)
            if serial == 1:
                state["player_pos"] = struct.unpack_from("<3f", plain, o)
                import npc_ai
                names = state.setdefault("player_action_names", {})
                if not names:
                    names.update({v: k for k, v in npc_ai.action_hashes("WARRIOR").items()})
                act = names.get(struct.unpack_from("<I", plain, 14)[0], "")
                if not any(k in act for k in ("Jump", "Air", "Fall", "Down")):
                    state["player_ground_y"] = state["player_pos"][1]
    except Exception:
        pass


def buff_delta_totals(state):
    tot = {}
    for b in (state.get("buff_deltas") or {}).values():
        for k, v in b["delta"].items():
            tot[k] = round(tot.get(k, 0.0) + v, 6)
    return tot


def player_status_packet(state, stats):
    import stub_server
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
    totals = equipment_status_totals(p, cls, int(p.get("level", 1)), stats=set(stats))
    extra = buff_delta_totals(state)
    out = {}
    for k in stats:
        kind, base = totals.get(k) or player_statuses(int(p.get("level", 1)), cls).get(k) or ("Float", 0.0)
        v = base + extra.get(k, 0.0)
        out[k] = int(round(v)) if kind in ("Int", "Int64") else v
    return [status_bundle_by_double(state.get("player_serial", 1), out)]


def tick(stub, state, log):
    import npc_ai
    import time
    bd = state.get("buff_deltas")
    if bd:
        now = time.monotonic()
        done = [k for k, b in bd.items() if b["until"] <= now]
        if done:
            stats = {s for k in done for s in bd[k]["delta"]}
            for k in done:
                bd.pop(k)
            log.write(f"buff StatusDelta ended {done} -> {sorted(stats)} (extra)")
            pk = player_status_packet(state, stats)
            if pk:
                return pk + (npc_ai.tick(state, log) if state.get("ai") else [])
    if not state.get("ai"):
        return []
    return npc_ai.tick(state, log)


def additional_npc_pop(plain, log, state):
    import struct, random
    import sdb_stage
    trig, info, skip = struct.unpack_from("<iiB", plain, 6)
    stage = state.get("stage_id", "Ep01Stage01")
    sec_id = state.get("sector_id", "Sector01")
    sec = next((s for s in sdb_stage.stage_info(stage).get("Sectors", []) if s["Id"] == sec_id), {})
    out = []
    try:
        entry = sec["AdditionalNpcPopInfos"][trig]["Infos"][info]
    except (KeyError, IndexError):
        entry = None
    if entry and not skip:
        groups = entry.get("PopGroupInfos", [])
        if groups:
            g = random.choices(groups, weights=[max(0.0, x.get("RateValue") or 0.0) for x in groups])[0]
            nxt = state.get("next_pop_serial", sector_serial_base(sec_id) + 50)
            for pop in g.get("PopInfos", []):
                out.append(create_npc(pop, nxt, 0, 1 if CLIENT_AI else 0, stage))
                state.setdefault("npcs", {})[nxt] = pop["NpcId"]
                state.setdefault("sector_monsters", set()).add(nxt)
                nxt += 1
            state["next_pop_serial"] = nxt
    state.get("pops_pending", set()).discard(trig)
    log.write(f"AdditionalNpcPop {sec_id} trig={trig} info={info} skip={skip} -> {len(out)} npc(s) (extra)")
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::RequestAdditionalNpcPop"))
    w.u32(trig & 0xFFFFFFFF).u32(info & 0xFFFFFFFF)
    out.append(protocol.packet(bytes(w.buf)))
    return out


def return_village(plain, log, state):
    import sdb_stage
    stage = state.get("stage_id", "Ep01Stage01")
    village = sdb_stage.village_for_stage(stage) or "Ep01Village01"
    sector = sdb_stage.stage_info(village).get("StartingSectorId") or "Sector01"
    log.write(f"ReturnVillage alone={plain[6]} {stage} -> {village}/{sector} (extra)")
    state["stage_id"], state["first_stage_sector"] = village, sector
    state.pop("pending_sector", None)
    return [schema_writer.build_message("ReturnVillage_StageSystem_FromServer",
                                        "Apple::Protocol::FromServer::StageSystem::ReturnVillage",
                                        {"VillageId": village, "SectorId": sector, "TimeStamp": 0})]


_EXP_TABLE = []
GENERAL_MAX_LEVEL = 75


def exp_to_next(level):
    import struct
    if not _EXP_TABLE:
        b = open(os.path.join(__import__("config").ALP_DIR, "Database", "ExpTable.b"), "rb").read()
        _EXP_TABLE.extend(struct.unpack_from("<%dI" % struct.unpack_from("<I", b, 0)[0], b, 4))

    if not 1 <= level < GENERAL_MAX_LEVEL or _EXP_TABLE[level - 1] == 0xFFFFFFFF:
        return None
    return _EXP_TABLE[level - 1]


SP_GAIN_PER_LEVEL_UP = 4


def skill_points(p):
    return max(0, (int(p.get("level", 1)) - 1) * SP_GAIN_PER_LEVEL_UP - int(p.get("sp_spent", 0)))


def game_sps_container(p, field="GameSps"):
    import struct
    w = protocol.Writer()
    w.field_tag(field).begin(protocol.h16("array")).u32(2)
    for v in (skill_points(p), 0):
        w.raw(struct.pack("<i", v))
    return bytes(w.buf)


def game_overclock_sps_container(field="GameOverClockSps"):
    import struct
    w = protocol.Writer()
    w.field_tag(field).begin(protocol.h16("array")).u32(2)
    w.raw(struct.pack("<ii", 0, 0))
    return bytes(w.buf)


def level_stat_packets(p, cls, old, new, me):
    import damage
    import items as items_mod
    b_new, b_old = player_statuses(new, cls), player_statuses(old, cls)
    changed = {k for k, v in b_new.items() if v != b_old.get(k)} | set(items_mod.equipment_stats(p, cls)) | {"MaxHp", "MaxMp"}
    totals = equipment_status_totals(p, cls, new, stats=changed)
    bundle = {k: v[1] for k, v in totals.items()}
    return [status_bundle_by_double(me, bundle)]


CAUSE_NONE, CAUSE_NPC, CAUSE_STAGE = 0, 1, 2


def player_info_change(name, typ, value, cause=CAUSE_NONE, gained=None):
    import json
    old_value = json.dumps({"TotalExp": gained, "BaseExp": gained}, separators=(",", ":")) if typ == 1 and gained is not None else ""
    return schema_writer.build_message(
        "PlayerInfoChange_FromServer", "Apple::Protocol::FromServer::ActorSystem::PlayerInfoChange",
        {"PlayerId": name, "Type": typ, "Value": str(value), "OldValue": old_value, "CauseEnum": cause})


def award_kill_exp(state, serial, log):
    import npc_ai
    import npc_status
    npc_id = state.get("npcs", {}).get(serial)
    given = state.setdefault("exp_given", set())
    if not npc_id or serial in given or not npc_ai.npc_info(npc_id).get("GiveExpWhenDie"):
        return []
    amount = npc_status.npc_exp(npc_id, state.get("stage_id", "Ep01Stage01"))
    given.add(serial)
    return add_exp(state, amount, CAUSE_NPC, f"{npc_id} #{serial}", log)


def add_exp(state, amount, cause, label, log):
    import stub_server
    if amount <= 0:
        return []
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
    level, exp = int(p.get("level", 1)), int(p.get("exp", 0)) + amount
    start = level
    while True:
        need = exp_to_next(level)
        if need is None or exp < need:
            break
        exp -= need
        level += 1
    p["level"], p["exp"] = level, exp
    import licenses
    new_lic = licenses.grant_by_level(p) if level != start else []
    stub_server.save_players(state.get("account_id", ""), players)
    me = state.get("player_serial", 1)
    out = []
    if level != start:
        out.append(player_info_change(p["name"], 0, level))
        out += level_stat_packets(p, cls, start, level, me)
        shift_mp(state, player_max_mp(p, cls, start), player_max_mp(p, cls, level))
        out.append(player_info_change(p["name"], 2, skill_points(p)))


    out.append(player_info_change(p["name"], 1, exp, cause, amount))
    if new_lic:
        out += licence_packets(p, new_lic)
        log.write(f"licence {new_lic} granted at level {level} (extra)")
    log.write(f"EXP +{amount} from {label}: level {start} -> {level}, exp {exp} (extra)")
    return out


def change_mp_results(changes, show_ui=True):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::Both::StatusSystem::ChangeMpResults"))
    w.field_tag("ChangeMpTable").begin(protocol.h16("associative_array")).u32(len(changes))
    for serial, mp in changes:
        w.u32(serial).u32(mp & 0xFFFFFFFF)
    w.raw(bytes([1 if show_ui else 0]))
    return protocol.packet(bytes(w.buf))


def npc_killed_by_effect(state, d, att, log):
    import time
    import npc_ai
    out = []
    me = state.get("player_serial", 1)
    try:
        run = state.get("run")
        if att == me and run is not None and not run.get("cleared"):
            import stage_result
            info = npc_ai.npc_info(state["npcs"][d])
            if stage_result.countable(info):
                stage_result.player_killed_npc(run, d, info.get("Rank") or "NORMAL")
        if att == me:
            out += award_kill_exp(state, d, log)
        out += npc_ai.on_death(state, d, time.monotonic())
    except Exception as e:
        log.write(f"kill bookkeeping failed: {e!r} (extra)")
    return out


def request_add_buff(plain, log, state):
    import random
    import struct
    count = struct.unpack_from("<I", plain, 10)[0]
    o, out = 14, []
    for _ in range(count):
        target, caster, owner, _ptype, _pparam, h = struct.unpack_from("<6I", plain, o + 4)
        level, duration = struct.unpack_from("<Hf", plain, o + 28)
        o += 34
        for _arr in range(2):
            o += 8 + struct.unpack_from("<I", plain, o + 4)[0]
        state["buff_serial"] = state.get("buff_serial", 0) + 1
        out.append(schema_writer.build_message(
            "AddBuff_Both", "Apple::Protocol::Both::BuffSystem::AddBuff",
            {"Serial": state["buff_serial"], "TargetSerial": target, "CasterSerial": caster, "CasterOwnerSerial": owner, "Hash": h,
             "Level": level, "Duration": duration, "RandomSeed": random.getrandbits(32)}))
        log.write(f"RequestAddBuff hash {h:#x} level {level} duration {duration} target {target} caster {caster} -> AddBuff serial {state['buff_serial']} (extra)")


        try:
            import eventoptions
            bid = eventoptions.buff_id_for_hash(state, h)
            cx = eventoptions.buff_change_hp(bid) if bid else None
            npcs = state.get("npcs", {})
            if cx and target in npcs:
                hp = state.setdefault("npc_hp", {})
                stage_id = state.get("stage_id", "Ep01Stage01")
                mx = npc_max_hp(npcs[target], stage_id=stage_id)
                cur = hp.get(target, mx)
                new = max(1 if os.path.exists(TEST_INFINITE_NPC_HP_FLAG) else 0, cur - int(mx * cx[1]))
                hp[target] = new
                out.append(change_hp_result(target, new, caster, is_hit=False, show_ui=True, info_hash=eventoptions.x65599(cx[0])))
                log.write(f"buff {bid}: ChangeHpInfo {cx[0]} MAX_HP {cx[1]} -> npc {target} hp {cur} -> {new} (max {mx}) (extra)")
                if new == 0:
                    out += npc_killed_by_effect(state, target, caster, log)
        except Exception as e:
            log.write(f"buff HP change failed: {e!r} (extra)")


        try:
            import time
            import eventoptions
            bid = eventoptions.buff_id_for_hash(state, h)
            delta = eventoptions.buff_status_delta(bid) if bid else None
            if delta and target == state.get("player_serial", 1):
                state.setdefault("buff_deltas", {})[bid] = {"until": time.monotonic() + float(duration), "delta": delta}
                out += player_status_packet(state, set(delta))
                log.write(f"buff {bid}: StatusDelta {delta} for {duration} s sent as totals (extra)")
        except Exception as e:
            log.write(f"buff StatusDelta failed: {e!r} (extra)")
    return out


def learn_skills(plain, log, state):
    import stub_server
    import hit_damage
    import skill_learn
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
    skill_set, req = skill_learn.parse_request(plain)
    ids, have = hit_damage._skill_ids(), skill_levels(cls, p)
    spend, new_levels = 0, {}
    for h, target, _second in req:
        sid = ids.get(h)
        info = skill_learn.learn_info(sid) if sid else None
        cur = have.get(sid, 0)
        if info is None or cls not in info["Classes"] or not cur < target <= info["MaxLevel"] or int(p.get("level", 1)) < info["MinLevelToLearn"]:
            log.write(f"LearnSkillMultiple REJECTED {sid or hex(h)}: level {cur} -> {target}, info {info}, character level {p.get('level', 1)} (extra)")
            return []
        spend += skill_learn.cost(info, cur, target)
        new_levels[sid] = target
    if spend > skill_points(p):
        log.write(f"LearnSkillMultiple REJECTED: needs {spend} SP, has {skill_points(p)} (extra)")
        return []
    p["sp_spent"] = int(p.get("sp_spent", 0)) + spend
    p.setdefault("skills", {}).update(new_levels)
    stub_server.save_players(state.get("account_id", ""), players)
    me = state.get("player_serial", 1)
    w = protocol.Writer()
    w.field_tag("Skills").begin(protocol.h16("array")).u32(len(new_levels))
    for j, (sid, lv) in enumerate(new_levels.items()):
        write_skill(w, f"item_{j}", sid, lv)
    out = [schema_writer.build_message("SetSkill_ActorSystem_FromServer", "Apple::Protocol::FromServer::ActorSystem::SetSkill",
                                       {"ActorSerial": me, "Skills": bytes(w.buf), "IsLearnSkill": 1, "SkillTabIndex": skill_set}),
           schema_writer.build_message("NewSkillLearned_ShopSystem_FromServer", "Apple::Protocol::FromServer::ShopSystem::NewSkillLearned",
                                       {"Result": 0, "SkillTabIndex": skill_set}),
           player_info_change(p["name"], 2, skill_points(p))]
    log.write(f"LearnSkillMultiple {new_levels}: -{spend} SP, left {skill_points(p)} (extra)")
    return out


USE_EFFECT_POTION = 0x13


def use_item(plain, log, state):
    import struct
    import items as items_mod
    import loot
    import stub_server
    tab, slot = struct.unpack_from("<IH", plain, 6)
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    if p is None:
        return []
    rec = loot.inventory(p).get(str(tab), {}).get(str(slot))
    if rec is None:
        log.write(f"UseItem tab {tab} slot {slot}: empty slot (extra)")
        return []
    info = items_mod.item_info(rec[0])
    u = info.get("UsableInfo") or {}
    if not u.get("Enable") or u.get("UseEffect") != "POTION":
        log.write(f"UseItem {rec[0]}: UseEffect {u.get('UseEffect')} not supported (extra)")
        return []
    params = dict(u.get("Params") or [])
    cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
    me = state.get("player_serial", 1)
    max_hp, max_mp = player_max_hp(p, cls), player_max_mp(p, cls)
    hp, mp = int(state.get("player_hp", max_hp)), int(state.get("player_mp", max_mp))
    pct = params.get("Type") == "PERCENT"
    new_hp = min(max_hp, hp + int(float(params["Hp"]) * (max_hp if pct else 1))) if "Hp" in params else hp
    new_mp = min(max_mp, mp + int(float(params["Mp"]) * (max_mp if pct else 1))) if "Mp" in params else mp
    state["player_hp"], state["player_mp"] = new_hp, new_mp
    used = list(rec)
    out = loot.remove_item(p, tab, slot, 1) or []
    stub_server.save_players(state.get("account_id", ""), players)
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::InventorySystem::UseItem"))
    w.u32(0).u32(me)
    items_mod.write_item(w, "ItemInfo", used[0], used[1], grade=(used[3] if len(used) > 3 else None), stack=1)
    w.u32(USE_EFFECT_POTION).empty_container("UseItemOutputItems", "array").empty_container("RecoveredFatigues")
    w.datetime("BlockExpiration", items_mod.NO_LOCK_PENDING_TICKS)
    out.append(protocol.packet(bytes(w.buf)))
    if new_hp != hp:
        out.append(change_hp_result(me, new_hp, 0, is_hit=False, show_ui=True))
    if new_mp != mp:
        out.append(change_mp_results([(me, new_mp)]))
    log.write(f"UseItem {rec[0]} (tab {tab} slot {slot}): hp {hp} -> {new_hp}/{max_hp}, mp {mp} -> {new_mp}/{max_mp}, {used[2] - 1} left (extra)")
    return out


def request_change_mp(plain, log, state):
    import struct
    serial, delta, reason = struct.unpack_from("<IiI", plain, 6)
    show = plain[6 + 12] if len(plain) > 6 + 12 else 1
    if serial != state.get("player_serial", 1):
        return []
    pl = selected_player(state)
    cls = schema_writer.enums()["classType"]["values"].get(str(pl["class"]), "WARRIOR") if pl else "WARRIOR"
    mx = player_max_mp(pl, cls) if pl else 10 ** 9
    mp = int(state.get("player_mp", mx))
    new = max(0, min(mx, mp + delta))
    state["player_mp"] = new
    log.write(f"RequestChangeMp serial {serial} delta {delta} reason {reason}: mp {mp} -> {new} (max {mx}) (extra)")
    return [change_mp_results([(serial, new)], show_ui=bool(show))]


def use_skill(plain, log, state):
    import struct
    import hit_damage
    skill_id = hit_damage._skill_ids().get(struct.unpack_from("<I", plain, 6)[0])
    if not skill_id:
        return []
    pl = selected_player(state)
    level = 1
    if pl:
        level = skill_levels(schema_writer.enums()["classType"]["values"].get(str(pl["class"]), "WARRIOR"), pl).get(skill_id, 1)
    cost = hit_damage.mp_cost(skill_id, level)
    if cost <= 0:
        return []
    mp = int(state.get("player_mp", 0))
    take = min(cost, mp)
    state["player_mp"] = mp - take
    log.write(f"UseSkill {skill_id}: MP cost {cost}, mp {mp} -> {state['player_mp']} (extra)")
    return [change_mp_results([(state.get("player_serial", 1), state["player_mp"])])] if take else []


def respond(stub, mid, plain, log, state):
    track_player(mid, plain, state)
    import quests
    quest_reply = quests.respond(mid, plain, log, state)
    if quest_reply is not None:
        return quest_reply
    if mid == msg("Apple::Protocol::ToServer::SectorSpecificEventSystem::LocationConditionAchieve"):
        return location_condition_achieve(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ActorSystem::RequestLicenses"):
        return request_licenses(plain, log, state)
    if mid == msg("Apple::Protocol::Both::BuffSystem::RequestAddBuff"):
        return request_add_buff(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ShopSystem::LearnSkillMultiple"):
        return learn_skills(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ActionSystem::RequestProjectileHit"):
        return projectile_hit(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::ChangeSector"):
        return change_sector(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::RequestAdditionalNpcPop"):
        return additional_npc_pop(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::DestroyActors"):
        return destroy_actors_reply(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ActionSystem::UseSkill"):
        return use_skill(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ActorSystem::SetQuickSlot"):
        return set_quick_slot(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::StartStage"):
        import struct, os
        stage_hash, difficulty, first_play = struct.unpack_from("<IIB", plain, 6)
        stage_id = stage_id_from_hash(stage_hash)
        import sdb_stage
        sector = sdb_stage.stage_info(stage_id).get("StartingSectorId") if stage_id else None
        sector = sector or "Sector01"
        log.write(f"StartStage hash=0x{stage_hash:08x} = {stage_id} difficulty={difficulty} first={first_play} -> sector {sector} (extra)")
        state["stage_hash"] = stage_hash
        state["stage_id"] = stage_id
        state["first_stage_sector"] = sector
        state["stage_difficulty"] = difficulty
        return [schema_writer.build_message(
            "StartStage_StageSystem_FromServer", "Apple::Protocol::FromServer::StageSystem::StartStage",
            {"StageHash": stage_hash, "SectorId": sector, "Difficulty": difficulty, "TimeStamp": 0,
             "IsRestart": 0, "SelectedRouteIndex": 0, "IntrusionType": 0, "UsingIntrusionItem": 0,
             "IsParallelMaze": 0, "ChangeStageLevel": 0, "AffixLevel": 0, "WeekDay": 0, "ParallelMazeStep": 0})]
    for name, fn in (("EquipItem", "equip"), ("UnequipItem", "unequip")):
        if mid == msg(f"Apple::Protocol::ToServer::InventorySystem::{name}"):
            return inventory_equip(fn, plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::InventorySystem::UseItem"):
        return use_item(plain, log, state)
    if mid == msg("Apple::Protocol::Both::StatusSystem::RequestChangeMp"):
        return request_change_mp(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::ActionSystem::RequestVerifyEventOption"):
        import eventoptions
        log.write(f"RequestVerifyEventOption raw: {plain[6:80].hex()} (extra)", quiet=True)
        try:
            return eventoptions.verify(plain, log, state)
        except Exception as e:
            log.write(f"RequestVerifyEventOption failed: {e!r} (extra)")
            return []
    if mid == msg("Apple::Protocol::ToServer::InventorySystem::RemoveItem"):
        return inventory_remove(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::InventorySystem::Move"):
        return inventory_move(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::StageReward"):
        return stage_reward(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::ReturnVillage"):
        return return_village(plain, log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::JoinVillage"):
        import os


        p = selected_player(state)
        en = schema_writer.enums()
        cls = class_info(en["classType"]["values"].get(str(p["class"]), "")) if p else None
        if JOIN_VILLAGE_FIRST_PLAY and cls and cls["DefaultStage"]:
            village = cls["DefaultStage"]
        else:
            village = os.environ.get("KRITIKA_VILLAGE", "Ep01Village01")
        sector = stage_starting_sector(village) or "Sector01"
        state["first_stage_sector"] = sector
        log.write(f"JoinVillage -> {village}/{sector} IsFirstPlay={JOIN_VILLAGE_FIRST_PLAY} (extra)")
        return [schema_writer.build_message(
            "JoinVillage_StageSystem_FromServer", "Apple::Protocol::FromServer::StageSystem::JoinVillage",
            {"IsFirstPlay": JOIN_VILLAGE_FIRST_PLAY, "SwitchChannelInVillage": 0, "VillageId": village,
             "SectorId": sector, "TimeStamp": 0, "IsReload": 0})]
    if mid == msg("Apple::Protocol::ToServer::StageSystem::LoadEnd") and state.get("pending_sector"):
        return sector_load_end(log, state)
    if mid == msg("Apple::Protocol::ToServer::StageSystem::LoadEnd"):
        out = [schema_writer.build_message("LoadEnd_StageSystem_FromServer",
                                           "Apple::Protocol::FromServer::StageSystem::LoadEnd", {})]
        import time as _t
        import stage_result
        state["run"] = stage_result.new_run(_t.time())
        state["sector_memory"] = {}
        if SEND_STAGE_CREATE_PLAYER:
            out += stage_create_player(stub, state)
        if SEND_SECTOR_NPCS:

            out.append(set_stage_master(1))
            out += sector_npcs(state, state.get("stage_id", "Ep01Stage01"), state.get("first_stage_sector", "Sector01"))
        if SEND_ALL_LOAD_END and is_village(state.get("stage_id", "")):


            out.append(protocol.packet(bytes(protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::VillageLoadEnd")).buf)))
        elif SEND_ALL_LOAD_END:
            out.append(schema_writer.build_message("AllLoadEnd_StageSystem_FromServer",
                                                   "Apple::Protocol::FromServer::StageSystem::AllLoadEnd",
                                                   {"TimeStamp": 0, "ElapsedTimeForQuestTimer": 0.0,
                                                    "PastSectorTime": 0.0}))
        import quests
        out += quests.on_stage_load(state, log)
        log.write(f"stage LoadEnd -> {len(out)} replies (create_player={SEND_STAGE_CREATE_PLAYER})")
        return out
    if mid == msg("Apple::Protocol::ToServer::SessionSystem::RequestServerTime"):
        return [schema_writer.build_message("SetServerTime", "Apple::Protocol::FromServer::SessionSystem::SetServerTime",
                                            {"ServerTime.Ticks": protocol.datetime_ticks_now()})]
    return None
