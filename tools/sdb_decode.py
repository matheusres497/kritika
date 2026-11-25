import json
import re
import os
import sqlite3
import struct
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rtti_decode as _RD
    if os.environ.get("KRITIKA_GENERIC") == "0":
        _RD = None
except Exception:
    _RD = None


def _generic(b, o, f):
    if _RD is None:
        return None
    try:
        return _RD.decode_container(b, o, f.get("rtti", ""), sys.modules[__name__])
    except Exception:
        return None


SDB = os.environ["KRITIKA_SDB"]
SIZES = {"u8": "B", "i8": "b", "bool": "B", "u16": "H", "i16": "h", "u32": "I", "i32": "i", "f32": "f",
         "u64": "Q", "i64": "q", "f64": "d"}


def nstr(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    return b[o + 4:o + 4 + n].rstrip(b"\0").decode("latin-1"), o + 4 + n


def wstr(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    if n > 4000:
        raise ValueError(f"string length {n} at {o}")
    s = b[o + 4:o + 4 + 2 * n].decode("utf-16-le").rstrip("\0")
    return s, o + 4 + 2 * n


def item(b, o, rtti):
    if rtti.startswith(".?AU?$WriterWrapper@$$CBV?$"):
        rtti = ".?AV?$" + rtti[len(".?AU?$WriterWrapper@$$CBV?$"):]
    if rtti.startswith(".?AV?$ContainerSerialize@V?$vector@V?$vector@V?$basic_string@_W"):
        n = struct.unpack_from("<I", b, o)[0]
        o += 4
        vals = []
        for _ in range(n):
            s, o = wstr(b, o)
            vals.append(s)
        return vals, o
    mnum = re.match(r"\.\?AV\?\$ContainerSerialize@V\?\$vector@U\?\$pair@([MNHI])([MNHI])@std@@", rtti)
    if mnum:
        fa, fb = MAP_VALUE[mnum.group(1)], MAP_VALUE[mnum.group(2)]
        x = struct.unpack_from("<" + fa, b, o)[0]; o += struct.calcsize(fa)
        y = struct.unpack_from("<" + fb, b, o)[0]; o += struct.calcsize(fb)
        return (x, y), o
    mw2 = re.match(r"\.\?AV\?\$ContainerSerialize@V\?\$vector@U\?\$pair@U\?\$Vector2@H@Math@AllM@@V\?\$basic_string@_W", rtti)
    if mw2:
        key = struct.unpack_from("<2i", b, o); o += 8
        v, o = wstr(b, o)
        return (key, v), o
    mv2 = re.match(r"\.\?AV\?\$ContainerSerialize@V\?\$vector@U\?\$pair@U\?\$Vector2@H@Math@AllM@@U((?:\w+@)+)@+std@@", rtti)
    if mv2:
        key = struct.unpack_from("<2i", b, o); o += 8
        sub = {}
        o = fields(b, o, element_schema("::".join(reversed(mv2.group(1).rstrip("@").split("@"))))["fields"], sub, 10 ** 6)
        return (key, sub), o
    mp = re.match(r"\.\?AV\?\$ContainerSerialize@V\?\$vector@U\?\$pair@V\?\$basic_string@_W", rtti)
    if mp:

        first = rtti.split("@std@@", 2)
        k, o = wstr(b, o)
        tail = rtti.split("@2@@std@@", 1)[1]
        if tail.startswith("V12@") or tail.startswith("VLocaleType@Locale@AllM@@"):
            v, o = wstr(b, o)
            return (k, v), o
        if tail.startswith("V?$vector@V?$basic_string@_W"):
            n = struct.unpack_from("<I", b, o)[0]; o += 4
            vals = []
            for _ in range(n):
                x, o = wstr(b, o)
                vals.append(x)
            return (k, vals), o
        ms = re.match(r"U((?:\w+@)+)@+std@@", tail)
        if ms:
            sub = {}
            o = fields(b, o, element_schema("::".join(reversed(ms.group(1).rstrip("@").split("@"))))["fields"], sub, 10 ** 6)
            return (k, sub), o
        t = re.match(r"(_J|_K|[MNHI]|_N)@std@@", tail)
        if not t:
            raise ValueError("unsupported pair value " + rtti[:420])
        f = MAP_VALUE[t.group(1)]
        v = struct.unpack_from("<" + f, b, o)[0]; o += struct.calcsize(f)
        return (k, v), o
    if "basic_string@_W" in rtti.split("allocator")[0] or "@VLocator@AllM@@" in rtti.split("allocator")[0]:
        return wstr(b, o)
    m = re.search(r"\?\$Vector([234])@M@Math@AllM@@", rtti.split("allocator")[0])
    if m:
        k = int(m.group(1))
        return list(struct.unpack_from(f"<{k}f", b, o)), o + 4 * k
    for t, f in (("@M@", "f"), ("@H@", "i"), ("@I@", "I"), ("@_N@", "B")):
        if t in rtti[:60]:
            return struct.unpack_from("<" + f, b, o)[0], o + struct.calcsize(f)
    m = re.match(r"\.\?AV\?\$ContainerSerialize@V\?\$(?:vector|list|set)@U((?:\w+@)+?)@", rtti)
    if m:
        sch = element_schema("::".join(reversed(m.group(1).rstrip("@").split("@"))))
        sub = {}
        o = fields(b, o, sch["fields"], sub, 10 ** 6)
        return sub, o
    if "KeyedContainerSerialize" in rtti and NARROW_MAP in rtti:
        return nstr(b, o)
    raise ValueError("unsupported element " + rtti[:80])


_SCHEMAS = {}


def element_schema(cxx):
    if cxx not in _SCHEMAS:
        import subprocess
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        path = os.path.join(root, "server", "schemas", "schema_" + cxx.replace("::", "_") + ".json")
        parts = cxx.split("::")
        ww = ".?AU?$WriterWrapper@$$CBU" + "@".join(reversed(parts)) + "@@@Private@Serializer@AllM@@"
        def _empty():
            try:
                return not json.load(open(path))["fields"]
            except (OSError, ValueError, KeyError):
                return True
        for label in (cxx, ww):
            if _empty():
                subprocess.run([sys.executable, os.path.join(root, "tools", "schema.py"), label, "--json", path],
                               capture_output=True, timeout=1800)
        _SCHEMAS[cxx] = full_form(json.load(open(path)))
    return _SCHEMAS[cxx]


def full_form(sch):
    fs = sch["fields"]
    names = [f.get("name") for f in fs]
    if len([n for n in names if n]) == len(set(n for n in names if n)):
        return sch
    best = None
    for k in range(2, len(fs)):
        head = {n for n in names[1:k] if n}
        if head and head <= set(names[k:]):
            best = k
    if best is None:
        return sch
    lead = [f for f in fs[:1] if f.get("op") == "begin"]
    return dict(sch, fields=lead + fs[best:])


ITEM_SECTION_BITS = {"Expire": 1, "Equip": 2, "Extend": 3, "Usable": 4, "Enhance": 5, "Aoe": 6, "LockExpire": 7}
MAP_VALUE = {"M": "f", "N": "d", "H": "i", "I": "I", "_N": "B", "_J": "q", "_K": "Q"}


NARROW_MAP = "map@V?$basic_string@DU"


def map_value(b, o, rtti):
    import re
    if NARROW_MAP in rtti:
        return nstr(b, o)
    if "@2@@std@@V?$vector@U?$pair@V?$basic_string@_WU?$char_traits@_W@std@@V?$allocator@_W@2@@std@@M@std@@V?$allocator@U?$pair@" in rtti:
        n = struct.unpack_from("<I", b, o)[0]; o += 4
        vals = []
        for _ in range(n):
            k, o = wstr(b, o)
            vals.append((k, struct.unpack_from("<f", b, o)[0])); o += 4
        return vals, o
    if "U?$pair@H_N@2@U?$less" in rtti:
        return (struct.unpack_from("<i", b, o)[0], b[o + 4]), o + 5
    if "U?$Vector2@H@Math@AllM@@U?$less" in rtti:
        return struct.unpack_from("<2i", b, o), o + 8
    if "map@HV?$map@V?$basic_string@_WU?$char_traits@_W@std@@V?$allocator@_W@2@@std@@MU?$less" in rtti:
        n = struct.unpack_from("<I", b, o)[0]; o += 4
        inner = []
        for _ in range(n):
            k, o = wstr(b, o)
            inner.append((k, struct.unpack_from("<f", b, o)[0])); o += 4
        return inner, o
    if "V?$map@V?$basic_string@_WU?$char_traits@_W@std@@V?$allocator@_W@2@@std@@V?$vector@HV?$allocator@H@std@@@2@U?$less" in rtti:
        n = struct.unpack_from("<I", b, o)[0]; o += 4
        inner = []
        for _ in range(n):
            k, o = wstr(b, o)
            m = struct.unpack_from("<I", b, o)[0]; o += 4
            inner.append((k, list(struct.unpack_from(f"<{m}i", b, o)))); o += 4 * m
        return inner, o
    if "U?$Vector3@M@Math@AllM@@U?$less" in rtti:
        return struct.unpack_from("<3f", b, o), o + 12
    if "U?$Vector2@M@Math@AllM@@U?$less" in rtti:
        return struct.unpack_from("<2f", b, o), o + 8

    mv = re.search(r"(?:@std@@|@Math@AllM@@|\$map@[HIKM])V\?\$vector@(U((?:\w+@)+)@V\?\$allocator@U|V\?\$basic_string@_W|VLocator@AllM@@|[MHIN_]+@?V\?\$allocator)", rtti)
    if mv and "KeyedContainerSerialize" in rtti:
        n = struct.unpack_from("<I", b, o)[0]; o += 4
        vals = []
        for _ in range(n):
            if mv.group(2):
                sch = element_schema("::".join(reversed(mv.group(2).rstrip("@").split("@"))))
                sub = {}
                o = fields(b, o, sch["fields"], sub, 10 ** 6)
                vals.append(sub)
            elif mv.group(1).startswith(("V?$basic_string", "VLocator")):
                x, o = wstr(b, o); vals.append(x)
            else:
                raise ValueError("unsupported map vector value " + rtti[:120])
        return vals, o
    if "@std@@V12@@2@U?$less" in rtti:
        a, o = wstr(b, o)
        c, o = wstr(b, o)
        return (a, c), o
    ms = re.search(r"map@[IKHM]U((?:\w+@)+)@U\?\$less", rtti)
    if ms:
        sch = element_schema("::".join(reversed(ms.group(1).rstrip("@").split("@"))))
        sub = {}
        o = fields(b, o, sch["fields"], sub, 10 ** 6)
        return sub, o
    ms = re.search(r"\$allocator@_W@2@@std@@U((?:\w+@)+)@U\?\$less", rtti)
    if ms:
        sch = element_schema("::".join(reversed(ms.group(1).rstrip("@").split("@"))))
        sub = {}
        o = fields(b, o, sch["fields"], sub, 10 ** 6)
        return sub, o
    if re.match(r"\.\?AV\?\$KeyedContainerSerialize@V\?\$map@[IKHM]V\?\$basic_string@_W", rtti):
        return wstr(b, o)
    mm = re.search(r"\$map@([MHI])([MHI])U\?\$less", rtti)
    if mm:
        f = MAP_VALUE[mm.group(2)]
        return struct.unpack_from("<" + f, b, o)[0], o + struct.calcsize(f)
    m = re.search(r"@std@@(_N|_J|_K|[MNHI])U\?\$less", rtti)
    if not m:
        return wstr(b, o)
    f = MAP_VALUE[m.group(1)]
    return struct.unpack_from("<" + f, b, o)[0], o + struct.calcsize(f)


def fields(b, o, fs, out, limit):
    srcmap = {}
    for f in fs:
        if len(out) >= limit:
            return o
        op, name = f["op"], f.get("name") or "?"
        if op == "begin":
            continue
        guard = f.get("cond_on_src")
        if guard is not None and guard in srcmap and not srcmap[guard]:
            continue
        if op == "str":
            v, o = nstr(b, o)
        elif op in ("wstr", "enum") or (op == "helper?" and f.get("func") in ("0x71bf20",)):

            v, o = wstr(b, o)
        elif op == "helper?" and f.get("func") == "0x761f00":


            k, o = wstr(b, o)
            v = (k, struct.unpack_from("<i", b, o)[0]); o += 4
        elif op == "helper?" and f.get("func") == "0x7f2020":


            k, o = wstr(b, o)
            v = (k, struct.unpack_from("<2f", b, o)); o += 8
        elif op in SIZES:
            v = struct.unpack_from("<" + SIZES[op], b, o)[0]; o += struct.calcsize(SIZES[op])
        elif op == "raw":
            size = f["size"]
            if name == "FanAngle":
                size += 4
            v = b[o:o + size]; o += size
            v = list(struct.unpack(f"<{len(v)//4}f", v)) if len(v) % 4 == 0 else v.hex()
        elif op == "struct":
            m = re.match(r"V\?\$EmptySharingType@U(\w+)@ItemInfo@Sdb@Apple@@", f.get("type") or "")
            if m:

                bit = ITEM_SECTION_BITS[m.group(1)]
                if not (out.get("_flags", 0) >> bit) & 1:
                    out[name] = None
                    continue
                sub = {}
                o = fields(b, o, element_schema("Apple::Sdb::ItemInfo::" + m.group(1))["fields"], sub, 10 ** 6)
                v = sub
            else:
                sub = {}
                o = fields(b, o, f.get("fields") or [], sub, 10 ** 6)
                v = sub
        elif op == "container":
            g = _generic(b, o, f)
            if g is not None:
                v, o = g
                n = 0
                rt = ""
            else:
                n = struct.unpack_from("<I", b, o)[0]; o += 4
                rt = f.get("rtti", "")
                v = []
            for _ in range(n):
                if f["kind"] == "associative_array":
                    kt = ("map@" + rt.split("$map@", 1)[1]) if "$map@" in rt else rt
                    if kt.startswith("map@H"):
                        k = struct.unpack_from("<i", b, o)[0]; o += 4
                    elif kt.startswith("map@U?$Vector2@M@Math@AllM@@"):
                        k = struct.unpack_from("<2f", b, o); o += 8
                    elif "map@V?$basic_string@_W" in rt:
                        k, o = wstr(b, o)
                    elif "map@U?$Vector2@H@Math@AllM@@" in rt:
                        k = struct.unpack_from("<2i", b, o); o += 8
                    elif "map@I" in rt or "map@K" in rt:
                        k = struct.unpack_from("<I", b, o)[0]; o += 4
                    elif "map@M" in rt:
                        k = struct.unpack_from("<f", b, o)[0]; o += 4
                    elif "map@H" in rt:
                        k = struct.unpack_from("<i", b, o)[0]; o += 4
                    else:
                        k, o = item(b, o, rt)
                    val, o = map_value(b, o, rt)
                    v.append((k, val))
                else:
                    x, o = item(b, o, rt)
                    v.append(x)
        else:
            raise ValueError("op " + op)
        out[name] = v
        if f.get("src") is not None:
            srcmap[f["src"]] = v
    return o


_DB_CACHE = {}


def _load_db(db):
    c = _DB_CACHE.get(db)
    if c is None:
        con = sqlite3.connect(f"file:{os.path.join(SDB, db)}?mode=ro", uri=True)
        t = con.execute("select name from sqlite_master where type='table'").fetchone()[0]
        rows, index = [], {}
        for (b,) in con.execute(f"select SerializedBinary from {t}"):
            if not b:
                continue
            b = bytes(b)
            rows.append(b)
            if len(b) >= 4:
                n = struct.unpack_from("<I", b, 0)[0]
                if 1 <= n < 256 and len(b) >= 4 + 2 * n and b[2 + 2 * n:4 + 2 * n] == b"\0\0":
                    index.setdefault(b[:4 + 2 * n], b)
        con.close()
        c = _DB_CACHE[db] = (rows, index)
    return c


def record(db, key_id):
    rows, index = _load_db(db)
    k = struct.pack("<I", len(key_id) + 1) + (key_id + "\0").encode("utf-16-le")
    b = index.get(k)
    if b is not None:
        return b
    for b in rows:
        if k in b[:len(k) + 96]:
            return b
    return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    b = record(sys.argv[1], sys.argv[2])
    sch = full_form(json.load(open(sys.argv[3])))
    out = {}
    try:
        fields(b, 0, sch["fields"], out, int(sys.argv[4]) if len(sys.argv) > 4 else 10 ** 6)
    except Exception as e:
        out["__stopped__"] = repr(e)
    for k, v in out.items():
        print(k, "=", v)
