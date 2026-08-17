"""Unit tests for callsight.elf — the symbol reader that turns the function
names in trace.config into the addresses the hooks actually see.

The host doing the reading is an x86-64 workstation; the binary may be for a
32-bit big-endian device. So the interesting cases are the foreign ones, and
they are built here in Python rather than with a cross toolchain: a handful
of synthetic ELFs covering ELF32/ELF64 x little/big endian, so the test runs
anywhere and pins the exact field layouts — including the one that bites,
which is that the symbol record's fields are in a *different order* in the
64-bit layout.

The reader is also checked against the real binary this repo builds, with nm
as the independent oracle.
"""

import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import elf

REPO = Path(__file__).resolve().parent.parent


def build_elf(bits=64, endian="<", machine=62, funcs=(), build_id=None,
              etype=elf.ET_EXEC, magic=elf.ELF_MAGIC):
    """A minimal but genuine ELF: header, string tables, symtab, build-id note.

    funcs: [(name, addr, size)] emitted as defined STT_FUNC symbols.
    """
    e = endian
    is32 = bits == 32

    shstr = b"\0.shstrtab\0.strtab\0.symtab\0.note.gnu.build-id\0"
    off_shstrtab, off_strtab = 1, 11
    off_symtab, off_note = 19, 27

    strtab = b"\0"
    sym_offsets = []
    for name, _a, _s in funcs:
        sym_offsets.append(len(strtab))
        strtab += name.encode() + b"\0"

    if is32:
        sym = struct.Struct(e + "IIIBBH")   # name, value, size, info, other, shndx
        pack_sym = lambda n, v, z: sym.pack(n, v, z, elf.STT_FUNC, 0, 1)  # noqa: E731
    else:
        sym = struct.Struct(e + "IBBHQQ")   # name, info, other, shndx, value, size
        pack_sym = lambda n, v, z: sym.pack(n, elf.STT_FUNC, 0, 1, v, z)  # noqa: E731

    # Index 0 is always the undefined symbol.
    symtab = pack_sym(0, 0, 0)
    for (name, addr, size), noff in zip(funcs, sym_offsets):
        symtab += pack_sym(noff, addr, size)

    note = b""
    if build_id is not None:
        desc = bytes.fromhex(build_id)
        note = (struct.pack(e + "III", 4, len(desc), elf.NT_GNU_BUILD_ID)
                + b"GNU\0" + desc + b"\0" * (-len(desc) % 4))

    ehsize = 52 if is32 else 64
    shentsize = 40 if is32 else 64

    body = b""
    def place(blob):
        nonlocal body
        off = ehsize + len(body)
        body += blob
        return off

    o_shstr = place(shstr)
    o_str = place(strtab)
    o_sym = place(symtab)
    o_note = place(note) if note else 0

    shoff = ehsize + len(body)
    shnum, shstrndx = 5, 1

    if is32:
        head = struct.pack(e + "HHIIIIIHHHHHH", etype, machine, 1, 0, 0,
                           shoff, 0, ehsize, 0, 0, shentsize, shnum, shstrndx)
        sh = struct.Struct(e + "IIIIIIIIII")
        def shdr(name, stype, off, size, link=0, entsize=0):
            return sh.pack(name, stype, 0, 0, off, size, link, 0, 1, entsize)
    else:
        head = struct.pack(e + "HHIQQQIHHHHHH", etype, machine, 1, 0, 0,
                           shoff, 0, ehsize, 0, 0, shentsize, shnum, shstrndx)
        sh = struct.Struct(e + "IIQQQQIIQQ")
        def shdr(name, stype, off, size, link=0, entsize=0):
            return sh.pack(name, stype, 0, 0, off, size, link, 0, 1, entsize)

    ident = magic + bytes([elf.ELFCLASS32 if is32 else elf.ELFCLASS64,
                           elf.ELFDATA2LSB if e == "<" else elf.ELFDATA2MSB,
                           1, 0, 0]) + b"\0" * 7

    headers = (shdr(0, 0, 0, 0)
               + shdr(off_shstrtab, elf.SHT_STRTAB, o_shstr, len(shstr))
               + shdr(off_strtab, elf.SHT_STRTAB, o_str, len(strtab))
               + shdr(off_symtab, elf.SHT_SYMTAB, o_sym, len(symtab),
                      link=2, entsize=sym.size)
               + shdr(off_note, elf.SHT_NOTE, o_note, len(note)))
    return ident + head + body + headers


FUNCS = [("checksum", 0x401B40, 96), ("transform", 0x401C90, 240),
         ("static_helper", 0x401D80, 32)]

# Every combination that matters, named the way a user would recognize it.
QUADRANTS = [
    ("x86-64", 64, "<", 62),
    ("ARMv7", 32, "<", 40),
    ("PowerPC 32-bit", 32, ">", 20),
    ("s390x", 64, ">", 22),
]


class TestQuadrants(unittest.TestCase):
    """Same three functions, four architectures: the reader must agree."""

    def _write(self, blob):
        tmp = tempfile.NamedTemporaryFile(suffix=".elf", delete=False)
        tmp.write(blob)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_symbols_read_identically_on_every_target(self):
        for label, bits, endian, machine in QUADRANTS:
            with self.subTest(target=label):
                path = self._write(build_elf(bits, endian, machine,
                                             funcs=FUNCS))
                got = elf.Elf(path).functions()
                self.assertEqual(
                    got, {n: (a, s) for n, a, s in FUNCS},
                    f"{label}: symbol table read wrong")

    def test_describe_names_the_target(self):
        for label, bits, endian, machine in QUADRANTS:
            with self.subTest(target=label):
                path = self._write(build_elf(bits, endian, machine))
                desc = elf.Elf(path).describe()
                self.assertIn(f"ELF{bits}", desc)
                self.assertIn("little" if endian == "<" else "big", desc)

    def test_build_id_round_trips_in_both_orders(self):
        want = "3f2ae1c9d4b5768290abcdef0123456789abcdef"
        for label, bits, endian, machine in QUADRANTS:
            with self.subTest(target=label):
                path = self._write(build_elf(bits, endian, machine,
                                             build_id=want))
                self.assertEqual(elf.Elf(path).build_id(), want)

    def test_no_build_id_is_not_an_error(self):
        path = self._write(build_elf(64, "<", 62, funcs=FUNCS))
        self.assertIsNone(elf.Elf(path).build_id())

    def test_a_64_bit_file_read_as_32_bit_would_not_pass(self):
        """Guards the reader's sharpest edge: the symbol record's fields are
        ordered differently in the two classes, so reading one layout for
        both yields plausible nonsense rather than an error."""
        blob = build_elf(64, "<", 62, funcs=FUNCS)
        wrong = bytearray(blob)
        wrong[elf.EI_CLASS] = elf.ELFCLASS32
        path = self._write(bytes(wrong))
        try:
            got = elf.Elf(path).functions()
        except (elf.ElfError, struct.error):
            return          # rejected outright is a fine outcome
        self.assertNotEqual(got, {n: (a, s) for n, a, s in FUNCS},
                            "a mis-declared class must not read correctly")


class TestPieAndErrors(unittest.TestCase):
    def _write(self, blob):
        tmp = tempfile.NamedTemporaryFile(suffix=".elf", delete=False)
        tmp.write(blob)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_pie_is_reported(self):
        exe = self._write(build_elf(etype=elf.ET_EXEC))
        pie = self._write(build_elf(etype=elf.ET_DYN))
        self.assertFalse(elf.Elf(exe).is_pie)
        self.assertTrue(elf.Elf(pie).is_pie)

    def test_not_an_elf_is_rejected(self):
        path = self._write(b"#!/bin/sh\necho hi\n")
        with self.assertRaises(elf.ElfError):
            elf.Elf(path)
        self.assertIsNone(elf.describe(path))

    def test_unknown_class_is_rejected(self):
        blob = bytearray(build_elf())
        blob[elf.EI_CLASS] = 7
        with self.assertRaises(elf.ElfError):
            elf.Elf(self._write(bytes(blob)))

    def test_stripped_of_symbols_says_so(self):
        """No symtab and no dynsym: the message has to name the likely cause,
        because 'no symbols' is the one failure a user can actually fix."""
        blob = build_elf(funcs=FUNCS)
        # Turn the symtab into a plain progbits section.
        path = self._write(blob.replace(
            struct.pack("<II", 19, elf.SHT_SYMTAB),
            struct.pack("<II", 19, 1)))
        with self.assertRaises(elf.ElfError) as cm:
            elf.Elf(path).functions()
        self.assertIn("stripped", str(cm.exception))


@unittest.skipUnless(shutil.which("nm"), "needs binutils nm")
class TestAgainstNm(unittest.TestCase):
    """The real binary this repo builds, checked against an outside opinion.

    Synthetic files prove the layouts; this proves the reader against what a
    real toolchain actually emits.
    """

    BINARY = REPO / "tests" / "matrixlab" / "bin" / "matrixlab.instr"

    def setUp(self):
        if not self.BINARY.exists():
            self.skipTest("run `make instrument` in tests/matrixlab first")

    def test_every_address_matches_nm(self):
        mine = elf.Elf(self.BINARY).functions()
        self.assertGreater(len(mine), 50)
        out = subprocess.run(["nm", "--defined-only", str(self.BINARY)],
                             capture_output=True, text=True).stdout
        theirs = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] in ("T", "t", "W", "w"):
                theirs.setdefault(parts[2], int(parts[0], 16))
        mismatched = {n: (a, theirs[n]) for n, (a, _s) in mine.items()
                      if n in theirs and theirs[n] != a}
        self.assertEqual(mismatched, {})
        self.assertEqual([n for n in mine if n not in theirs], [])

    def test_static_functions_are_included(self):
        """The whole reason for reading .symtab rather than .dynsym: static
        functions are exactly the ones people want to count, and they are
        unreachable any other way."""
        mine = elf.Elf(self.BINARY).functions()
        self.assertIn("qs_swap", mine)   # static in sort/quicksort.c

    def test_nm_fallback_finds_the_same_functions(self):
        via_elf = elf.Elf(self.BINARY).functions()
        via_nm = elf._functions_via_nm(self.BINARY)
        for name, (addr, _size) in via_elf.items():
            self.assertEqual(via_nm.get(name, (None,))[0], addr, name)


if __name__ == "__main__":
    unittest.main()
