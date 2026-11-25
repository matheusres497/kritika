import hashlib
import re
import struct
import datetime
import time

import config

CLIENT_EXE = config.CLIENT_EXE


def h(text: str) -> int:
    v = 0
    for ch in text:
        v = ((ord(ch) % 31) + v * 31) % 64000000
    return v


def h16(text: str) -> int:
    return h(text) & 0xFFFF


def msg_id(full_name: str) -> int:
    return h16(hashlib.md5(full_name.encode("utf-8")).hexdigest())


class MessageTable:

    def __init__(self, exe_path: str = CLIENT_EXE):
        self.by_id = {}
        self.by_name = {}
        try:
            data = open(exe_path, "rb").read()
        except OSError:
            return
        registered = self._registered_md5(data)
        candidates = {}
        for m in re.finditer(rb"\.\?AU([A-Za-z0-9_]+)@([A-Za-z0-9_]+)@(FromServer|ToServer|Both)@Protocol@Apple@@", data):
            d, s, n = m.group(3).decode(), m.group(2).decode(), m.group(1).decode()
            full = f"Apple::Protocol::{d}::{s}::{n}"
            for spelling in (full, f"{d}::{s}::{n}", f"Protocol::{d}::{s}::{n}", f"{s}::{n}", n):
                candidates.setdefault(hashlib.md5(spelling.encode()).hexdigest(), full)

        for m in re.finditer(rb"\.\?AU([A-Za-z0-9_]+)@Protocol@Apple@@\x00", data):
            n = m.group(1).decode()
            full = f"Apple::Protocol::{n}"
            for spelling in (full, f"Protocol::{n}", n):
                candidates.setdefault(hashlib.md5(spelling.encode()).hexdigest(), full)
        for md5hex in registered:
            name = candidates.get(md5hex, "unknown:" + md5hex)
            mid = h16(md5hex)
            self.by_id[mid] = name
            self.by_name[name] = mid

    @staticmethod
    def _registered_md5(data: bytes):
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        secs = []
        for i in range(nsec):
            o = pe + 24 + opt + i * 40
            _vs, va, rs, ro = struct.unpack_from("<IIII", data, o + 8)
            secs.append((data[o:o + 8].rstrip(b"\0"), 0x400000 + va, rs, ro))

        def wstr(v):
            for _n, va, rs, ro in secs:
                if va <= v < va + rs:
                    off = ro + v - va
                    end = data.find(b"\0\0", off)
                    while end != -1 and (end - off) % 2:
                        end = data.find(b"\0\0", end + 1)
                    try:
                        return data[off:end].decode("utf-16-le")
                    except UnicodeDecodeError:
                        return None
            return None

        text = [s for s in secs if s[0] == b".text"][0]
        code = data[text[3]:text[3] + text[2]]
        out = set()
        for m in re.finditer(rb"\x68(....)\xe8....\x8b\xc8\xe8....\xa3....\xc3", code, re.S):
            s = wstr(struct.unpack("<I", m.group(1))[0])
            if s and re.fullmatch(r"[0-9a-f]{32}", s):
                out.add(s)
        return out

    def name(self, mid: int) -> str:
        return self.by_id.get(mid, f"unregistered:0x{mid:04x}")


_TABLE = None


def registered_id(full_name: str) -> int:
    global _TABLE
    if _TABLE is None:
        _TABLE = MessageTable()
    if full_name not in _TABLE.by_name:
        raise KeyError(f"{full_name} is not registered by the client")
    return _TABLE.by_name[full_name]


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def begin(self, type_hash: int):
        self.buf += struct.pack("<H", type_hash & 0xFFFF)
        return self

    def u32(self, v: int):
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)
        return self

    def i64(self, v: int):
        self.buf += struct.pack("<q", v)
        return self

    def u8(self, v: int):
        self.buf += struct.pack("<B", v & 0xFF)
        return self

    def u16(self, v: int):
        self.buf += struct.pack("<H", v & 0xFFFF)
        return self

    def wstr(self, s: str):
        data = s.encode("utf-16-le") + b"\0\0"
        self.buf += struct.pack("<H", len(data) // 2) + data
        return self

    def str8(self, s: str):
        data = s.encode("utf-8") + b"\0"
        self.buf += struct.pack("<H", len(data)) + data
        return self

    def raw(self, data: bytes):
        self.buf += data
        return self

    def field_tag(self, field_name: str):
        self.buf += struct.pack("<H", h16(field_name))
        return self

    def datetime(self, field_name: str, ticks: int):
        return self.field_tag(field_name).begin(h16(DATETIME_TYPE)).i64(ticks)

    def empty_container(self, field_name: str, kind: str = "associative_array"):
        return self.field_tag(field_name).begin(h16(kind)).u32(0)


NET_STREAM = "NetStream"


def packet(body: bytes, word2: int = None) -> bytes:
    if word2 is None:
        word2 = h16(NET_STREAM)
    size = 4 + len(body)
    return struct.pack("<HH", size, word2) + body


def split_header(pkt: bytes):
    size, word2, mid = struct.unpack_from("<HHH", pkt, 0)
    return size, word2, mid


WHOAREYOU_NAME = "Apple::Protocol::FromServer::SessionSystem::WhoAreYou"
DATETIME_TYPE = "Apple::DateTime"


DATETIME_EPOCH = datetime.datetime(1400, 1, 1)


def datetime_ticks(when: datetime.datetime) -> int:
    delta = when.replace(tzinfo=None) - DATETIME_EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def datetime_ticks_now() -> int:
    return datetime_ticks(datetime.datetime.now())


def build_who_are_you(service_type: int, enc_key: int, dec_key: int, ticks: int) -> bytes:
    w = Writer()
    w.begin(msg_id(WHOAREYOU_NAME))
    w.u32(service_type).u32(enc_key).u32(dec_key)
    w.field_tag("ServerTime").begin(h16(DATETIME_TYPE)).i64(ticks)
    return packet(bytes(w.buf))


WORLDINFO_NAME = "Apple::Protocol::FromServer::SessionSystem::WorldInfo"

WORLD_STATUS = ("INSPECTION", "COMFORTABLE", "CROWED", "SATURATION")


def build_world_info(world_number: int, world_id: str, world_name: str, login_address: str,
                     status: str = "COMFORTABLE", capacity: int = 1000, players: int = 0,
                     create_disabled: bool = False) -> bytes:
    if status not in WORLD_STATUS:
        raise ValueError(f"status must be one of {WORLD_STATUS}")
    w = Writer()
    w.begin(msg_id(WORLDINFO_NAME))
    w.field_tag("Info").begin(h16("WorldInfo"))
    w.u32(world_number)
    w.wstr(world_id).wstr(world_name).wstr(login_address)
    w.wstr(status)
    w.u32(capacity).u32(players)
    w.u8(1 if create_disabled else 0)
    return packet(bytes(w.buf))


LOGON_REQUEST_NAME = "Apple::Protocol::ToServer::SessionSystem::LogOn"
LOGON_REPLY_NAME = "Apple::Protocol::FromServer::SessionSystem::LogOn"
CHECK_RESOURCE_HASH_NAME = "Apple::Protocol::ToServer::SecuritySystem::CheckResourceHash"


def read_wstr(buf: bytes, off: int):
    n = struct.unpack_from("<H", buf, off)[0]
    raw = buf[off + 2:off + 2 + 2 * n]
    return raw.decode("utf-16-le", "replace").split("\0", 1)[0], off + 2 + 2 * n


def parse_logon_request(pkt: bytes) -> dict:
    out = {}
    try:
        out["AccountId"], _ = read_wstr(pkt, 6)
    except (struct.error, UnicodeDecodeError):
        out["AccountId"] = ""
    out["ChannelType"] = struct.unpack_from("<I", pkt, len(pkt) - 4)[0] if len(pkt) >= 10 else 0
    return out


def build_logon_reply(account_id: str, world_id: str, world_name: str, oid_account: int,
                      channel_type: int, ticks: int, result: int = 0,
                      player_not_created: bool = False) -> bytes:
    w = Writer()
    w.begin(msg_id(LOGON_REPLY_NAME))
    w.u32(result)
    w.wstr(account_id)
    w.u32(20).u32(0).u32(0)
    w.u8(0).u8(0).u8(0)
    w.u8(1 if player_not_created else 0)
    w.wstr(world_name).wstr(world_id)
    w.i64(oid_account)
    w.field_tag("ReturneeInfo").begin(h16("Apple::ReturneeInfo"))
    w.u8(0).datetime("ReturnExpiration", ticks)
    w.u32(0).u32(0).u32(0).u32(0).u32(0)
    w.u8(0)
    w.datetime("MembershipExpiration", ticks)
    w.datetime("Created", ticks)
    w.datetime("BlockExpiration", ticks)
    w.u32(0)
    w.wstr("")
    w.u32(0)
    w.datetime("BlockGradeExpiration", ticks)
    w.wstr("").wstr("")
    w.field_tag("VipInfo").begin(h16("Apple::VipInfo"))
    w.u32(0).u32(0)
    w.wstr("").wstr("")
    w.wstr("")
    w.u8(0)
    for field in ("AttendInfo", "SchoolAttendInfo"):
        w.field_tag(field).begin(h16("Apple::AttendEventInfo"))
        w.datetime("AttendTime", ticks).u32(0).u32(0)
    w.empty_container("SchoolAttendV2Info")
    w.datetime("SecondPasswordResetDateTime", ticks)
    w.u32(0).u32(0).u32(0)
    w.empty_container("CardCollectionList")
    w.empty_container("RedrawCardCollectionCards", "array")
    w.u32(channel_type)
    return packet(bytes(w.buf))


def build_channel_list(channels) -> bytes:
    w = Writer()
    w.begin(msg_id("Apple::Protocol::FromServer::SessionSystem::ChannelList"))
    w.field_tag("Channels").begin(h16("array")).u32(len(channels))
    for i, c in enumerate(channels):
        w.field_tag(f"item_{i}").begin(h16("Apple::ChannelElementForClient"))
        w.u32(c["number"])
        w.wstr(c["id"]).wstr(c["title"])
        w.u8(1 if c.get("enabled", True) else 0)
        w.u32(c.get("capacity", 1000))
        w.u32(c.get("players", 0))
        w.u32(0).u32(0)
        w.wstr(c.get("property_id", ""))
        w.raw(c.get("crowded_criteria", bytes(8)))
    w.field_tag("DbChannelPropertys").begin(h16("array")).u32(0)
    return packet(bytes(w.buf))


def build_account_option(oid_account: int, compressed_option: str = "", result: int = 0) -> bytes:
    w = Writer()
    w.begin(msg_id("Apple::Protocol::FromServer::SessionSystem::AccountOption"))
    w.i64(oid_account).str8(compressed_option).u8(0).u32(result)
    return packet(bytes(w.buf))


def build_cash_info(kpoint: int = 0, cash: int = 0, result: int = 0) -> bytes:
    w = Writer()
    w.begin(msg_id("Apple::Protocol::FromServer::CashShopSystem::RequestCashInfo"))
    w.u32(result).u32(kpoint).u32(cash)
    return packet(bytes(w.buf))


def build_list_players(account_id: str, selected_player_id: str = "", connection_serial: int = 1,
                       sending_type: int = 1, extra_parts=()):
    b = Writer().begin(registered_id("Apple::Protocol::BeginPartialListPlayers")).wstr(account_id)


    f = Writer().begin(registered_id("Apple::Protocol::PartialListBestFriendInfo")).wstr(account_id)
    f.field_tag("SpecialBestFriend").begin(h16("Apple::BestFriendInfo"))
    f.i64(0).wstr("").u32(0).u32(0).wstr("").u8(0)
    f.field_tag("BestFriends").begin(h16("array")).u32(0)
    e = Writer().begin(registered_id("Apple::Protocol::EndPartialListPlayers")).wstr(account_id)
    e.wstr(selected_player_id).u32(connection_serial).u32(sending_type)
    return [packet(bytes(b.buf)), packet(bytes(f.buf))] + list(extra_parts) + [packet(bytes(e.buf))]


def parse_request_switch_channel(pkt: bytes) -> dict:
    cid, off = read_wstr(pkt, 6)
    kind, _ = read_wstr(pkt, off) if off + 2 <= len(pkt) else ("", off)
    return {"ChannelId": cid, "SwitchChannelType": kind}


def parse_switch_channel(pkt: bytes) -> dict:
    cid, off = read_wstr(pkt, 6)
    acct, off = read_wstr(pkt, off)
    key, _ = read_wstr(pkt, off)
    return {"ChannelId": cid, "AccountId": acct, "SessionKey": key}


def build_allow_switch_channel(channel_id: str, account_id: str, session_key: str,
                               stage_addresses=(), prev_channel_id: str = "", result: int = 0) -> bytes:
    w = Writer()
    w.begin(msg_id("Apple::Protocol::FromServer::SessionSystem::AllowSwitchChannel"))
    w.u32(result).wstr(channel_id).wstr(account_id).wstr(session_key)
    w.field_tag("StageServerAddresses").begin(h16("array")).u32(len(stage_addresses))
    for i, (host, port) in enumerate(stage_addresses):
        w.field_tag(f"item_{i}").begin(h16("AllM::Net::Address")).str8(host).u16(port)
    w.wstr(prev_channel_id)
    return packet(bytes(w.buf))


def build_switch_channel(channel_id: str, channel_number: int, channel_title: str,
                         selected_player_id: str = "", result: int = 0) -> bytes:
    w = Writer()
    w.begin(msg_id("Apple::Protocol::FromServer::SessionSystem::SwitchChannel"))
    w.u32(result).wstr(selected_player_id).wstr(channel_id).u32(channel_number)
    w.u8(0).wstr("").wstr(channel_title).i64(0)
    return packet(bytes(w.buf))


SECOND_PASSWORD_NONE = 3


def build_second_password_state(state: int = SECOND_PASSWORD_NONE) -> bytes:
    w = Writer().begin(msg_id("Apple::Protocol::FromServer::SessionSystem::RequestSecondPasswordState"))
    return packet(bytes(w.u32(state).buf))


def build_direct_purchase_product_list() -> bytes:
    w = Writer().begin(msg_id("Apple::Protocol::FromServer::CashShopSystem::DirectPurchaseProductInfoListResult"))
    w.field_tag("ProductList").begin(h16("array")).u32(0)
    return packet(bytes(w.buf))


def parse_create_player(pkt: bytes) -> dict:
    name, off = read_wstr(pkt, 6)
    gender, cls = struct.unpack_from("<II", pkt, off)
    off += 8

    end = len(pkt)
    tail = None
    for p in range(end - 4, off, -2):
        n = struct.unpack_from("<H", pkt, p)[0]
        if n >= 1 and p + 2 + 2 * n == end and pkt[end - 2:end] == bytes(2):
            tail = p
            break
    if tail is None:
        raise ValueError("cannot find JumpingEventId")
    jumping = pkt[tail - 1]
    event_id, _ = read_wstr(pkt, tail)
    return {"PlayerId": name, "PlayerGender": gender, "ClassType": cls,
            "CustomizingData": pkt[off:tail - 1], "IsJumpingCharacter": jumping, "JumpingEventId": event_id}


def build_create_player_reply(player_info: bytes, ticks: int, result: int = 0) -> bytes:
    w = Writer().begin(msg_id("Apple::Protocol::FromServer::PlayerManagementSystem::CreatePlayer"))
    w.u32(result).datetime("BlockExpiration", ticks)
    if result == 0:
        w.field_tag("Player").raw(player_info)
    return packet(bytes(w.buf))


def parse_who_are_you(pkt: bytes) -> dict:
    size, word2, mid = split_header(pkt)
    service, enc, dec = struct.unpack_from("<III", pkt, 6)
    tag, dt_hash, ticks = struct.unpack_from("<HHq", pkt, 18)
    return {"size": size, "word2": word2, "id": mid, "GameServiceType": service, "EncKey": enc,
            "DecKey": dec, "ServerTimeTag": tag, "DateTimeHash": dt_hash, "Ticks": ticks}
