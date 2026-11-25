import math
import struct

OPS = ("MOVE LOADK LOADBOOL LOADNIL GETUPVAL GETGLOBAL GETTABLE SETGLOBAL SETUPVAL SETTABLE NEWTABLE SELF ADD "
       "SUB MUL DIV MOD POW UNM NOT LEN CONCAT JMP EQ LT LE TEST TESTSET CALL TAILCALL RETURN FORLOOP FORPREP "
       "TFORLOOP SETLIST CLOSE CLOSURE VARARG").split()
FPF = 50


class LuaError(Exception):
    pass


class LuaTable:
    __slots__ = ("h", "meta")

    def __init__(self, d=None):
        self.h = dict(d or {})
        self.meta = None

    @staticmethod
    def _k(k):
        if isinstance(k, float) and k.is_integer():
            return int(k)
        return k

    def get(self, k):
        return self.h.get(self._k(k))

    def set(self, k, v):
        k = self._k(k)
        if k is None:
            raise LuaError("table index is nil")
        if v is None:
            self.h.pop(k, None)
        else:
            self.h[k] = v

    def length(self):
        n = 0
        while (n + 1) in self.h:
            n += 1
        return n

    def __repr__(self):
        return "LuaTable(%r)" % (self.h,)

    def to_py(self, depth=8):
        out = {}
        for k, v in self.h.items():
            out[k] = v.to_py(depth - 1) if isinstance(v, LuaTable) and depth else v
        return out


class Proto:
    pass


class Closure:
    __slots__ = ("p", "up")

    def __init__(self, p, up):
        self.p, self.up = p, up


class Cell:
    __slots__ = ("v",)

    def __init__(self, v=None):
        self.v = v


def load(data):
    if data[:5] != b"\x1bLuaQ":
        raise LuaError("not Lua 5.1 bytecode")
    assert data[6:12] == b"\x01\x04\x04\x04\x08\x00", data[6:12].hex()
    o = [12]

    def u8():
        v = data[o[0]]; o[0] += 1; return v

    def i32():
        v = struct.unpack_from("<i", data, o[0])[0]; o[0] += 4; return v

    def string():
        n = struct.unpack_from("<I", data, o[0])[0]; o[0] += 4
        if n == 0:
            return None
        s = data[o[0]:o[0] + n - 1]; o[0] += n
        return s.decode("latin-1")

    def func():
        p = Proto()
        p.source = string()
        p.line, p.lastline = i32(), i32()
        p.nups, p.nparams, p.vararg, p.maxstack = u8(), u8(), u8(), u8()
        n = i32()
        p.code = list(struct.unpack_from(f"<{n}I", data, o[0])); o[0] += 4 * n
        p.k = []
        for _ in range(i32()):
            t = u8()
            if t == 0:
                p.k.append(None)
            elif t == 1:
                p.k.append(bool(u8()))
            elif t == 3:
                v = struct.unpack_from("<d", data, o[0])[0]; o[0] += 8
                p.k.append(v)
            elif t == 4:
                p.k.append(string())
            else:
                raise LuaError("bad constant type %d" % t)
        p.p = [func() for _ in range(i32())]
        n = i32()
        o[0] += 4 * n
        for _ in range(i32()):
            string(); i32(); i32()
        p.upnames = [string() for _ in range(i32())]
        return p

    return func()


def truthy(v):
    return v is not None and v is not False


def tostr(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return "%.14g" % v
    if isinstance(v, int):
        return str(v)
    return str(v)


def tonum(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        try:
            return float(int(v, 0)) if v.strip().lower().startswith("0x") else float(v)
        except ValueError:
            return None
    return None


def arith(op, a, b):
    x, y = tonum(a), tonum(b)
    if x is None or y is None:
        raise LuaError(f"attempt to perform arithmetic ({op}) on {type(a).__name__}/{type(b).__name__}: {a!r} {b!r}")
    if op == "ADD":
        return x + y
    if op == "SUB":
        return x - y
    if op == "MUL":
        return x * y
    if op == "DIV":
        if y == 0:
            return math.inf if x > 0 else (-math.inf if x < 0 else math.nan)
        return x / y
    if op == "MOD":
        return x - math.floor(x / y) * y
    if op == "POW":
        return float(x) ** y


class LuaVM:
    def __init__(self):
        self.globals = LuaTable()
        self._stdlib()


    def _stdlib(self):
        g = self.globals
        m = LuaTable({"floor": lambda x: [float(math.floor(x))], "ceil": lambda x: [float(math.ceil(x))],
                      "abs": lambda x: [abs(x)], "sqrt": lambda x: [math.sqrt(x)],
                      "max": lambda *a: [max(a)], "min": lambda *a: [min(a)], "pi": math.pi, "huge": math.inf,
                      "pow": lambda a, b: [float(a) ** b], "random": self._random, "randomseed": lambda *a: [],
                      "fmod": lambda a, b: [math.fmod(a, b)], "sin": lambda x: [math.sin(x)],
                      "cos": lambda x: [math.cos(x)], "atan2": lambda y, x: [math.atan2(y, x)],
                      "acos": lambda x: [math.acos(x)], "log": lambda x: [math.log(x)],
                      "exp": lambda x: [math.exp(x)], "modf": lambda x: [float(math.trunc(x)), x - math.trunc(x)]})
        s = LuaTable({"format": lambda f, *a: [self._format(f, *a)], "len": lambda x: [len(x)],
                      "sub": self._sub, "upper": lambda x: [x.upper()], "lower": lambda x: [x.lower()],
                      "find": self._find, "rep": lambda x, n: [x * int(n)]})
        t = LuaTable({"insert": self._tinsert, "remove": self._tremove, "getn": lambda t: [t.length()],
                      "maxn": lambda t: [float(max([k for k in t.h if isinstance(k, (int, float))], default=0))],
                      "concat": lambda t, sep="", *a: [sep.join(tostr(t.get(i)) for i in range(1, t.length() + 1))]})
        for k, v in {"math": m, "string": s, "table": t, "print": lambda *a: print(*map(tostr, a)) or [],
                     "type": lambda v: [self._type(v)], "tostring": lambda v=None: [tostr(v)],
                     "tonumber": lambda v=None, b=None: [tonum(v) if b is None else float(int(v, int(b)))],
                     "pairs": lambda t: [self._next, t, None], "next": self._next,
                     "ipairs": lambda t: [self._inext, t, 0.0], "select": self._select,
                     "unpack": lambda t, i=1, j=None: [t.get(k) for k in range(int(i), int(j or t.length()) + 1)],
                     "error": self._error, "assert": lambda v=None, msg="assertion failed!", *a: [v, msg, *a]
                     if truthy(v) else self._error(msg), "pcall": self._pcall,
                     "setmetatable": self._setmeta, "getmetatable": lambda t: [getattr(t, "meta", None)],
                     "rawget": lambda t, k: [t.get(k)], "rawset": lambda t, k, v: (t.set(k, v), [t])[1]}.items():
            g.set(k, v)
        g.set("_G", g)

    random_source = None

    def _random(self, a=None, b=None):
        import random
        r = self.random_source or random
        if a is None:
            return [r.random()]
        if b is None:
            return [float(r.randint(1, int(a)))]
        return [float(r.randint(int(a), int(b)))]

    @staticmethod
    def _type(v):
        if v is None:
            return "nil"
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, (int, float)):
            return "number"
        if isinstance(v, str):
            return "string"
        if isinstance(v, LuaTable):
            return "table"
        if isinstance(v, Closure) or callable(v):
            return "function"
        return "userdata"

    @staticmethod
    def _format(f, *a):
        import re
        a = list(a)
        out = []

        def rep(m):
            spec = m.group(0)
            if spec == "%%":
                return "%"
            v = a.pop(0)
            if spec[-1] in "di":
                return (spec[:-1] + "d") % int(v)
            if spec[-1] == "s":
                return spec % tostr(v)
            return spec % v
        return re.sub(r"%[-+ #0]*\d*(?:\.\d+)?[diouxXeEfgGqcs%]", rep, f)

    @staticmethod
    def _sub(s, i=1, j=-1):
        n = len(s)
        i, j = int(i), int(j)
        if i < 0:
            i = max(n + i + 1, 1)
        if j < 0:
            j = n + j + 1
        i = max(i, 1)
        return [s[i - 1:j]]

    @staticmethod
    def _find(s, pat, init=1, plain=False):
        i = s.find(pat, int(init) - 1)
        return [None] if i < 0 else [float(i + 1), float(i + len(pat))]

    @staticmethod
    def _tinsert(t, a, b=None):
        if b is None:
            t.set(t.length() + 1, a)
        else:
            pos = int(a)
            for k in range(t.length(), pos - 1, -1):
                t.set(k + 1, t.get(k))
            t.set(pos, b)
        return []

    @staticmethod
    def _tremove(t, pos=None):
        n = t.length()
        pos = n if pos is None else int(pos)
        v = t.get(pos)
        for k in range(pos, n):
            t.set(k, t.get(k + 1))
        t.set(n, None)
        return [v]

    @staticmethod
    def _next(t, k=None):
        keys = list(t.h.keys())
        if k is None:
            i = 0
        else:
            k = LuaTable._k(k)
            try:
                i = keys.index(k) + 1
            except ValueError:
                return [None]
        if i >= len(keys):
            return [None]
        return [keys[i], t.h[keys[i]]]

    @staticmethod
    def _inext(t, i):
        i = int(i) + 1
        v = t.get(i)
        return [None] if v is None else [float(i), v]

    @staticmethod
    def _select(n, *a):
        if n == "#":
            return [float(len(a))]
        return list(a[int(n) - 1:])

    @staticmethod
    def _error(msg=None, *a):
        raise LuaError(tostr(msg))

    def _pcall(self, f, *a):
        try:
            return [True, *self.call(f, *a)]
        except LuaError as e:
            return [False, str(e)]

    @staticmethod
    def _setmeta(t, m):
        t.meta = m
        return [t]


    def index(self, o, k):
        if isinstance(o, LuaTable):
            v = o.get(k)
            if v is None and o.meta is not None:
                ix = o.meta.get("__index")
                if ix is not None:
                    return self.index(ix, k) if isinstance(ix, LuaTable) else self.call(ix, o, k)[0]
            return v
        if isinstance(o, str):
            return self.globals.get("string").get(k)
        if o is None:
            raise LuaError(f"attempt to index a nil value (key {k!r})")
        v = getattr(o, k, None) if isinstance(k, str) else None
        if v is None and hasattr(o, "__getitem__"):
            try:
                v = o[k]
            except (KeyError, IndexError, TypeError):
                v = None
        return v

    def setindex(self, o, k, v):
        if isinstance(o, LuaTable):
            if o.get(k) is None and o.meta is not None and o.meta.get("__newindex") is not None:
                ni = o.meta.get("__newindex")
                if isinstance(ni, LuaTable):
                    return self.setindex(ni, k, v)
                return self.call(ni, o, k, v)
            o.set(k, v)
        else:
            setattr(o, k, v)


    def run_file(self, path):
        return self.run_bytes(open(path, "rb").read())

    def run_bytes(self, data):
        return self.call(Closure(load(data), []))

    def call(self, f, *args):
        if isinstance(f, Closure):
            return self._exec(f, list(args))
        if callable(f):
            r = f(*args)
            if r is None:
                return []
            if isinstance(r, list):
                return r
            if isinstance(r, tuple):
                return list(r)
            return [r]
        if isinstance(f, LuaTable) and f.meta is not None and f.meta.get("__call") is not None:
            return self.call(f.meta.get("__call"), f, *args)
        raise LuaError(f"attempt to call a {self._type(f)} value ({f!r})")

    def _exec(self, cl, args):
        p = cl.p
        R = [None] * (max(p.maxstack, len(args)) + 256)
        np = p.nparams
        for i in range(min(np, len(args))):
            R[i] = args[i]
        varargs = args[np:] if p.vararg else []
        K = p.k
        code = p.code
        pc = 0
        top = 0
        open_cells = {}

        def RK(x):
            return K[x & 0xFF] if x & 0x100 else R[x]

        def cell_for(i):
            c = open_cells.get(i)
            if c is None:
                c = open_cells[i] = Cell(R[i])
            return c

        def close(frm):
            for i in [i for i in open_cells if i >= frm]:
                open_cells.pop(i)


        def get(i):
            c = open_cells.get(i)
            return c.v if c is not None else R[i]

        def put(i, v):
            c = open_cells.get(i)
            if c is not None:
                c.v = v
            R[i] = v

        while True:
            ins = code[pc]
            pc += 1
            op = OPS[ins & 0x3F]
            a = (ins >> 6) & 0xFF
            c = (ins >> 14) & 0x1FF
            b = (ins >> 23) & 0x1FF
            bx = ins >> 14
            sbx = bx - 131071
            if op == "MOVE":
                put(a, get(b))
            elif op == "LOADK":
                put(a, K[bx])
            elif op == "LOADBOOL":
                put(a, bool(b))
                if c:
                    pc += 1
            elif op == "LOADNIL":
                for i in range(a, b + 1):
                    put(i, None)
            elif op == "GETUPVAL":
                put(a, cl.up[b].v)
            elif op == "GETGLOBAL":
                put(a, self.index(self.globals, K[bx]))
            elif op == "GETTABLE":
                put(a, self.index(get(b), K[c & 0xFF] if c & 0x100 else get(c)))
            elif op == "SETGLOBAL":
                self.globals.set(K[bx], get(a))
            elif op == "SETUPVAL":
                cl.up[b].v = get(a)
            elif op == "SETTABLE":
                self.setindex(get(a), K[b & 0xFF] if b & 0x100 else get(b), K[c & 0xFF] if c & 0x100 else get(c))
            elif op == "NEWTABLE":
                put(a, LuaTable())
            elif op == "SELF":
                o = get(b)
                put(a + 1, o)
                put(a, self.index(o, K[c & 0xFF] if c & 0x100 else get(c)))
            elif op in ("ADD", "SUB", "MUL", "DIV", "MOD", "POW"):
                x = K[b & 0xFF] if b & 0x100 else get(b)
                y = K[c & 0xFF] if c & 0x100 else get(c)
                put(a, arith(op, x, y))
            elif op == "UNM":
                put(a, -tonum(get(b)))
            elif op == "NOT":
                put(a, not truthy(get(b)))
            elif op == "LEN":
                v = get(b)
                put(a, float(len(v)) if isinstance(v, str) else float(v.length()))
            elif op == "CONCAT":
                put(a, "".join(tostr(get(i)) for i in range(b, c + 1)))
            elif op == "JMP":
                pc += sbx
            elif op in ("EQ", "LT", "LE"):
                x = K[b & 0xFF] if b & 0x100 else get(b)
                y = K[c & 0xFF] if c & 0x100 else get(c)
                if op == "EQ":
                    r = (x == y) and (type(x) is type(y) or (self._type(x) == "number" == self._type(y)))
                    if isinstance(x, bool) or isinstance(y, bool):
                        r = x is y
                elif op == "LT":
                    r = x < y
                else:
                    r = x <= y
                if r != bool(a):
                    pc += 1
            elif op == "TEST":
                if truthy(get(a)) != bool(c):
                    pc += 1
            elif op == "TESTSET":
                v = get(b)
                if truthy(v) == bool(c):
                    put(a, v)
                else:
                    pc += 1
            elif op in ("CALL", "TAILCALL"):
                f = get(a)
                n = (b - 1) if b else (top - a - 1)
                res = self.call(f, *[get(a + 1 + i) for i in range(n)])
                if op == "TAILCALL":
                    close(0)
                    return res
                if c == 0:
                    for i, v in enumerate(res):
                        put(a + i, v)
                    top = a + len(res)
                else:
                    for i in range(c - 1):
                        put(a + i, res[i] if i < len(res) else None)
            elif op == "RETURN":
                n = (b - 1) if b else (top - a)
                res = [get(a + i) for i in range(n)]
                close(0)
                return res
            elif op == "FORLOOP":
                step = get(a + 2)
                idx = get(a) + step
                put(a, idx)
                lim = get(a + 1)
                if (step > 0 and idx <= lim) or (step <= 0 and idx >= lim):
                    pc += sbx
                    put(a + 3, idx)
            elif op == "FORPREP":
                put(a, tonum(get(a)) - tonum(get(a + 2)))
                put(a + 1, tonum(get(a + 1)))
                put(a + 2, tonum(get(a + 2)))
                pc += sbx
            elif op == "TFORLOOP":
                res = self.call(get(a), get(a + 1), get(a + 2))
                for i in range(c):
                    put(a + 3 + i, res[i] if i < len(res) else None)
                if get(a + 3) is not None:
                    put(a + 2, get(a + 3))
                else:
                    pc += 1
            elif op == "SETLIST":
                n = b if b else (top - a - 1)
                if c == 0:
                    c = code[pc]
                    pc += 1
                t = get(a)
                base = (c - 1) * FPF
                for i in range(1, n + 1):
                    t.set(base + i, get(a + i))
            elif op == "CLOSE":
                close(a)
            elif op == "CLOSURE":
                np_ = p.p[bx]
                ups = []
                for _ in range(np_.nups):
                    pi = code[pc]
                    pc += 1
                    pop = OPS[pi & 0x3F]
                    pb = (pi >> 23) & 0x1FF
                    ups.append(cell_for(pb) if pop == "MOVE" else cl.up[pb])
                put(a, Closure(np_, ups))
            elif op == "VARARG":
                n = (b - 1) if b else len(varargs)
                for i in range(n):
                    put(a + i, varargs[i] if i < len(varargs) else None)
                if b == 0:
                    top = a + n
            else:
                raise LuaError("bad opcode " + op)
