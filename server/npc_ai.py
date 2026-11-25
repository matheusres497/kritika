import math
import os
import random
import re
import sqlite3
import struct
import time

import protocol

import config

SDB_DIR = config.SDB_DIR
_actions_cache = {}


def action_hashes(class_id):
    if class_id not in _actions_cache:
        db = sqlite3.connect(f"file:{os.path.join(SDB_DIR, 'ActionList.db')}?mode=ro", uri=True)
        out = {}
        for h, b in db.execute("select Hash, SerializedBinary from ActionList where ClassId=?", (class_id,)):
            n = struct.unpack_from("<I", b, 0)[0]
            if 0 < n < 100:
                out[b[4:4 + 2 * (n - 1)].decode("utf-16-le", "replace")] = h & 0xFFFFFFFF
        _actions_cache[class_id] = out
    return _actions_cache[class_id]


def npc_action_list(npc_id):
    db = sqlite3.connect(f"file:{os.path.join(SDB_DIR, 'NpcList.db')}?mode=ro", uri=True)
    key = struct.pack("<I", len(npc_id) + 1) + (npc_id + "\0").encode("utf-16-le")
    for (b,) in db.execute("select SerializedBinary from NpcList"):
        if 0 <= b.find(key) < 40:
            strs = [m.decode("utf-16-le") for m in re.findall(rb"(?:[ -~]\x00){2,80}", b[:1200])]
            for i, s in enumerate(strs):
                if s.endswith(".modelinfo") and i + 2 < len(strs):
                    return strs[i + 2]
    return npc_id


def half(v):
    return struct.unpack("<H", struct.pack("<e", v))[0]


def action_sync(serial, action_serial, action_hash, pos, direction, moving, prev_serial):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::Both::ActionSystem::ActionSyncPacket"))
    w.u32(serial).u32(action_serial).u32(action_hash).raw(b"\0")
    w.raw(struct.pack("<3f", *pos))
    w.u16(half(direction[0])).u16(half(direction[1])).u16(half(moving[0])).u16(half(moving[1]))
    w.raw(struct.pack("<f", float(int(time.monotonic() * 1000) & 0xFFFFFF)))
    w.u32(random.getrandbits(32)).u32(prev_serial).raw(struct.pack("<f", 0.0)).raw(b"\x01")
    return protocol.packet(bytes(w.buf))


def destroy_actors(serials, attacker=1):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::StageSystem::DestroyActors"))
    w.field_tag("Serials").begin(protocol.h16("array")).u32(len(serials))
    for s in sorted(serials):
        w.u32(s)
    w.field_tag("LastAttackedSerials").begin(protocol.h16("associative_array")).u32(len(serials))
    for i, s in enumerate(sorted(serials)):
        w.u32(s).u32(attacker)
    return protocol.packet(bytes(w.buf))


_NPC_INFO = {}


def npc_info(npc_id):
    if npc_id not in _NPC_INFO:
        _NPC_INFO[npc_id] = _npc_info(npc_id)
    return _NPC_INFO[npc_id]


def _npc_info(npc_id):
    import json
    import sdb_decode
    sch = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "schema_NpcInfo.json")))
    out = {}
    try:
        sdb_decode.fields(sdb_decode.record("NpcList.db", npc_id), 0, sch["fields"], out, 10 ** 6)
    except Exception:
        pass
    return out


ATTACK_RANGE, RUN_SPEED = 3.5, 6.0
ATTACK_COOLDOWN, HIT_RECOVER, DIE_REMOVE = 2.5, 1.2, 2.5


class Npc:
    def __init__(self, serial, npc_id, pos2d):
        self.serial, self.npc_id = serial, npc_id
        self.x, self.z = pos2d
        self.y = None
        info = npc_info(npc_id)
        self.sight = info.get("Sight", 0.0)
        self.actions = action_hashes(info.get("ActionListId") or npc_action_list(npc_id))
        self.action_serial = 0
        self.state, self.until = "idle", 0.0
        self.next_attack = 0.0
        self.dir = (0.0, 1.0)
        self.sent_dir = (0.0, 1.0)

    def act(self, name, moving=(0.0, 0.0), y=0.0):
        h = self.actions.get(name)
        if h is None:
            return []
        prev = self.action_serial
        self.action_serial += 1
        return [action_sync(self.serial, self.action_serial, h, (self.x, self.y if self.y is not None else y, self.z),
                            self.dir, moving, prev)]


def on_hit(state, serial, now):
    n = state.get("ai", {}).get(serial)
    if n and n.state != "dead":
        n.state, n.until = "hit", now + HIT_RECOVER


def on_death(state, serial, now):
    n = state.get("ai", {}).get(serial)
    if n and n.state != "dead":
        n.state, n.until = "dead", now + DIE_REMOVE
        return n.act("Troll_Die") or n.act("DeadDownStart")
    return []


def tick(state, log):
    now = time.monotonic()
    ai = state.setdefault("ai", {})
    player = state.get("player_pos")
    out = []
    for serial, n in list(ai.items()):
        if n.state == "dead":
            if now >= n.until:
                out.append(destroy_actors([serial]))
                del ai[serial]
            continue
        if n.state == "hit":
            if now >= n.until:
                out += n.act("Rise") or n.act("IdleStand")
                n.state = "idle"
            continue
        if player is None:
            continue
        px, py, pz = player


        n.y = state.get("player_ground_y", py)
        dx, dz = px - n.x, pz - n.z
        dist = math.hypot(dx, dz)
        if dist > n.sight:
            if n.state != "idle":
                out += n.act("IdleStand")
                n.state = "idle"
            continue
        n.dir = (dx / dist, dz / dist) if dist > 1e-3 else n.dir
        if dist > ATTACK_RANGE:
            dt = now - getattr(n, "last_move", now)
            n.last_move = now
            if n.state == "chase":
                step = min(RUN_SPEED * dt, max(0.0, dist - ATTACK_RANGE * 0.8))
                n.x += n.sent_dir[0] * step
                n.z += n.sent_dir[1] * step
            turn = n.state != "chase" or (n.dir[0] * n.sent_dir[0] + n.dir[1] * n.sent_dir[1]) < 0.87
            if turn:
                n.state, n.sent_dir = "chase", n.dir
                out += n.act("Run", moving=n.dir)
        elif now >= n.next_attack:
            n.state, n.next_attack = "attack", now + ATTACK_COOLDOWN
            out += n.act("Troll_Atk01")
    return out
