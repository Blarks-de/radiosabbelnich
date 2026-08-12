#!/usr/bin/env python3
"""
fix_silero_execstack.py — Build-time Patch für silero-vad-lite.

Die mitgelieferte silero_vad_lite.so ist mit einem PT_GNU_STACK-Segment
gebaut, das einen ausführbaren Stack verlangt (alter Toolchain-Default).
Der Kernel auf Dockfish verweigert das beim dlopen() mit
"cannot enable executable stack as shared object requires: Invalid
argument" — reproduzierbar in jedem Container auf diesem Host, unabhängig
von Compose-Config/User/Capabilities (getestet gegen ein cleanes
python:3.12-slim). Ohne diesen Patch fällt speech_detector.py dauerhaft
auf die deutlich unzuverlässigere Signal-Heuristik zurück.

Fix: das Executable-Bit (PF_X) im PT_GNU_STACK-Programmheader direkt in
der ELF-Datei löschen (das macht klassischerweise `execstack -c`, das
Tool ist aber im aktuellen Debian-Slim-Repo nicht mehr paketiert -> hier
in reinem Python nachgebaut, keine externe Abhängigkeit nötig).
"""

import os
import struct
import sys

PT_GNU_STACK = 0x6474E551
PF_X = 0x1


def clear_exec_stack(path: str) -> bool:
    with open(path, "rb") as f:
        data = f.read()

    if data[:4] != b"\x7fELF" or data[4] != 2:  # nur ELF64
        raise ValueError(f"{path} ist keine ELF64-Datei")
    endian = "<" if data[5] == 1 else ">"

    e_phoff, = struct.unpack_from(endian + "Q", data, 0x20)
    e_phentsize, = struct.unpack_from(endian + "H", data, 0x36)
    e_phnum, = struct.unpack_from(endian + "H", data, 0x38)

    patched = False
    with open(path, "r+b") as f:
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            p_type, p_flags = struct.unpack_from(endian + "II", data, off)
            if p_type == PT_GNU_STACK and (p_flags & PF_X):
                f.seek(off + 4)
                f.write(struct.pack(endian + "I", p_flags & ~PF_X))
                patched = True
    return patched


if __name__ == "__main__":
    import silero_vad_lite

    so_path = os.path.join(os.path.dirname(silero_vad_lite.__file__), "data", "silero_vad_lite.so")
    if clear_exec_stack(so_path):
        print(f"✓ Executable-Stack-Flag entfernt: {so_path}")
    else:
        print(f"– Kein PT_GNU_STACK/PF_X-Segment gefunden, nichts zu patchen: {so_path}")
        sys.exit(0)
