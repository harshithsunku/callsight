"""Minimal ELF reader: function symbols, the build id, and what kind of
machine the binary is for.

Why this exists at all: hardware-counter selection names functions, but the
instrumentation hooks only ever receive an address, so something has to turn
one into the other. That job lands here rather than in the runtime because
the answer is the same for every run of a given binary — and because the
host doing the reading is an x86-64 workstation while the binary may well be
for a 32-bit big-endian device. So this handles **ELF32 and ELF64 in both
byte orders**, which is the whole point; a reader that only understood the
host's own format would be useless for exactly the targets callsight is for.

Symbols only, no DWARF. `analyze` still shells out to addr2line for
file:line, which needs the debug info; names and addresses live in the
symbol table and are a fraction of the work to parse.

    >>> f = Elf("bin/app.instr")
    >>> f.describe()
    'ELF32 big-endian PowerPC executable'
    >>> f.functions()["checksum"]
    (4201280, 96)

`nm` is the fallback for anything this cannot parse, so an unusual linker's
output degrades to "slower" rather than "unsupported".
"""

import os
import struct
import subprocess

ELF_MAGIC = b"\x7fELF"

# e_ident indices
EI_CLASS, EI_DATA = 4, 5
ELFCLASS32, ELFCLASS64 = 1, 2
ELFDATA2LSB, ELFDATA2MSB = 1, 2

# e_type
ET_EXEC, ET_DYN = 2, 3

# Section types
SHT_SYMTAB, SHT_STRTAB, SHT_NOTE, SHT_DYNSYM = 2, 3, 7, 11

# Symbol types (st_info & 0xf)
STT_FUNC = 2

SHN_UNDEF = 0
SHN_XINDEX = 0xFFFF

NT_GNU_BUILD_ID = 3

# Only the machines callsight is plausibly pointed at; anything else is
# reported by number, which is still more useful than "unknown".
MACHINES = {
    3: "x86", 8: "MIPS", 20: "PowerPC", 21: "PowerPC64", 22: "S/390",
    40: "ARM", 62: "x86-64", 183: "AArch64", 243: "RISC-V",
}


class ElfError(Exception):
    """The file is not an ELF this reader can make sense of."""


class Elf:
    """A parsed ELF file. Reads only the headers it needs, lazily."""

    def __init__(self, path):
        self.path = str(path)
        with open(self.path, "rb") as f:
            ident = f.read(16)
            if len(ident) < 16 or ident[:4] != ELF_MAGIC:
                raise ElfError(f"{self.path}: not an ELF file")
            cls, data = ident[EI_CLASS], ident[EI_DATA]
            if cls not in (ELFCLASS32, ELFCLASS64):
                raise ElfError(f"{self.path}: unknown ELF class {cls}")
            if data not in (ELFDATA2LSB, ELFDATA2MSB):
                raise ElfError(f"{self.path}: unknown ELF data encoding {data}")
            self.bits = 32 if cls == ELFCLASS32 else 64
            self.endian = "<" if data == ELFDATA2LSB else ">"
            self._read_header(f)
            self._read_sections(f)

    # --- header and sections ---

    def _read_header(self, f):
        e = self.endian
        # Everything after e_ident. The address-sized fields are the only
        # difference between the two classes.
        fmt = e + ("HHIIIIIHHHHHH" if self.bits == 32
                   else "HHIQQQIHHHHHH")
        f.seek(16)
        raw = f.read(struct.calcsize(fmt))
        (self.e_type, self.e_machine, _ver, self.entry, _phoff, self._shoff,
         _flags, _ehsize, _phentsize, _phnum, self._shentsize, self._shnum,
         self._shstrndx) = struct.unpack(fmt, raw)

    def _section_struct(self):
        e = self.endian
        # sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link,
        # sh_info, sh_addralign, sh_entsize
        return struct.Struct(e + ("IIIIIIIIII" if self.bits == 32
                                  else "IIQQQQIIQQ"))

    def _read_sections(self, f):
        sh = self._section_struct()
        if self._shoff == 0:
            self.sections = []
            return
        f.seek(self._shoff)
        first = sh.unpack(f.read(sh.size))
        # A binary with more than 65279 sections (C++ built with
        # -ffunction-sections gets there) stores the real counts in section
        # zero. Cheap to honour, and ignoring it silently truncates the
        # symbol table.
        count = self._shnum or first[5]
        strndx = (first[6] if self._shstrndx == SHN_XINDEX
                  else self._shstrndx)

        f.seek(self._shoff)
        raw = f.read(count * sh.size)
        if len(raw) < count * sh.size:
            raise ElfError(f"{self.path}: section headers are truncated")
        entries = [sh.unpack_from(raw, i * sh.size) for i in range(count)]

        names_blob = b""
        if 0 < strndx < count:
            _n, _t, _fl, _a, off, size = entries[strndx][:6]
            f.seek(off)
            names_blob = f.read(size)

        self.sections = []
        for (name_off, stype, _flags, addr, off, size, link, info,
             _align, entsize) in entries:
            self.sections.append({
                "name": _cstr(names_blob, name_off),
                "type": stype, "addr": addr, "offset": off, "size": size,
                "link": link, "info": info, "entsize": entsize,
            })

    def _section(self, name=None, stype=None):
        for s in self.sections:
            if (name is None or s["name"] == name) and \
                    (stype is None or s["type"] == stype):
                return s
        return None

    # --- what callers want ---

    @property
    def is_pie(self):
        """ET_DYN: addresses in the symbol table are offsets from wherever
        the loader put the image, which is why the runtime records its load
        bias and analyze subtracts it."""
        return self.e_type == ET_DYN

    def describe(self):
        machine = MACHINES.get(self.e_machine, f"machine {self.e_machine}")
        order = "little-endian" if self.endian == "<" else "big-endian"
        kind = "PIE/shared object" if self.is_pie else "executable"
        return f"ELF{self.bits} {order} {machine} {kind}"

    def functions(self):
        """{name: (address, size)} for every defined function symbol.

        Link-time addresses, so a PIE's are offsets — the same space the
        trace files record after the load bias is subtracted.

        .symtab is preferred and .dynsym is the fallback: a stripped binary
        keeps only the latter, which holds exported functions and none of the
        `static` ones. Local symbols are kept deliberately — static functions
        are exactly what people want to count and are unreachable any other
        way.
        """
        sec = self._section(stype=SHT_SYMTAB) or self._section(stype=SHT_DYNSYM)
        if sec is None or sec["entsize"] == 0:
            raise ElfError(f"{self.path}: no symbol table (stripped?)")
        strtab = self._strtab_for(sec)

        e = self.endian
        if self.bits == 32:
            # st_name, st_value, st_size, st_info, st_other, st_shndx
            sym = struct.Struct(e + "IIIBBH")
            unpack = lambda v: (v[0], v[1], v[2], v[3], v[5])  # noqa: E731
        else:
            # 64-bit reorders the fields; reading one layout for both is the
            # classic way to get plausible nonsense out of this.
            # st_name, st_info, st_other, st_shndx, st_value, st_size
            sym = struct.Struct(e + "IBBHQQ")
            unpack = lambda v: (v[0], v[4], v[5], v[1], v[3])  # noqa: E731

        with open(self.path, "rb") as f:
            f.seek(sec["offset"])
            raw = f.read(sec["size"])

        out = {}
        step = sec["entsize"]
        for off in range(0, len(raw) - step + 1, step):
            name_off, value, size, info, shndx = unpack(
                sym.unpack_from(raw, off))
            if (info & 0xF) != STT_FUNC or shndx == SHN_UNDEF:
                continue
            name = _cstr(strtab, name_off)
            if not name:
                continue
            # A name defined more than once (static functions in different
            # files share names) keeps the first; ambiguity is the caller's
            # to resolve, and it is reported there rather than guessed here.
            out.setdefault(name, (value, size))
        return out

    def _strtab_for(self, sec):
        link = sec["link"]
        if not (0 < link < len(self.sections)):
            raise ElfError(f"{self.path}: symbol table has no string table")
        s = self.sections[link]
        with open(self.path, "rb") as f:
            f.seek(s["offset"])
            return f.read(s["size"])

    def build_id(self):
        """The GNU build id as hex, or None.

        Cheap identity for "is this the binary that produced that trace?" —
        which matters because a counter map is a list of addresses, and
        addresses from a previous build point at whatever happens to live
        there now.
        """
        sec = self._section(name=".note.gnu.build-id")
        if sec is None or sec["type"] != SHT_NOTE:
            return None
        with open(self.path, "rb") as f:
            f.seek(sec["offset"])
            blob = f.read(sec["size"])
        head = struct.Struct(self.endian + "III")
        pos = 0
        while pos + head.size <= len(blob):
            namesz, descsz, ntype = head.unpack_from(blob, pos)
            pos += head.size
            name = blob[pos:pos + namesz]
            pos += _align4(namesz)
            desc = blob[pos:pos + descsz]
            pos += _align4(descsz)
            if ntype == NT_GNU_BUILD_ID and name.rstrip(b"\0") == b"GNU":
                return desc.hex()
        return None


def _cstr(blob, off):
    if off >= len(blob):
        return ""
    end = blob.find(b"\0", off)
    raw = blob[off:] if end < 0 else blob[off:end]
    return raw.decode("utf-8", "replace")


def _align4(n):
    return (n + 3) & ~3


# --- the nm fallback -------------------------------------------------------

def _functions_via_nm(path, nm="nm"):
    """{name: (address, 0)} from nm, for ELFs this reader cannot parse.

    Sizes are not reported, which is fine: nothing here needs them, and a
    zero is honest about not knowing.
    """
    try:
        proc = subprocess.run([nm, "--defined-only", str(path)],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise ElfError(f"{path}: cannot parse as ELF and {nm} failed: {e}")
    if proc.returncode != 0:
        raise ElfError(f"{path}: cannot parse as ELF and {nm} failed: "
                       f"{(proc.stderr or '').strip()}")
    out = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        # "<addr> <type> <name>"; T/t are text (function) symbols.
        if len(parts) == 3 and parts[1] in ("T", "t", "W", "w"):
            try:
                out.setdefault(parts[2], (int(parts[0], 16), 0))
            except ValueError:
                continue
    return out


def functions(path, nm=None):
    """{name: (address, size)} for a binary, however we can get it."""
    try:
        return Elf(path).functions()
    except (ElfError, OSError, struct.error):
        return _functions_via_nm(path, nm or os.environ.get("CALLSIGHT_NM")
                                 or "nm")


def describe(path):
    """One line about the target, or None if it is not a readable ELF."""
    try:
        return Elf(path).describe()
    except (ElfError, OSError, struct.error):
        return None
