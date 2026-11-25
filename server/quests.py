import os
import sys

import protocol
import schema_writer

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(HERE, "..", "tools") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))


CONDITION_NPC = 0x200

STATE_COMPLETABLE, STATE_ACCEPTED, STATE_COMPLETED = 1, 3, 4


RESULT_SUCCESS = 0
RESULT_REWARD_ITEM_ID_REQUIRED, RESULT_NOT_SELECTABLE_REWARD_ITEM, RESULT_REWARD_ITEM_ID_NOT_REQUIRED, RESULT_INVENTORY_FULL = 4, 5, 6, 17

FAIL_QUEST_INFO_NOT_FOUND, FAIL_CLASS, FAIL_LEVEL, FAIL_COMPLETED, FAIL_ALREADY_ACCEPTED = 3, 5, 7, 20, 23

RAISE_NOW = 1

_QUESTS = {}


def quest_info(quest_id):
    if quest_id not in _QUESTS:
        import json
        import sdb_decode
        sch = sdb_decode.full_form(json.load(open(os.path.join(HERE, "schemas", "schema_QuestInfo.json"))))
        rec = sdb_decode.record("QuestList.db", quest_id)
        out = None
        if rec is not None:
            out = {}
            try:
                sdb_decode.fields(bytes(rec), 0, sch["fields"], out, 10 ** 6)
            except Exception as e:
                out["__stopped__"] = repr(e)
        _QUESTS[quest_id] = out
    return _QUESTS[quest_id]


_REWARD_BY_LEVEL = {}


def reward_by_level(level):
    if not _REWARD_BY_LEVEL:
        import struct
        b = open(os.path.join(__import__("config").ALP_DIR, "Database", "QuestRewardByLevel.b"), "rb").read()
        for i in range(struct.unpack_from("<I", b, 0)[0]):
            lvl, exp, gold = struct.unpack_from("<Iii", b, 4 + 12 * i)
            _REWARD_BY_LEVEL[lvl] = (exp, gold)
    return _REWARD_BY_LEVEL.get(int(level), (0, 0))


def state_of(p):
    return p.setdefault("quests", {"accepted": {}, "completed": {}, "next_oid": 1})


def _oid(p):
    q = state_of(p)
    q["next_oid"] += 1
    return q["next_oid"]


def npc_conditions(info):
    return list((info.get("CompleteCondition") or {}).get("NpcConditions") or [])


def new_record(p, quest_id, info):
    remain = [{"type": c.get("Type") or "ID", "key": c["Key"], "value": int(c["Value"]), "stages": list(c.get("ApprovedStages") or [])} for c in npc_conditions(info)]
    return {"remain": remain, "completable": not remain, "oid": _oid(p)}


def ensure_default(p, class_enum, class_info):
    if "quests" in p:
        return False
    q = state_of(p)
    qid = (class_info or {}).get("DefaultQuest") or ""
    info = quest_info(qid) if qid else None
    if info:
        q["accepted"][qid] = new_record(p, qid, info)
    return True


def _remain_npc_element(w, index, c):
    w.field_tag(f"item_{index}")
    stages = c["stages"]
    sw = protocol.Writer()
    schema_writer.write_fields(sw, schema_writer.load_schema("Apple_ActiveCondition_RemainNpcCondition"),
                               {"Type": c.get("type") or "ID", "Key": c["key"], "Value": c["value"]})
    body = bytes(sw.buf)

    w.raw(_with_stages(body, stages))


def _with_stages(body, stages):
    tail_map = protocol.Writer().field_tag("NpcKillBySkill").begin(protocol.h16("associative_array")).u32(0)
    tail = bytes(tail_map.buf)
    assert body.endswith(tail)
    head = body[:-len(tail)]
    empty = bytes(protocol.Writer().field_tag("Stages").begin(protocol.h16("array")).u32(0).buf)
    assert head.endswith(empty)
    st = protocol.Writer().field_tag("Stages").begin(protocol.h16("array")).u32(len(stages))
    for s in stages:
        st.wstr(s)
    return head[:-len(empty)] + bytes(st.buf) + tail


def active_condition(quest_id, rec, info):
    conditions = CONDITION_NPC if npc_conditions(info) else 0
    w = protocol.Writer()
    w.field_tag("RemainKillNpcInfos").begin(protocol.h16("array")).u32(len(rec["remain"]))
    for i, c in enumerate(rec["remain"]):
        _remain_npc_element(w, i, c)
    return schema_writer.build_struct("Apple_ActiveCondition", {
        "Id": quest_id, "Conditions": conditions, "Completable": 1 if rec["completable"] else 0,
        "IsImmediateCompletable": 1 if rec["completable"] else 0,
        "RemainCondition.RemainKillNpcInfos": bytes(w.buf), "OidActiveCondition": rec["oid"]})


def accepted_override(p):
    acc = state_of(p)["accepted"]
    w = protocol.Writer().field_tag("AcceptedQuests").begin(protocol.h16("associative_array")).u32(len(acc))
    for i, (qid, rec) in enumerate(sorted(acc.items())):
        w.wstr(qid).field_tag(f"item_second_{i}").raw(active_condition(qid, rec, quest_info(qid) or {}))
    return bytes(w.buf)


def _completed_map(p, tag):
    done = state_of(p)["completed"]
    w = protocol.Writer().field_tag(tag).begin(protocol.h16("associative_array")).u32(len(done))
    for i, (qid, c) in enumerate(sorted(done.items())):
        w.wstr(qid).field_tag(f"item_second_{i}").begin(protocol.h16("Apple::CompletedQuest"))
        w.raw(c["count"].to_bytes(2, "little", signed=True))
        w.datetime("CompletedTime", c["time"]).i64(c["oid"])
    return bytes(w.buf)


def completed_override(p):
    return _completed_map(p, "CompletedQuests")


def _msg(name):
    return protocol.Writer().begin(protocol.registered_id(f"Apple::Protocol::FromServer::QuestSystem::{name}"))


def result_packet(name, quest_id, result):
    w = _msg(name).u32(result).wstr(quest_id).datetime("BlockExpiration", protocol.datetime_ticks_now())
    return protocol.packet(bytes(w.buf))


def changed_packet(player_name, changes, direct_clear=False):
    elem = "Apple::Protocol::FromServer::QuestSystem::ChangedQuestType"
    ctype = "Apple::Protocol::FromServer::QuestSystem::ChangeType"
    w = _msg("ActiveQuestChanged").wstr(player_name)
    w.field_tag("ChangedQuests").begin(protocol.h16("array")).u32(len(changes))
    for i, c in enumerate(changes):
        w.field_tag(f"item_{i}").begin(protocol.h16(elem))
        w.wstr(c["quest_id"]).u32(c["state"]).u32(c["condition"])
        if c["state"] in (STATE_ACCEPTED, STATE_COMPLETABLE):
            w.field_tag("AcceptState").raw(c["accept"])
        if c["state"] == STATE_ACCEPTED:
            w.field_tag("Changes").begin(protocol.h16("array")).u32(len(c.get("changes", [])))
            for j, (count, full, npc_key, npc_type) in enumerate(c.get("changes", [])):
                w.field_tag(f"item_{j}").begin(protocol.h16(ctype))


                w.wstr("Value").wstr("")
                w.wstr("").wstr(npc_key)
                w.wstr("").wstr("").wstr("").wstr("").wstr("")
                w.raw(int(count).to_bytes(4, "little", signed=True)).u32(full)
        if c["state"] == STATE_COMPLETED:
            w.datetime("CompletedTime", c["time"])
    w.u8(1 if direct_clear else 0)
    return protocol.packet(bytes(w.buf))


def dialog_packet(dialog_id):
    return protocol.packet(bytes(_msg("RaiseCinematicDialog").wstr(dialog_id).u32(RAISE_NOW).buf))


def last_main_quest_packet(quest_id):
    return protocol.packet(bytes(_msg("UpdateLastMainQuestId").wstr(quest_id).buf))


def notice_override(p):
    ids = sorted(state_of(p).setdefault("notice", []))
    w = protocol.Writer().field_tag("QuestsOnNoticeList").begin(protocol.h16("array")).u32(len(ids))
    for q in ids:
        w.wstr(q)
    return bytes(w.buf)


def update_notice(plain, log, state):
    import struct
    off = 6
    lists = []
    for _ in range(2):
        off += 4
        n = struct.unpack_from("<I", plain, off)[0]
        off += 4
        ids = []
        for _ in range(n):
            q, off = protocol.read_wstr(plain, off)
            ids.append(q)
        lists.append(ids)
    added, deleted = lists
    players, p = _player(state)
    if p is None:
        return []
    notice = set(state_of(p).setdefault("notice", [])) | set(added)
    notice -= set(deleted)
    state_of(p)["notice"] = sorted(notice)
    _save(state, players)
    log.write(f"UpdateNotice +{added} -{deleted} -> {sorted(notice)} (extra)")
    w = _msg("UpdateNotice").field_tag("QuestsOnNoticeList").begin(protocol.h16("array")).u32(len(notice))
    for q in sorted(notice):
        w.wstr(q)
    return [protocol.packet(bytes(w.buf))]


def objective_sectors(stage_id, npc_key):
    import sdb_stage
    out = set()
    for sec in sdb_stage.stage_info(stage_id).get("Sectors", []):
        ids = {pop["NpcId"].lower() for pop in sec.get("PopInfos", [])}
        for add in sec.get("AdditionalNpcPopInfos", []):
            for inf in add.get("Infos", []):
                for grp in inf.get("PopGroupInfos", []):
                    ids |= {pop["NpcId"].lower() for pop in grp.get("PopInfos", [])}
        if npc_key.lower() in ids:
            out.add(sec["Id"])
    return out


def mark_sectors(p, stage_id):
    sectors = set()
    for qid, rec in state_of(p)["accepted"].items():
        for c in rec["remain"]:
            if c["value"] > 0 and stage_id in c["stages"]:
                sectors |= objective_sectors(stage_id, c["key"])
    return sectors


def marks_packet(p, stage_id):
    ids = sorted(mark_sectors(p, stage_id))
    w = _msg("UpdateQuestMarks").wstr(p["name"]).field_tag("EnabledSectorIds").begin(protocol.h16("array")).u32(len(ids))
    for sid in ids:
        w.wstr(sid)
    return protocol.packet(bytes(w.buf))


def _save(state, players):
    import stub_server
    stub_server.save_players(state.get("account_id", ""), players)


def _player(state):
    import stub_server
    players = stub_server.load_players(state.get("account_id", ""))
    p = next((x for x in players if x["name"] == state.get("player")), None)
    return players, p


def on_kills(state, destroyed, log):
    players, p = _player(state)
    if p is None:
        return []
    last = state.get("last_attacker", {})
    me = state.get("player_serial", 1)
    npc_ids = [npc for serial, npc in destroyed if npc and last.get(serial, me) == me]
    stage = state.get("stage_id", "")
    out, dirty = [], False
    for qid, rec in sorted(state_of(p)["accepted"].items()):
        info = quest_info(qid)
        if not info or not rec["remain"]:
            continue
        totals = {c["Key"]: int(c["Value"]) for c in npc_conditions(info)}
        changes = []
        for npc in npc_ids:
            for c in rec["remain"]:
                if npc and c["key"].lower() == npc.lower() and stage in c["stages"] and c["value"] > 0:
                    c["value"] -= 1
                    changes.append((totals[c["key"]] - c["value"], totals[c["key"]], c["key"], c.get("type") or "ID"))
                    dirty = True
        if not changes:
            continue
        rec["remain"] = [c for c in rec["remain"] if c["value"] > 0]
        rec["completable"] = not rec["remain"]
        out.append(changed_packet(p["name"], [{"quest_id": qid, "state": STATE_ACCEPTED, "condition": CONDITION_NPC,
                                               "accept": active_condition(qid, rec, info), "changes": changes}]))
        log.write(f"quest {qid}: kill progress {changes}, remaining {rec['remain']} (extra)")
        if rec["completable"]:
            out.append(changed_packet(p["name"], [{"quest_id": qid, "state": STATE_COMPLETABLE, "condition": 0,
                                                   "accept": active_condition(qid, rec, info)}]))
            log.write(f"quest {qid}: COMPLETABLE (extra)")
    if dirty:
        _save(state, players)
        out.append(marks_packet(p, stage))
    return out


def on_enter_sector(state, log):
    players, p = _player(state)
    if p is None:
        return []
    out = []
    for qid in sorted(state_of(p)["accepted"]):
        info = quest_info(qid) or {}
        seen = set()
        for e in (info.get("CinematicDialogInfo") or {}).get("OnEnterSector") or []:
            loc = e["SectorInfo"]
            key = (loc["StageId"], loc["SectorId"], e["DialogId"])
            if key in seen:
                continue
            if loc["StageId"] == state.get("stage_id") and loc["SectorId"] == state.get("sector_id") and loc.get("Difficulty") == "NORMAL":
                seen.add(key)
                out.append(dialog_packet(e["DialogId"]))
                log.write(f"quest {qid}: cinematic dialog {e['DialogId']} on entering {key[0]}/{key[1]} (extra)")
    return out


def on_stage_load(state, log):
    players, p = _player(state)
    out = []
    if p is not None:
        for qid, rec in sorted(state_of(p)["accepted"].items()):
            if rec["completable"]:
                out.append(changed_packet(p["name"], [{"quest_id": qid, "state": STATE_COMPLETABLE, "condition": 0,
                                                       "accept": active_condition(qid, rec, quest_info(qid) or {})}]))
                log.write(f"quest {qid}: COMPLETABLE re-announced on stage load (extra)")
        out.append(marks_packet(p, state.get("stage_id", "")))
    return out + on_enter_sector(state, log)


def accept(plain, log, state):
    qid, off = protocol.read_wstr(plain, 6)
    players, p = _player(state)
    if p is None:
        return []
    info = quest_info(qid)
    q = state_of(p)
    fail = None
    if not info:
        fail = FAIL_QUEST_INFO_NOT_FOUND
    elif qid in q["accepted"]:
        fail = FAIL_ALREADY_ACCEPTED
    elif q["completed"].get(qid, {}).get("count", 0) >= int(info.get("CanCompleteCount") or 1):
        fail = FAIL_COMPLETED
    else:
        cond = info.get("AcceptCondition") or {}
        cls = schema_writer.enums()["classType"]["values"].get(str(p["class"]), "WARRIOR")
        lv = cond.get("Level") or [0, 0]
        if cond.get("Classes") and cls not in cond["Classes"]:
            fail = FAIL_CLASS
        elif not (lv[0] <= p.get("level", 1) <= lv[1]):
            fail = FAIL_LEVEL
    if fail:
        log.write(f"Accept {qid}: refused {fail} (extra)")
        return [result_packet("AcceptFailed", qid, fail)]
    rec = new_record(p, qid, info)
    q["accepted"][qid] = rec
    _save(state, players)
    log.write(f"Accept {qid}: accepted, remain {rec['remain']} (extra)")
    out = [result_packet("AcceptQuestResult", qid, RESULT_SUCCESS),
           changed_packet(p["name"], [{"quest_id": qid, "state": STATE_ACCEPTED, "condition": 0, "accept": active_condition(qid, rec, info), "changes": []}])]
    dlg = (info.get("CinematicDialogInfo") or {}).get("OnQuestAccepted")
    if dlg:
        out.append(dialog_packet(dlg))
    if rec["completable"]:
        out.append(changed_packet(p["name"], [{"quest_id": qid, "state": STATE_COMPLETABLE, "condition": 0, "accept": active_condition(qid, rec, info)}]))
    out.append(marks_packet(p, state.get("stage_id", "")))
    return out


def complete(plain, log, state):
    import handlers_extra
    import loot
    qid, off = protocol.read_wstr(plain, 6)
    reward_id, off = protocol.read_wstr(plain, off)
    direct = plain[off] if off < len(plain) else 0
    players, p = _player(state)
    if p is None:
        return []
    info = quest_info(qid)
    q = state_of(p)
    rec = q["accepted"].get(qid)
    if not info or rec is None or direct or not rec["completable"]:
        log.write(f"Complete {qid}: refused (known={bool(info)} accepted={rec is not None} direct={direct})")
        return [result_packet("CompleteQuest", qid, 1 if not info else 16)]
    rw = info.get("CompleteReward") or {}
    selectable = [i for i, _n in (rw.get("SelectableItems") or [])]
    if selectable and not reward_id:
        return [result_packet("CompleteQuest", qid, RESULT_REWARD_ITEM_ID_REQUIRED)]
    if selectable and reward_id not in selectable:
        return [result_packet("CompleteQuest", qid, RESULT_NOT_SELECTABLE_REWARD_ITEM)]
    if not selectable and reward_id:
        return [result_packet("CompleteQuest", qid, RESULT_REWARD_ITEM_ID_NOT_REQUIRED)]
    grants = [(i, int(n)) for i, n in (rw.get("ItemsToGive") or [])]
    if reward_id:
        grants += [(i, int(n)) for i, n in (rw.get("SelectableItems") or []) if i == reward_id]
    out, given = [], []
    try:
        for item_id, count in grants:
            tab, slot, serial, grade = loot.add_item(p, item_id, count)
            given.append((tab, slot, item_id, serial, count, grade))
    except StopIteration:
        log.write(f"Complete {qid}: inventory full")
        return [result_packet("CompleteQuest", qid, RESULT_INVENTORY_FULL)]
    except Exception as e:
        log.write(f"Complete {qid}: reward item creation failed: {e!r}")
        return [result_packet("CompleteQuest", qid, 15 if reward_id else 16)]
    skipped = [k for k in ("ExpRate", "RemoveGold", "BuffsToGive", "BuffsToRemove", "ItemsToRemove", "RewarsdByCompleteCount", "LicensesToRemove", "ChangeClass",
                           "MakeAwaken", "AwakenPoint", "Title", "TierAttackPower", "StepLevelStageIdToGive") if rw.get(k)]
    if skipped:
        log.write(f"Complete {qid}: reward kinds not implemented: {skipped}")
    table_exp, table_gold = reward_by_level(info.get("Level") or 0)
    gold = int(rw.get("Gold") or 0) + table_gold
    if gold:
        p.setdefault("items", {})["gold"] = p["items"].get("gold", 0) + gold
    del q["accepted"][qid]
    now = protocol.datetime_ticks_now()
    prev = q["completed"].get(qid)
    q["completed"][qid] = {"count": (prev["count"] if prev else 0) + 1, "time": now, "oid": _oid(p)}
    new_lic = []
    import licenses
    for stage_id, difficulty in rw.get("LicensesToGive") or []:
        if licenses.grant(p, stage_id, difficulty if isinstance(difficulty, str) else "NORMAL"):
            new_lic.append(stage_id)
    _save(state, players)
    log.write(f"Complete {qid}: reward {grants}, licences {new_lic} (extra)")
    out.append(result_packet("CompleteQuest", qid, RESULT_SUCCESS))
    for tab, slot, item_id, serial, count, grade in given:
        out.append(loot.set_item_packet(tab, slot, item_id, serial, count, loot.REASON_STAGE, "", grade))
    out.append(changed_packet(p["name"], [{"quest_id": qid, "state": STATE_COMPLETED, "condition": 0, "time": now}]))
    dlg = (info.get("CinematicDialogInfo") or {}).get("OnQuestCompleted")
    if dlg:
        out.append(dialog_packet(dlg))
    if info.get("Category") == "MAIN":
        out.append(last_main_quest_packet(qid))
    if new_lic:
        out += handlers_extra.licence_packets(p, new_lic)
    exp = int(rw.get("BonusExp") or 0) + table_exp
    if exp:
        out += handlers_extra.add_exp(state, exp, handlers_extra.CAUSE_NONE, f"quest {qid} (QuestRewardByLevel {table_exp} + BonusExp)", log)
    return out


def completed_list(plain, log, state):
    players, p = _player(state)
    if p is None:
        return []
    w = _msg("RequestCompletedQuestList").raw(_completed_map(p, "CompletedQuests"))
    return [protocol.packet(bytes(w.buf))]


def completed_list_single_npc(plain, log, state):
    npc, _ = protocol.read_wstr(plain, 6)
    players, p = _player(state)
    if p is None:
        return []
    w = _msg("RequestCompletedQuestListForSingleNpc").wstr(npc).raw(_completed_map(p, "CompletedQuests"))
    return [protocol.packet(bytes(w.buf))]


def respond(mid, plain, log, state):
    names = {"Accept": accept, "Complete": complete, "UpdateNotice": update_notice, "RequestCompletedQuestList": completed_list,
             "RequestCompletedQuestListForSingleNpc": completed_list_single_npc}
    for name, fn in names.items():
        if mid == protocol.msg_id(f"Apple::Protocol::ToServer::QuestSystem::{name}"):
            try:
                return fn(plain, log, state)
            except Exception as e:


                log.write(f"QuestSystem {name} failed: {e!r}")
                if name in ("Accept", "Complete"):
                    qid, _ = protocol.read_wstr(plain, 6)
                    return [result_packet("AcceptFailed" if name == "Accept" else "CompleteQuest", qid, 16)]
                return []
    return None
