KEY = 0x14


def _f(x: int) -> int:
    return ((x * 0x49) & 0xFF) ^ 0x15


def _build_tables(key: int = KEY):
    enc = [bytearray(256) for _ in range(256)]
    dec = [bytearray(256) for _ in range(256)]
    for i in range(256):
        row = _f(i)
        for u in range(256):
            c = ((_f(u) + _f(row)) & 0xFF) ^ key
            enc[row][u] = c
            dec[row][c] = u
    return enc, dec


ENC, DEC = _build_tables()


def row_for(counter: int) -> int:
    counter &= 0xFFFFFFFF
    b = (counter + 4) & 0xFF
    for shift in (8, 16, 24):
        b = (b + _f((counter >> shift) & 0xFF)) & 0xFF
    return b


def _crc_table():
    t = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x8005) if c & 0x8000 else (c << 1)
            c &= 0xFFFF
        t.append(c)
    return t


CRC_TABLE = _crc_table()


def crc16(data: bytes) -> int:
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFF) ^ CRC_TABLE[((c >> 8) ^ b) & 0xFF]
    return c


class Cryptor:

    def __init__(self, send_counter: int, recv_counter: int, mode: int):
        self.send_counter = send_counter & 0xFFFFFFFF
        self.recv_counter = recv_counter & 0xFFFFFFFF
        self.mode = mode
        self.seq = 0

    def _apply(self, buf: bytearray, start: int, end: int, table, attr: str):
        n = getattr(self, attr)
        for i in range(start, end):
            buf[i] = table[row_for(n)][buf[i]]
            n = (n + 1) & 0xFFFFFFFF
        setattr(self, attr, n)

    def encode(self, plain: bytes) -> bytes:
        if len(plain) < 4:
            raise ValueError("packet shorter than the 4 clear bytes")
        extra = 3 if self.mode == 0 else 0
        buf = bytearray(plain) + bytearray(extra)
        size = len(buf)
        buf[0:2] = size.to_bytes(2, "little")
        if self.mode == 0:
            buf[size - 3] = self.seq
            self.seq = (self.seq + 1) & 0xFF
            buf[size - 2:size] = crc16(bytes(buf[: size - 2])).to_bytes(2, "big")
        self._apply(buf, 4, size, ENC, "send_counter")
        return bytes(buf)

    def decode(self, block: bytes) -> bytes:
        size = int.from_bytes(block[0:2], "little")
        if size < 5 or size != len(block):
            raise ValueError(f"bad block size field {size} for {len(block)} bytes")
        buf = bytearray(block)
        self._apply(buf, 4, size, DEC, "recv_counter")
        if self.mode == 1:
            if crc16(bytes(buf)) != 0:
                raise ValueError("CRC mismatch")
            if buf[size - 3] != self.seq:
                raise ValueError(f"sequence mismatch: got {buf[size - 3]}, want {self.seq}")
            self.seq = (self.seq + 1) & 0xFF
            buf = buf[: size - 3]
            buf[0:2] = len(buf).to_bytes(2, "little")
        return bytes(buf)


def _self_test():

    assert crc16(b"123456789") == 0xFEE8

    for r in range(256):
        assert sorted(ENC[r]) == list(range(256))
        assert all(DEC[r][ENC[r][u]] == u for u in range(256))

    a, b = 0x1234, 0xBEEF
    client = Cryptor(send_counter=a, recv_counter=b, mode=0)
    server = Cryptor(send_counter=b, recv_counter=a, mode=1)

    for n in (5, 6, 37, 300):
        pkt = bytes([0, 0]) + bytes((i * 7) & 0xFF for i in range(n - 2))
        wire = client.encode(pkt)
        assert wire[2:4] == pkt[2:4], "bytes 2-3 must stay in clear"
        back = server.decode(wire)
        assert back[2:] == pkt[2:], "client->server round trip"
        wire2 = server.encode(pkt)
        assert client.decode(wire2)[2:] == pkt[2:], "server->client round trip"
    print("kcrypto self-test passed")


if __name__ == "__main__":
    _self_test()
