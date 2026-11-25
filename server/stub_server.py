import argparse
import asyncio
import copy
import datetime as dt
import json
import os
import random
import re
import struct
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kcrypto
import protocol
import schema_writer

_LOG_MODE = os.environ.get("KRITIKA_LOG", "")
LOG_FILES = _LOG_MODE == "1"
VERBOSE = _LOG_MODE == "2"
DETAIL = os.environ.get("KRITIKA_LOG_DETAIL") == "1"
_CONSOLE_LINE = re.compile(r".*(failed|corrupt|too small)", re.I)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "saves")


HERE = os.path.dirname(os.path.abspath(__file__))
_EXTRA = {"mtime": None, "mod": None}


def extra_handlers():
    import importlib.util
    path = os.path.join(HERE, "handlers_extra.py")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if mtime != _EXTRA["mtime"]:
        spec = importlib.util.spec_from_file_location("handlers_extra", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"handlers_extra.py failed to load: {e!r}", flush=True)
            return _EXTRA["mod"]
        _EXTRA.update(mtime=mtime, mod=mod)
    return _EXTRA["mod"]


def run_inject(stub, state, log):
    path = os.path.join(HERE, "inject.py")
    if not os.path.exists(path):
        return []
    ns = {}
    try:
        exec(compile(open(path, encoding="utf-8").read(), path, "exec"), ns)
        role = ns.get("TARGET_ROLE", "stage")
        if stub.role != role:
            return []
        pkts = ns["packets"](stub, state)
    except Exception as e:
        log.write(f"inject failed: {e!r}")
        pkts = []
    done = os.path.join(HERE, "inject_done.py")
    try:
        os.replace(path, done)
    except OSError:
        pass
    for p in pkts:
        log.write(f"INJECT {stub.table.name(protocol.split_header(p)[2])} ({len(p)} bytes)", detail=f": {p[:80].hex(' ')}")
    return pkts


def load_players(account_id):
    path = os.path.join(SAVE_DIR, f"{account_id or 'unknown'}.json")
    try:
        players = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []

    import items
    en = schema_writer.enums()
    dirty = False
    import quests
    import handlers_extra
    for p in players:
        if "equip" not in p.get("items", {}):
            items.player_equipment(p, en["classType"]["values"].get(str(p["class"]), "WARRIOR"))
            dirty = True

        cls_enum = en["classType"]["values"].get(str(p["class"]), "WARRIOR")
        if quests.ensure_default(p, cls_enum, handlers_extra.class_info(cls_enum)):
            dirty = True
        import licenses
        if licenses.grant_by_level(p):
            dirty = True
    if dirty:
        save_players(account_id, players)
    return players


def save_players(account_id, players):
    os.makedirs(SAVE_DIR, exist_ok=True)
    path = os.path.join(SAVE_DIR, f"{account_id or 'unknown'}.json")
    json.dump(players, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def player_overrides(p):
    en = schema_writer.enums()
    return {
        "StaticInfo.PlayerName": p["name"],
        "StaticInfo.PlayerSerial": p.get("serial", p.get("slot", 0) + 1),
        "StaticInfo.PlayerGender": en["playerGender"]["values"].get(str(p["gender"]), "MALE"),
        "StaticInfo.ClassType": en["classType"]["values"].get(str(p["class"]), "WARRIOR"),
        "StaticInfo.CreatedDateTime.Ticks": p["created"],
        "Licenses": __import__("licenses").licenses_override(p),

        "AcceptedQuests": __import__("quests").accepted_override(p),
        "CompletedQuests": __import__("quests").completed_override(p),
        "QuestsOnNoticeList": __import__("quests").notice_override(p),
        "DynamicInfo.GameLv": p.get("level", 1),


        "DynamicInfo.GameXp": p.get("exp", 0),
        "DynamicInfo.SlotIndex": p.get("slot", 0),
        "DynamicInfo.Fatigue": 100,

        "DynamicInfo.SelectedSkillTabIndex": 0,
        "CustomizingData": bytes.fromhex(p["customizing"]),


        "QuickSlots": __import__("handlers_extra").quick_slots(en["classType"]["values"].get(str(p["class"]), ""),
                                                             int(os.environ.get("KRITIKA_QUICKSLOT_TABS", "4")), p),
        "EquipItems": __import__("items").equip_items(p, en["classType"]["values"].get(str(p["class"]), "WARRIOR")),
        "SelectedTitles": schema_writer.empty_selected_titles(),


        "Skills": __import__("handlers_extra").skills_one_tab(en["classType"]["values"].get(str(p["class"]), ""), p),
        "Inventories": __import__("loot").inventories_nested(p, int(os.environ.get("KRITIKA_INVENTORY_TABS", "8"))),
        "DynamicInfo.Gold": p.get("items", {}).get("gold", 0),
        "DynamicInfo.GameSps": __import__("handlers_extra").game_sps_container(p),
        "DynamicInfo.GameOverClockSps": __import__("handlers_extra").game_overclock_sps_container(),
    }


def player_info_bytes(p):
    ov = player_overrides(p)
    ov["Licenses"] = __import__("licenses").playerinfo_licenses_override(p)
    return schema_writer.build_struct("PlayerInfo", ov)


PARTIAL_PARTS = ["StaticInfo", "DynamicInfo", "EquipInfo", "Etc", "Inventories", "Skills", "QuickSlots",
                 "OldSkillsAndQuickSlots", "Buffs", "Quests", "CompletedQuestsByPage", "Titles", "ActiveTitles",
                 "TitleActiveConditionByPage", "Friends", "Abilities", "Licenses", "Contracts", "Imprintings",
                 "EquipSouls", "CrestInfos", "Force", "ItemLocations", "PlantInfos", "RebuyItems", "EventMissions",
                 "AcceptedDailyTodoList", "CompletedDailyTodoList", "DailyTodoAchievement", "CustomHudOffsets"]


def player_list_parts(account_id, p):
    ov = player_overrides(p)
    ov.update({"AccountId": account_id, "PlayerId": p["name"]})


    part_ov = dict(ov)
    part_ov.pop("Inventories", None)
    tabs = int(os.environ.get("KRITIKA_INVENTORY_TABS", "8"))
    out = []
    skip = set(filter(None, os.environ.get("KRITIKA_SKIP_PARTS", "").split(",")))
    for part in PARTIAL_PARTS:
        if part in skip:
            continue
        if part == "Inventories":
            for tab in range(tabs):
                out.append(schema_writer.build_message(
                    "PartialListPlayersInventories", "Apple::Protocol::PartialListPlayersInventories",
                    dict(part_ov, TabIndex=tab, Inventories=__import__("loot").inventory_part_items(p, tab))))
            continue
        out.append(schema_writer.build_message(f"PartialListPlayers{part}",
                                               f"Apple::Protocol::PartialListPlayers{part}", part_ov))
    return out


class SessionLog:
    def __init__(self, peer, events=None):
        self.f = None
        if LOG_FILES:
            os.makedirs(LOG_DIR, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.path = os.path.join(LOG_DIR, f"session-{stamp}.log")
            self.f = open(self.path, "w", encoding="utf-8")
        self.events = events
        self.once = set()
        self.write(f"session opened {peer}")

    def write(self, line, event=None, detail="", quiet=False, once=None):
        if self.events is not None and event is not None:
            self.events.append(event)
        if (quiet or once is not None) and not DETAIL:
            if quiet or once in self.once:
                return
            self.once.add(once)
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        text = f"[{ts}] {line}{detail if DETAIL else ''}"
        if VERBOSE or _CONSOLE_LINE.match(line):
            print(text, flush=True)
        if self.f:
            self.f.write(text + "\n")
            self.f.flush()

    def close(self):
        self.write("session closed")
        if self.f:
            self.f.close()


class StubServer:

    def __init__(self, service_type=1, enc_key=None, dec_key=None, table=None, events=None,
                 role="login", login_address="127.0.0.1:10332", stage_address=("127.0.0.1", 10333)):
        self.service_type = service_type
        self.enc_key = enc_key
        self.dec_key = dec_key
        self.table = table or protocol.MessageTable()
        self.events = events
        self.role = role
        self.login_address = login_address
        self.stage_address = stage_address

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        log = SessionLog(f"[{self.role}]", self.events)
        if self.role == "worldstatus":
            info = protocol.build_world_info(1, "kritika", "Kritika Offline", self.login_address)
            writer.write(info)
            await writer.drain()
            log.write(f"sent WorldInfo ({len(info)} bytes)", detail=f" login={self.login_address}: {info.hex(' ')}",
                      event={"type": "sent_worldinfo"})
            crypt = None
        else:
            enc_key = self.enc_key if self.enc_key is not None else random.getrandbits(32)
            dec_key = self.dec_key if self.dec_key is not None else random.getrandbits(32)
            ticks = protocol.datetime_ticks_now()
            hello = protocol.build_who_are_you(self.service_type, enc_key, dec_key, ticks)
            writer.write(hello)
            await writer.drain()
            log.write(f"sent WhoAreYou ({len(hello)} bytes)",
                      detail=f" GameServiceType={self.service_type} EncKey=0x{enc_key:08x} DecKey=0x{dec_key:08x} Ticks={ticks}: {hello.hex(' ')}",
                      event={"type": "sent_whoareyou", "enc_key": enc_key, "dec_key": dec_key})

            crypt = kcrypto.Cryptor(send_counter=dec_key, recv_counter=enc_key, mode=1)
        buf = bytearray()
        state = {"account_id": "", "oid_account": 1}
        last_tick = 0.0

        async def ai_tick():
            nonlocal last_tick
            now = time.monotonic()
            if crypt is None or self.role != "stage" or now - last_tick < 0.3:
                return
            last_tick = now
            try:
                mod = extra_handlers()
                pkts = mod.tick(self, state, log) if mod is not None and hasattr(mod, "tick") else []
            except Exception as e:
                log.write(f"tick failed: {e!r}")
                pkts = []
            for pkt in pkts:
                writer.write(crypt.encode(pkt))
            if pkts:
                await writer.drain()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(65536), timeout=1.0)
                except asyncio.TimeoutError:
                    await ai_tick()
                    if crypt is not None and self.role in ("stage", "login"):
                        for pkt in run_inject(self, state, log):
                            writer.write(crypt.encode(pkt))
                            await writer.drain()
                    continue
                if not chunk:
                    break
                log.write(f"recv {len(chunk)} raw bytes", detail=f": {chunk[:256].hex(' ')}{' ...' if len(chunk) > 256 else ''}", quiet=True)
                buf += chunk
                while len(buf) >= 2:
                    size = struct.unpack_from("<H", buf, 0)[0]
                    if size < 5:
                        log.write(f"size field {size} too small; dropping buffer", detail=f" {bytes(buf).hex(' ')}",
                                  event={"type": "bad_size", "size": size})
                        buf.clear()
                        break
                    if len(buf) < size:
                        break
                    block = bytes(buf[:size])
                    del buf[:size]
                    for reply in self.process_block(block, crypt, log, state):
                        writer.write(crypt.encode(reply))
                        await writer.drain()
                await ai_tick()
        except (ConnectionResetError, asyncio.IncompleteReadError) as e:
            log.write("client closed the connection", detail=f" ({e!r})")
        finally:
            log.close()
            writer.close()

    def process_block(self, block, crypt, log, state=None):
        if crypt is None:
            self.describe(block, log, "clear", encrypted=False)
            return []
        trial = copy.deepcopy(crypt)
        try:
            plain = trial.decode(block)
        except ValueError as e:


            self.describe(block, log, f"undecryptable ({e}); as clear text", encrypted=False)
            return []
        crypt.__dict__.update(trial.__dict__)
        self.describe(plain, log, f"decrypted ok, seq now {crypt.seq}", encrypted=True)
        return self.respond(plain, log, state if state is not None else {"account_id": "", "oid_account": 1})

    CHANNELS = [{"number": 1, "id": "1", "title": "Channel 1"}]

    def respond(self, plain, log, state):
        if len(plain) < 6:
            return []
        mid = protocol.split_header(plain)[2]
        name = self.table.name(mid).rsplit("::", 1)[-1]
        extra = extra_handlers()
        if extra is not None and hasattr(extra, "respond"):
            try:
                got = extra.respond(self, mid, plain, log, state)
            except Exception as e:
                log.write(f"handlers_extra.respond failed: {e!r}")
                got = None
            if got is not None:
                for r in got:
                    rname = self.table.name(protocol.split_header(r)[2]).rsplit("::", 1)[-1]
                    log.write(f"reply to {name}: {rname} ({len(r)} bytes)", detail=f": {r[:80].hex(' ')}")
                return got
        replies = []
        if mid == protocol.msg_id(protocol.LOGON_REQUEST_NAME):
            req = protocol.parse_logon_request(plain)
            state["account_id"] = req["AccountId"]
            replies = [protocol.build_logon_reply(
                account_id=req["AccountId"], world_id="kritika", world_name="Kritika Offline",
                oid_account=state["oid_account"], channel_type=req["ChannelType"],
                ticks=protocol.datetime_ticks_now())]
            if os.environ.get("KRITIKA_PROBE") == "listplayers":
                saved = load_players(req["AccountId"])
                parts = [b for p in saved for b in player_list_parts(req["AccountId"], p)]
                replies += protocol.build_list_players(req["AccountId"], extra_parts=parts)
                log.write(f"PROBE: sending player list with {len(saved)} characters")
            if os.environ.get("KRITIKA_PROBE") == "createplayer":


                saved = load_players(req["AccountId"])
                if saved:
                    replies.append(protocol.build_create_player_reply(player_info_bytes(saved[0]),
                                                                      protocol.datetime_ticks_now()))
                    log.write("PROBE: sending CreatePlayer for " + saved[0]["name"])
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::RequestChannelList"):
            replies = [protocol.build_channel_list(self.CHANNELS)]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::GetAccountOption"):
            replies = [protocol.build_account_option(state["oid_account"])]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::CashShopSystem::_RequestCashInfo"):
            replies = [protocol.build_cash_info()]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::ListPlayers"):
            players = load_players(state["account_id"])
            parts = [b for p in players for b in player_list_parts(state["account_id"], p)]
            replies = protocol.build_list_players(state["account_id"], extra_parts=parts)
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::RequestSwitchChannel"):
            req = protocol.parse_request_switch_channel(plain)
            log.write(f"RequestSwitchChannel {req}")
            state["channel_id"] = req["ChannelId"]


            replies = [protocol.build_allow_switch_channel(req["ChannelId"], state["account_id"],
                                                           session_key="offline",
                                                           stage_addresses=[self.stage_address])]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::SwitchChannel"):
            req = protocol.parse_switch_channel(plain)
            log.write(f"SwitchChannel {req}")
            state["account_id"] = req["AccountId"] or state["account_id"]
            ch = next((c for c in self.CHANNELS if c["id"] == req["ChannelId"]), self.CHANNELS[0])
            replies = [protocol.build_switch_channel(ch["id"], ch["number"], ch["title"])]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::RequestSecondPasswordState"):
            replies = [protocol.build_second_password_state()]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::CashShopSystem::RequestDirectPurchaseProductInfoList"):
            replies = [protocol.build_direct_purchase_product_list()]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::PlayerManagementSystem::_CreatePlayer"):
            req = protocol.parse_create_player(plain)
            players = load_players(state["account_id"])
            existing = next((p for p in players if p["name"] == req["PlayerId"]), None)
            if existing is None:
                existing = {"name": req["PlayerId"], "gender": req["PlayerGender"], "class": req["ClassType"],
                            "customizing": req["CustomizingData"].hex(), "created": protocol.datetime_ticks_now(),
                            "level": 1, "slot": len(players)}
                players.append(existing)
                save_players(state["account_id"], players)
                log.write(f"created character {existing['name']!r} class={existing['class']} gender={existing['gender']} "
                          f"(account {state['account_id']}, {len(players)} total)")
            replies = [protocol.build_create_player_reply(player_info_bytes(existing), protocol.datetime_ticks_now())]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::SelectPlayer"):
            name, _ = protocol.read_wstr(plain, 6)
            state["player"] = name
            log.write(f"SelectPlayer {name!r}")


            sel = next((p for p in load_players(state["account_id"]) if p["name"] == name), None)
            replies = []
            if sel is not None:
                replies += protocol.build_list_players(state["account_id"], selected_player_id=name, sending_type=2,
                                                       extra_parts=player_list_parts(state["account_id"], sel))
            replies += [schema_writer.build_message(
                "SelectPlayer", "Apple::Protocol::FromServer::SessionSystem::SelectPlayer",
                {"Result": 0, "PlayerId": name, "BlockExpiration.Ticks": protocol.datetime_ticks_now()})]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::SessionSystem::DeselectPlayer"):


            log.write(f"DeselectPlayer {state.get('player')!r}")
            state.pop("player", None)
            replies = [schema_writer.build_message(
                "DeselectPlayer_FromServer", "Apple::Protocol::FromServer::SessionSystem::DeselectPlayer", {"Result": 0})]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::StageSystem::JoinVillage"):
            village = os.environ.get("KRITIKA_VILLAGE", "Ep01Village01")
            sector = os.environ.get("KRITIKA_SECTOR", "Sector01")
            log.write(f"JoinVillage -> {village}/{sector}")
            replies = [schema_writer.build_message(
                "JoinVillage_StageSystem_FromServer", "Apple::Protocol::FromServer::StageSystem::JoinVillage",
                {"IsFirstPlay": 0, "SwitchChannelInVillage": 0, "VillageId": village, "SectorId": sector,
                 "TimeStamp": 0, "IsReload": 0})]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::CashShopSystem::RequestPeriodPackageInfoList"):
            replies = [schema_writer.build_message(
                "RequestPeriodPackageInfoListResult",
                "Apple::Protocol::FromServer::CashShopSystem::RequestPeriodPackageInfoListResult", {})]
        elif mid == protocol.msg_id("Apple::Protocol::ToServer::AutoBattleSystem::GetAutoBattleChannelList"):
            replies = [schema_writer.build_message(
                "GetAutoBattleChannels", "Apple::Protocol::FromServer::AutoBattleSystem::GetAutoBattleChannels", {})]
        elif mid == protocol.msg_id(protocol.CHECK_RESOURCE_HASH_NAME):

            return []
        else:
            log.write(f"no reply sent for {self.table.name(mid).replace('Apple::Protocol::', '')}", event={"type": "unanswered", "id": mid}, once=mid)
            return []
        for r in replies:
            rname = self.table.name(protocol.split_header(r)[2]).rsplit("::", 1)[-1]
            log.write(f"reply to {name}: {rname} ({len(r)} bytes)", detail=f": {r[:120].hex(' ')}{' ...' if len(r) > 120 else ''}",
                      event={"type": "sent", "name": rname, "account": state["account_id"]})
        return replies

    def describe(self, pkt, log, note, encrypted):
        if len(pkt) < 6:
            log.write(f"{note}: short packet ({len(pkt)} bytes)", detail=f" {pkt.hex(' ')}", event={"type": "short", "encrypted": encrypted})
            return
        size, word2, mid = protocol.split_header(pkt)
        name = self.table.name(mid)
        log.write(f"{note}: size={size} word2=0x{word2:04x} id=0x{mid:04x} {name}", detail=f" body={pkt[6:].hex(' ')}", quiet=True,
                  event={"type": "packet", "encrypted": encrypted, "id": mid, "name": name,
                         "word2": word2, "body": pkt[6:]})


def _warm_caches():
    import threading
    def run():
        try:
            import items
            if items._TOOLS not in sys.path:
                sys.path.insert(0, items._TOOLS)
            import sdb_decode
            for db in ("ItemList.db", "EventOptionInfoList.db", "BuffList.db"):
                sdb_decode._load_db(db)
            import loot
            loot.equipment_pool()
        except Exception as e:
            print("cache warm-up failed:", repr(e))
    threading.Thread(target=run, daemon=True).start()


async def main():
    _warm_caches()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=10331, help="world-status port (WorldList.xml)")
    ap.add_argument("--login-port", type=int, default=10332, help="login/game server port advertised in WorldInfo")
    ap.add_argument("--stage-port", type=int, default=10333, help="stage/channel server port sent in AllowSwitchChannel")
    ap.add_argument("--service-type", type=int, default=1,
                    help="WhoAreYou GameServiceType; 1 makes the client continue to LogOn (default)")
    ap.add_argument("--enc-key", type=lambda s: int(s, 0))
    ap.add_argument("--dec-key", type=lambda s: int(s, 0))
    a = ap.parse_args()
    table = protocol.MessageTable()
    login_address = f"{a.host}:{a.login_port}"
    world = StubServer(table=table, role="worldstatus", login_address=login_address)
    stage_address = (a.host, a.stage_port)
    login = StubServer(a.service_type, a.enc_key, a.dec_key, table=table, role="login",
                       stage_address=stage_address)


    stage = StubServer(0, table=table, role="stage", stage_address=stage_address)
    try:
        s1 = await asyncio.start_server(world.handle, a.host, a.port)
        s2 = await asyncio.start_server(login.handle, a.host, a.login_port)
        s3 = await asyncio.start_server(stage.handle, a.host, a.stage_port)
    except OSError:
        raise SystemExit(f"Cannot listen on ports {a.port}, {a.login_port} and {a.stage_port}: another copy of the server (or another program) is already using one of them. "
                         "Close it and start this again.")
    print(f"Server ready on {a.host}:{a.port} (login {a.login_port}, stage {a.stage_port}).", flush=True)
    async with s1, s2, s3:
        await asyncio.gather(s1.serve_forever(), s2.serve_forever(), s3.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
