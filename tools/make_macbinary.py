#!/usr/bin/env python3
"""Encode a MacBinary II (.bin) file from separate data-fork/resource-fork files,
matching exactly the parser in hfsutils' copyin.c (cpi_macb), so hcopy -m accepts it."""
import sys
import struct
import datetime

MAC_EPOCH = datetime.datetime(1904, 1, 1)


def mac_time(dt: datetime.datetime) -> int:
    return int((dt - MAC_EPOCH).total_seconds())


def crc_macb(data: bytes) -> int:
    """Bit-wise CRC-CCITT (poly 0x1021, init 0), equivalent to hfsutils' crc_macb table version."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def make_macbinary(name: str, file_type: str, creator: str, finder_flags: int,
                    data_fork: bytes, rsrc_fork: bytes, when: datetime.datetime) -> bytes:
    assert len(file_type) == 4 and len(creator) == 4
    name_b = name.encode("mac_roman")
    assert 1 <= len(name_b) <= 63

    buf = bytearray(128)
    buf[0] = 0                                    # old version, must be 0
    buf[1] = len(name_b)                          # filename length
    buf[2:2 + len(name_b)] = name_b                # filename (rest stays zero)
    buf[65:69] = file_type.encode("ascii")
    buf[69:73] = creator.encode("ascii")
    buf[73] = (finder_flags >> 8) & 0xFF           # Finder flags high byte
    buf[74] = 0                                    # zero fill, required
    # 75-80 window position / folder id: leave zero
    buf[81] = 0                                    # protected flag: unlocked
    buf[82] = 0                                    # zero fill, required
    struct.pack_into(">I", buf, 83, len(data_fork))
    struct.pack_into(">I", buf, 87, len(rsrc_fork))
    struct.pack_into(">I", buf, 91, mac_time(when))   # creation date
    struct.pack_into(">I", buf, 95, mac_time(when))   # modification date
    struct.pack_into(">H", buf, 99, 0)             # Get Info comment length
    buf[101] = finder_flags & 0xFF                 # Finder flags low byte
    buf[102] = 0
    struct.pack_into(">I", buf, 116, 0)            # unpacked length (n/a, not packed)
    struct.pack_into(">H", buf, 120, 0)            # secondary header length
    buf[122] = 129                                 # version made by
    buf[123] = 129                                 # min version to read
    crc = crc_macb(bytes(buf[0:124]))
    struct.pack_into(">H", buf, 124, crc)
    buf[126] = 0
    buf[127] = 0

    def pad128(b: bytes) -> bytes:
        pad = (-len(b)) % 128
        return b + b"\x00" * pad

    return bytes(buf) + pad128(data_fork) + pad128(rsrc_fork)


def read_or_empty(path):
    if path is None:
        return b""
    with open(path, "rb") as f:
        return f.read()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--creator", required=True)
    p.add_argument("--flags", type=lambda x: int(x, 0), default=0)
    p.add_argument("--data", default=None)
    p.add_argument("--rsrc", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--date", default="1997-01-21")
    args = p.parse_args()

    when = datetime.datetime.strptime(args.date, "%Y-%m-%d")
    data_fork = read_or_empty(args.data)
    rsrc_fork = read_or_empty(args.rsrc)

    out = make_macbinary(args.name, args.type, args.creator, args.flags,
                          data_fork, rsrc_fork, when)
    with open(args.out, "wb") as f:
        f.write(out)
    print(f"wrote {args.out}: {len(out)} bytes (data={len(data_fork)}, rsrc={len(rsrc_fork)})")
