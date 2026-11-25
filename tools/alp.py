import os
import struct
import sys
import zlib

KEY = 0x036F97DF


def unxor(b):
    pad = (-len(b)) % 4
    w = struct.unpack(f"<{(len(b) + pad) // 4}I", b + b"\0" * pad)
    return struct.pack(f"<{len(w)}I", *(x ^ KEY for x in w))[:len(b)]


class Reader:
    def __init__(self, data, off):
        self.d, self.o = data, off

    def record(self):
        n = struct.unpack_from("<I", self.d, self.o)[0]
        r = unxor(self.d[self.o + 4:self.o + 4 + n])
        self.o += 4 + n
        return r


def parse_dir(r, path, out):
    b = r.record()
    assert b[:4] == b"DGKP", b[:8]
    ver, ndirs, nfiles, nlen = struct.unpack_from("<HIIH", b, 4)
    name = b[16:16 + 2 * nlen].decode("utf-16-le")
    here = path + "/" + name if path is not None else ""
    for _ in range(ndirs):
        parse_dir(r, here, out)
    for _ in range(nfiles):
        f = r.record()
        assert f[:4] == b"FGKP", f[:8]
        o = 4
        fver = struct.unpack_from("<H", f, o)[0]; o += 2
        extra = None
        if fver > 3:
            extra = f[o]; o += 1
        flags = f[o:o + 5]; o += 5
        a, b2, c, s1, s2, d = struct.unpack_from("<IIIHHI", f, o); o += 20
        e = 0
        if fver:
            e = struct.unpack_from("<I", f, o)[0]; o += 4
        n = struct.unpack_from("<H", f, o)[0]; o += 2
        fname = f[o:o + 2 * n].decode("utf-16-le")
        out.append(dict(path=here + "/" + fname, ver=fver, extra=extra, flags=flags.hex(),
                        a=a, b=b2, c=c, s1=s1, s2=s2, d=d, e=e))


def listing(alph):
    h = open(alph, "rb").read()
    assert h[:4] == b"GKPA"

    hdr_size = struct.unpack_from("<I", h, 6)[0]
    out = []
    parse_dir(Reader(h, hdr_size), None, out)
    return out


def main():
    cmd, alph = sys.argv[1], sys.argv[2]
    files = listing(alph)
    flt = (sys.argv[4] if cmd == "extract" else sys.argv[3]) if len(sys.argv) > (4 if cmd == "extract" else 3) else ""
    files = [f for f in files if flt.lower() in f["path"].lower()]
    if cmd == "list":
        for f in files:
            print(f["path"], f["flags"], f["a"], f["b"], f["c"], f["s1"], f["s2"], hex(f["d"]), hex(f["e"]), f["extra"])
        print(len(files), "files", file=sys.stderr)
    else:
        data = open(alph[:-1], "rb").read()
        outdir = sys.argv[3]
        bad = 0
        for f in files:
            try:
                blob = read_file(data, f)
            except Exception as ex:
                bad += 1
                print("FAIL", f["path"], ex, file=sys.stderr)
                continue
            p = os.path.join(outdir, f["path"].lstrip("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(blob)
        print(len(files) - bad, "extracted,", bad, "failed", file=sys.stderr)


def read_file(data, f):
    import lzma
    raw = data[f["a"]:f["a"] + f["c"]]
    if f["b"] == f["c"] and f["flags"] == "0000000000":
        return raw
    props = bytes.fromhex(f["flags"])
    p, dic = props[0], struct.unpack_from("<I", props, 1)[0]
    filt = [{"id": lzma.FILTER_LZMA1, "lc": p % 9, "lp": (p // 9) % 5, "pb": p // 45, "dict_size": dic}]
    for cand in (raw, unxor(raw)):
        try:
            out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filt).decompress(cand, f["b"])
            if len(out) == f["b"]:
                return out
        except lzma.LZMAError:
            pass
    raise ValueError("cannot unpack")


if __name__ == "__main__":
    main()
