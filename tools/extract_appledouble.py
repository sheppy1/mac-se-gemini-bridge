#!/usr/bin/env python3
"""Parse an AppleDouble container (magic 0x00051607) and pull out the real
resource fork (entry id 2) and Finder Info (entry id 9)."""
import struct
import sys

ENTRY_NAMES = {
    1: "Data Fork", 2: "Resource Fork", 3: "Real Name", 4: "Comment",
    5: "Icon B&W", 6: "Icon Color", 8: "File Dates Info", 9: "Finder Info",
    10: "Macintosh File Info", 15: "AFP Short Name",
}


def parse_appledouble(path):
    with open(path, "rb") as f:
        data = f.read()

    magic, version = struct.unpack(">II", data[0:8])
    assert magic == 0x00051607, f"not AppleDouble (magic={magic:#x})"
    num_entries = struct.unpack(">H", data[24:26])[0]

    entries = {}
    for i in range(num_entries):
        base = 26 + i * 12
        eid, off, length = struct.unpack(">III", data[base:base + 12])
        entries[eid] = data[off:off + length]
        print(f"  entry id={eid:2d} ({ENTRY_NAMES.get(eid,'?'):16s}) "
              f"offset={off:8d} length={length:8d}")

    return entries


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p)
        parse_appledouble(p)
        print()
