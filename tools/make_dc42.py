#!/usr/bin/env python3
"""Wrap a raw HFS volume image in an Apple Disk Copy 4.2 header, matching the
format documented at ciderpress2.com/formatdoc/DiskCopy-notes.html and
discferret.com/wiki/Apple_DiskCopy_4.2."""
import sys
import struct


def dc42_checksum(data: bytes) -> int:
    assert len(data) % 2 == 0
    acc = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) | data[i + 1]
        acc = (acc + word) & 0xFFFFFFFF
        acc = ((acc >> 1) | ((acc & 1) << 31)) & 0xFFFFFFFF
    return acc


def make_dc42(disk_name: str, raw_image: bytes, disk_format: int, format_byte: int) -> bytes:
    name_b = disk_name.encode("mac_roman")
    assert len(name_b) <= 63
    assert len(raw_image) % 512 == 0

    header = bytearray(84)
    header[0] = len(name_b)
    header[1:1 + len(name_b)] = name_b
    struct.pack_into(">I", header, 0x40, len(raw_image))   # dataSize
    struct.pack_into(">I", header, 0x44, 0)                 # tagSize
    struct.pack_into(">I", header, 0x48, dc42_checksum(raw_image))  # dataChecksum
    struct.pack_into(">I", header, 0x4c, 0)                 # tagChecksum
    header[0x50] = disk_format
    header[0x51] = format_byte
    struct.pack_into(">H", header, 0x52, 0x0100)             # private/signature

    return bytes(header) + raw_image


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    with open(args.infile, "rb") as f:
        raw = f.read()

    size_kb = len(raw) // 1024
    fmt_table = {400: (0, 0x12), 800: (1, 0x22), 720: (2, 0x24), 1440: (3, 0x22)}
    if size_kb not in fmt_table:
        sys.exit(f"unexpected image size: {len(raw)} bytes ({size_kb}KB) not a standard floppy size")
    disk_format, format_byte = fmt_table[size_kb]

    out = make_dc42(args.name, raw, disk_format, format_byte)
    with open(args.out, "wb") as f:
        f.write(out)
    print(f"wrote {args.out}: {len(out)} bytes (header 84 + image {len(raw)}), "
          f"format={disk_format} formatByte=0x{format_byte:02x}")
