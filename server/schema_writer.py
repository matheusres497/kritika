import json
import os
import struct

import protocol

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(HERE, "schemas")

_ENUMS = None


def enums():
    global _ENUMS
    if _ENUMS is None:
        _ENUMS = json.load(open(os.path.join(SCHEMA_DIR, "enums.json"), encoding="utf-8"))
    return _ENUMS


ENUM_DEFAULTS = {
    "playerGender": "MALE", "classType": "WARRIOR", "ClassType": "WARRIOR", "AasState": "HEALTHY",
    "NoviceGuildJoin": "NONE", "GradeInGuild": "NONE", "MentorType": "NONE", "VipStep": "BRONZE",
}

_NUM = {"u8": "<B", "i8": "<b", "bool": "<B", "u16": "<H", "i16": "<h", "u32": "<I", "i32": "<i",
        "u64": "<Q", "i64": "<q", "f32": "<f", "f64": "<d"}


def load_schema(name):
    return json.load(open(os.path.join(SCHEMA_DIR, f"schema_{name}.json"), encoding="utf-8"))["fields"]


def write_fields(w, fields, overrides, path=""):
    for f in fields:
        op = f["op"]
        name = f.get("name", "")
        key = f"{path}{name}"
        if op == "begin":
            if f["type"] == "<message id>":
                w.begin(overrides["<message id>"])
            else:
                w.begin(protocol.h16(f["type"]))
        elif op == "struct":
            ov = overrides.get(key)
            if isinstance(ov, (bytes, bytearray)):
                w.raw(ov)
                continue
            w.field_tag(name)
            sub = f.get("fields")
            if not isinstance(sub, list):
                raise ValueError(f"struct {key} has no resolved fields")
            write_fields(w, sub, overrides, key + ".")
        elif op == "container":
            ov = overrides.get(key)
            if isinstance(ov, (bytes, bytearray)):
                w.raw(ov)
            else:
                w.field_tag(name).begin(protocol.h16(f["kind"])).u32(0)
        elif op == "enum":
            w.wstr(overrides.get(key, ENUM_DEFAULTS.get(f["enum"], "NONE")))
        elif op == "wstr":
            w.wstr(overrides.get(key, ""))
        elif op == "str":
            w.str8(overrides.get(key, ""))
        elif op == "raw":
            v = overrides.get(key, bytes(f["size"]))
            w.raw(bytes(v).ljust(f["size"], b"\0")[:f["size"]])
        elif op in _NUM:
            w.raw(struct.pack(_NUM[op], overrides.get(key, 0)))
        else:
            raise ValueError(f"cannot write field {key} with op {op}")
    return w


def build_struct(schema_name, overrides=None):
    w = protocol.Writer()
    write_fields(w, load_schema(schema_name), overrides or {})
    return bytes(w.buf)


def build_message(schema_name, full_name, overrides=None):
    ov = dict(overrides or {})
    ov["<message id>"] = protocol.registered_id(full_name)
    w = protocol.Writer()
    write_fields(w, load_schema(schema_name), ov)
    return protocol.packet(bytes(w.buf))


def nested_empty(field, outer_kind, inner_kind, count):
    w = protocol.Writer()
    w.field_tag(field).begin(protocol.h16(outer_kind)).u32(count)
    for i in range(count):
        w.field_tag(f"item_{i}").begin(protocol.h16(inner_kind)).u32(0)
    return bytes(w.buf)


EQUIP_SLOTS_PER_TAB = [22, 14, 14, 8]


def empty_item(w, tag):
    w.field_tag(tag).begin(protocol.h16("Apple::Item"))
    w.u16(0)
    w.u32(0)


def empty_equip_items(field="EquipItems"):
    w = protocol.Writer()
    w.field_tag(field).begin(protocol.h16("array")).u32(len(EQUIP_SLOTS_PER_TAB))
    for i, n in enumerate(EQUIP_SLOTS_PER_TAB):
        w.field_tag(f"item_{i}").begin(protocol.h16("array")).u32(n)
        for j in range(n):
            empty_item(w, f"item_{j}")
    return bytes(w.buf)


def empty_selected_titles(count=5):
    w = protocol.Writer()
    w.field_tag("SelectedTitles").begin(protocol.h16("array")).u32(count)
    for i in range(count):
        w.field_tag(f"item_{i}").begin(protocol.h16("Apple::Title")).wstr("")
    return bytes(w.buf)


def one_empty_quickslot_tab():
    w = protocol.Writer()
    w.field_tag("QuickSlots").begin(protocol.h16("array")).u32(1)
    w.field_tag("item_0").begin(protocol.h16("associative_array")).u32(0)
    return bytes(w.buf)
