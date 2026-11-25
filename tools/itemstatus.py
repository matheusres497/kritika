import os, struct
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "alp", "Database")


def _ws(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    return b[o + 4:o + 4 + 2 * n].decode("utf-16-le").rstrip("\0"), o + 4 + 2 * n


def _inner(b, o):
    n = struct.unpack_from("<I", b, o)[0]; o += 4
    m = {}
    for _ in range(n):
        k, o = _ws(b, o)
        m[k] = struct.unpack_from("<f", b, o)[0]; o += 4
    return m, o


def by_level():
    b = open(os.path.join(DB, "ItemStatusByLevel.b"), "rb").read()
    n = struct.unpack_from("<I", b, 0)[0]; o = 4; out = {}
    for _ in range(n):
        k = struct.unpack_from("<I", b, o)[0]; o += 4
        out[k], o = _inner(b, o)
    assert o == len(b), (o, len(b))
    return out


def by_sub_category():
    b = open(os.path.join(DB, "ItemStatusByEquipSubCategory.b"), "rb").read()
    n = struct.unpack_from("<I", b, 0)[0]; o = 4; out = {}
    for _ in range(n):
        k, o = _ws(b, o)
        out[k], o = _inner(b, o)
    assert o == len(b), (o, len(b))
    return out


if __name__ == "__main__":
    lv = by_level(); sc = by_sub_category()
    ks = sorted(lv); print(len(ks), ks[:12], ks[-5:])
    print('key 1:', {k: v for k, v in lv[ks[0]].items() if v})
    print([k for k in sc if 'SWORD' in k.upper() or 'KNIGHT' in k.upper()][:20])
