#!/usr/bin/env python3
"""Minimal classic Mac resource-fork parser: list resource types/ids/names."""
import struct
import sys


def parse(path):
    with open(path, "rb") as f:
        data = f.read()

    data_off, map_off, data_len, map_len = struct.unpack(">IIII", data[0:16])
    print(f"{path}: total={len(data)} data_off={data_off} map_off={map_off} "
          f"data_len={data_len} map_len={map_len}")

    map_data = data[map_off:map_off + map_len]
    # resource map header: 16 (copy of header) + 4 (next map handle) + 2 (file ref) +
    # 2 (attrs) + 2 (offset to type list, from start of map) + 2 (offset to name list)
    type_list_off = struct.unpack(">H", map_data[24:26])[0]
    name_list_off = struct.unpack(">H", map_data[26:28])[0]

    num_types = struct.unpack(">H", map_data[type_list_off:type_list_off + 2])[0] + 1
    print(f"  {num_types} resource type(s):")

    entries = []
    for i in range(num_types):
        base = type_list_off + 2 + i * 8
        rtype, rcount, ref_off = struct.unpack(">4sHH", map_data[base:base + 8])
        rcount += 1
        ref_list_off = type_list_off + ref_off
        ids = []
        for j in range(rcount):
            rbase = ref_list_off + j * 12
            res_id, name_off, attr_and_off = struct.unpack(
                ">hhI", map_data[rbase:rbase + 4] + b"\x00" + map_data[rbase + 4:rbase + 7])
            ids.append(res_id)
        entries.append((rtype.decode("mac_roman", "replace"), rcount, ids))
        print(f"    {rtype!r:8s} count={rcount:4d} ids={ids[:10]}{'...' if len(ids) > 10 else ''}")

    return entries


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parse(p)
        print()
