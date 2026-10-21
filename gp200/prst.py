"""Read and write Valeton GP-200 .prst preset files.

Layout (little-endian unless noted), firmware 1.8 user preset, 1224 bytes:

    0x000  "TSRP" magic                     0x010  "2-PG" device id
    0x00B  u8 format version (6)            0x014  firmware 00 01 01 00
    0x01C  u32 timestamp / id               0x020  u32 offset of MRAP (0x28)
    0x024  u32 payload size (len - 0x34)    0x028  "MRAP", 0x02C payload size
    0x030  u16 0x0002, 0x032 u16 0x0058     0x034  u8 target patch slot 0..255
    0x036  u16 tempo (BPM)                  0x038  u16 patch volume 0..100
    0x03A  s16 pan (-50..50)                0x03C  u16 style tag
    0x03E  u16 FX loop send level           0x040  u16 FX loop return level
    0x042  u16 FX loop mode (0 parallel, 1 series)
    0x044  name   (16 bytes ASCII, NUL padded)
    0x054  author (16 bytes)                0x064  note (40 bytes)
    0x08C  08 00 10 00                      0x090  u8 patch slot (mirror)
    0x092  u8 FX loop send position         0x093  u8 FX loop return position
    0x094  11 x u8 signal-chain order (block number playing at each position)
    0x0A0  11 effect blocks x 72 bytes, one per module (PRE..VOL):
             14 00 44 00 | u8 block | u8 enabled | 0F 00 | u32 effect code |
             15 x f32 parameters (index = the parameter "ID" in algorithm.xml)
    0x3B0  controls tail: 8 zero bytes, then TLV records [u16 type][u16 size]:
             9 x EXP   (type 0x0C, 12 bytes): page<<4|item, block (FF=none),
                                              u16 param, f32 max, f32 min
             3 x 0x10  (4 bytes): quick-access knob records (kept verbatim)
             N x CTRL  (type 0x0F, 8 bytes): index, state, 2 raw, u16 mask, 2 raw
           footer: u16 own offset, 4 zero bytes
    last 2 bytes: big-endian u16 = sum(all preceding bytes) & 0xFFFF

Factory presets from older firmware are 1176 bytes: same layout, with only
4 CTRL records. Both sizes are read; files are always written as 1224 bytes.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional

MODULES = ["PRE", "WAH", "DST", "AMP", "NR", "CAB", "EQ", "MOD", "DLY", "RVB", "VOL"]
BLOCK_COUNT = 11
PARAM_COUNT = 15
FX_LOOP_BIT = 11  # CTRL mask bit for the FX loop

USER_SIZE = 1224
FACTORY_SIZE = 1176
SUPPORTED_SIZES = (USER_SIZE, FACTORY_SIZE)

OFF_PATCH_SLOT = 0x34
OFF_TEMPO = 0x36
OFF_VOLUME = 0x38
OFF_PAN = 0x3A
OFF_STYLE = 0x3C
OFF_SEND_LEVEL = 0x3E
OFF_RETURN_LEVEL = 0x40
OFF_FX_MODE = 0x42
OFF_NAME, NAME_LEN = 0x44, 16
OFF_AUTHOR, AUTHOR_LEN = 0x54, 16
OFF_NOTE, NOTE_LEN = 0x64, 40
OFF_ROUTING = 0x8C
OFF_SLOT_MIRROR = 0x90
OFF_FX_SEND = 0x92
OFF_FX_RETURN = 0x93
OFF_CHAIN = 0x94
OFF_BLOCKS = 0xA0
BLOCK_SIZE = 0x48
OFF_TAIL = 0x3B0

REC_EXP, REC_KNOB, REC_CTRL = 0x000C, 0x0010, 0x000F
REC_SIZES = {REC_EXP: 12, REC_KNOB: 4, REC_CTRL: 8}

# Values a clean firmware 1.8 export carries in the three 0x10 records.
DEFAULT_KNOB_RECORDS = [bytes([0, 0xFF, 0, 0]), bytes([1, 0xFF, 0, 0]), bytes([2, 0x0B, 0, 0])]


class PrstError(ValueError):
    """The bytes are not a GP-200 preset this module understands."""


@dataclass
class Block:
    """One of the 11 effect blocks. `block` is the module number (0=PRE .. 10=VOL)."""

    block: int
    code: int = 0
    enabled: bool = False
    params: List[float] = field(default_factory=lambda: [0.0] * PARAM_COUNT)

    @property
    def module(self) -> str:
        return MODULES[self.block]


@dataclass
class ExpAssign:
    """EXP pedal target. page: 0 = EXP1 mode A, 1 = EXP1 mode B, 2 = EXP2; item 0..2."""

    page: int
    item: int
    block: Optional[int] = None  # None = unassigned; 0..10 module; >10 special target
    param: int = 0
    min: float = 0.0
    max: float = 100.0


@dataclass
class CtrlAssign:
    """CTRL footswitch: bit n of mask toggles block n; bit 11 toggles the FX loop."""

    index: int
    mask: int = 0
    state: int = 0  # saved toggle position byte, kept verbatim
    raw: bytes = b"\0\0\0\0"  # undocumented bytes, kept for byte-exact round trips


def default_exp() -> List[ExpAssign]:
    """What the pedal stores in a fresh patch: EXP1-A -> VOL volume, EXP1-B -> WAH position."""
    out = []
    for page in range(3):
        for item in range(3):
            a = ExpAssign(page, item)
            if (page, item) == (0, 0):
                a.block, a.param = 10, 0
            elif (page, item) == (1, 0):
                a.block, a.param = 1, 3
            out.append(a)
    return out


def default_ctrl() -> List[CtrlAssign]:
    return [CtrlAssign(i) for i in range(8)]


@dataclass
class Preset:
    name: str = "New Preset"
    author: str = ""
    note: str = ""
    patch_slot: Optional[int] = None  # 0..255 = (bank-1)*4 + A..D
    slot_mirror: Optional[int] = None  # byte 0x90; usually == patch_slot, sometimes slot+1
    tempo: int = 120
    volume: int = 50
    pan: int = 0
    style: int = 0
    send_level: int = 0
    return_level: int = 0
    fx_mode: int = 0  # 0 parallel, 1 series
    fx_send: int = 4
    fx_return: int = 4
    chain: List[int] = field(default_factory=lambda: list(range(BLOCK_COUNT)))
    blocks: List[Block] = field(default_factory=lambda: [Block(i) for i in range(BLOCK_COUNT)])
    exp: List[ExpAssign] = field(default_factory=default_exp)
    ctrl: List[CtrlAssign] = field(default_factory=default_ctrl)
    knob_records: List[bytes] = field(default_factory=lambda: list(DEFAULT_KNOB_RECORDS))
    timestamp: Optional[int] = None
    source_size: int = USER_SIZE


# ── slot names ────────────────────────────────────────────────────────────────

def slot_name(index: int) -> str:
    """0 -> '01-A', 255 -> '64-D'."""
    return f"{index // 4 + 1:02d}-{'ABCD'[index % 4]}"


def parse_slot_name(text: str) -> int:
    t = str(text).strip().upper().replace(" ", "")
    if "-" in t:
        bank, letter = t.split("-", 1)
    else:
        bank, letter = t[:-1], t[-1:]
    if not bank.isdigit() or letter not in ("A", "B", "C", "D"):
        raise ValueError(f"patch slot must look like '01-A'..'64-D', got {text!r}")
    index = (int(bank) - 1) * 4 + "ABCD".index(letter)
    if not 0 <= index <= 255:
        raise ValueError(f"patch slot out of range (01-A..64-D): {text!r}")
    return index


# ── helpers ───────────────────────────────────────────────────────────────────

def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFF


def _ascii(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def _put_ascii(buf: bytearray, off: int, text: str, length: int) -> None:
    raw = text.encode("ascii", errors="replace")[:length]
    buf[off:off + length] = raw + b"\0" * (length - len(raw))


def _walk_tail(data: bytes, start: int):
    """Yield (type, payload_offset) for each TLV record; stops at the footer."""
    off = start
    while off + 1 < len(data) and data[off] == 0 and data[off + 1] == 0 and off - start < 32:
        off += 2
    while off + 4 <= len(data):
        rtype, size = struct.unpack_from("<HH", data, off)
        if rtype not in REC_SIZES:
            return
        if size != REC_SIZES[rtype] or off + 4 + size > len(data):
            raise PrstError(f"malformed control record at {off:#x}")
        yield rtype, off + 4
        off += 4 + size


# ── reading ───────────────────────────────────────────────────────────────────

def read_prst(data: bytes) -> Preset:
    if len(data) not in SUPPORTED_SIZES:
        raise PrstError(f"expected {USER_SIZE} or {FACTORY_SIZE} bytes, got {len(data)}")
    if data[0:4] != b"TSRP" or data[0x10:0x14] != b"2-PG":
        raise PrstError("not a GP-200 preset (missing TSRP / 2-PG header)")
    stored = struct.unpack_from(">H", data, len(data) - 2)[0]
    if checksum(data[:-2]) != stored:
        raise PrstError("checksum mismatch: the file is corrupt or was edited by hand")

    u16 = lambda off: struct.unpack_from("<H", data, off)[0]  # noqa: E731
    p = Preset(
        name=_ascii(data[OFF_NAME:OFF_NAME + NAME_LEN]),
        author=_ascii(data[OFF_AUTHOR:OFF_AUTHOR + AUTHOR_LEN]),
        note=_ascii(data[OFF_NOTE:OFF_NOTE + NOTE_LEN]),
        patch_slot=data[OFF_PATCH_SLOT],
        slot_mirror=data[OFF_SLOT_MIRROR],
        tempo=u16(OFF_TEMPO),
        volume=data[OFF_VOLUME],
        pan=struct.unpack_from("<h", data, OFF_PAN)[0],
        style=u16(OFF_STYLE),
        send_level=u16(OFF_SEND_LEVEL),
        return_level=u16(OFF_RETURN_LEVEL),
        fx_mode=1 if u16(OFF_FX_MODE) == 1 else 0,
        fx_send=data[OFF_FX_SEND],
        fx_return=data[OFF_FX_RETURN],
        timestamp=struct.unpack_from("<I", data, 0x1C)[0],
        source_size=len(data),
    )

    # Signal chain: keep valid, unique entries in order, append anything missing.
    chain, seen = [], set()
    for b in data[OFF_CHAIN:OFF_CHAIN + BLOCK_COUNT]:
        if b < BLOCK_COUNT and b not in seen:
            chain.append(b)
            seen.add(b)
    chain += [b for b in range(BLOCK_COUNT) if b not in seen]
    p.chain = chain

    blocks = [Block(i) for i in range(BLOCK_COUNT)]
    for i in range(BLOCK_COUNT):
        base = OFF_BLOCKS + i * BLOCK_SIZE
        if data[base] != 0x14 or data[base + 2] != 0x44:
            raise PrstError(f"effect block {i} has a bad header")
        n = data[base + 4]
        if n >= BLOCK_COUNT:
            raise PrstError(f"effect block {i} claims module {n}")
        params = list(struct.unpack_from("<15f", data, base + 12))
        params = [v if v == v and abs(v) != float("inf") else 0.0 for v in params]  # NaN/inf -> 0
        blocks[n] = Block(n, struct.unpack_from("<I", data, base + 8)[0], data[base + 5] == 1, params)
    p.blocks = blocks

    exp, ctrl, knobs = [], [], []
    for rtype, off in _walk_tail(data, OFF_TAIL):
        if rtype == REC_EXP:
            sid = data[off]
            block = data[off + 1]
            mx, mn = struct.unpack_from("<ff", data, off + 4)
            exp.append(ExpAssign(sid >> 4, sid & 0x0F, None if block == 0xFF else block,
                                 struct.unpack_from("<H", data, off + 2)[0], mn, mx))
        elif rtype == REC_KNOB:
            knobs.append(bytes(data[off:off + 4]))
        else:
            ctrl.append(CtrlAssign(data[off], struct.unpack_from("<H", data, off + 4)[0] & 0x0FFF,
                                   data[off + 1],
                                   bytes(data[off + 2:off + 4] + data[off + 6:off + 8])))
    if exp:
        p.exp = exp
    if knobs:
        p.knob_records = knobs
    # Older files carry fewer CTRL records; fill the rest with "nothing assigned".
    have = {c.index for c in ctrl}
    p.ctrl = sorted(ctrl + [CtrlAssign(i) for i in range(8) if i not in have], key=lambda c: c.index)[:8]
    return p


def read_prst_file(path) -> Preset:
    with open(path, "rb") as f:
        return read_prst(f.read())


# ── writing ───────────────────────────────────────────────────────────────────

def _build_tail(p: Preset) -> bytes:
    out = bytearray(8)
    by_slot = {(a.page, a.item): a for a in p.exp}
    for page in range(3):
        for item in range(3):
            a = by_slot.get((page, item)) or ExpAssign(page, item)
            block = 0xFF if a.block is None else a.block
            out += struct.pack("<HHBBHff", REC_EXP, 12, (page << 4) | item, block, a.param, a.max, a.min)
    knobs = (list(p.knob_records) + DEFAULT_KNOB_RECORDS[len(p.knob_records):])[:3]
    for rec in knobs:
        out += struct.pack("<HH", REC_KNOB, 4) + bytes(rec)
    by_index = {c.index: c for c in p.ctrl}
    for i in range(8):
        c = by_index.get(i) or CtrlAssign(i)
        raw = (bytes(c.raw) + b"\0\0\0\0")[:4]
        out += struct.pack("<HHBB", REC_CTRL, 8, i, c.state & 0xFF) + raw[:2] + struct.pack("<H", c.mask & 0x0FFF) + raw[2:]
    footer_at = OFF_TAIL + len(out)
    out += struct.pack("<HI", footer_at, 0)
    return bytes(out)


def write_prst(p: Preset) -> bytes:
    """Serialize a Preset as a firmware-1.8 user preset (1224 bytes)."""
    if sorted(p.chain) != list(range(BLOCK_COUNT)):
        raise PrstError("chain must contain each module exactly once")
    buf = bytearray(USER_SIZE)
    buf[0:4] = b"TSRP"
    buf[0x0B] = 0x06
    buf[0x10:0x14] = b"2-PG"
    buf[0x14:0x18] = bytes([0x00, 0x01, 0x01, 0x00])
    ts = int(time.time()) & 0xFFFFFFFF if p.timestamp is None else p.timestamp
    struct.pack_into("<I", buf, 0x1C, ts)
    struct.pack_into("<II", buf, 0x20, 0x28, USER_SIZE - 0x34)
    buf[0x28:0x2C] = b"MRAP"
    struct.pack_into("<I", buf, 0x2C, USER_SIZE - 0x34)
    struct.pack_into("<HH", buf, 0x30, 0x0002, 0x0058)

    slot = 0 if p.patch_slot is None else p.patch_slot
    buf[OFF_PATCH_SLOT] = slot & 0xFF
    struct.pack_into("<H", buf, OFF_TEMPO, p.tempo)
    struct.pack_into("<H", buf, OFF_VOLUME, p.volume)
    struct.pack_into("<h", buf, OFF_PAN, p.pan)
    struct.pack_into("<HHHH", buf, OFF_STYLE, p.style, p.send_level, p.return_level, p.fx_mode)
    _put_ascii(buf, OFF_NAME, p.name, NAME_LEN)
    _put_ascii(buf, OFF_AUTHOR, p.author, AUTHOR_LEN)
    _put_ascii(buf, OFF_NOTE, p.note, NOTE_LEN)

    buf[OFF_ROUTING:OFF_ROUTING + 4] = bytes([0x08, 0x00, 0x10, 0x00])
    buf[OFF_SLOT_MIRROR] = (slot if p.slot_mirror is None else p.slot_mirror) & 0xFF
    buf[OFF_FX_SEND] = p.fx_send
    buf[OFF_FX_RETURN] = p.fx_return
    buf[OFF_CHAIN:OFF_CHAIN + BLOCK_COUNT] = bytes(p.chain)

    by_block = {b.block: b for b in p.blocks}
    for i in range(BLOCK_COUNT):
        b = by_block.get(i) or Block(i)
        base = OFF_BLOCKS + i * BLOCK_SIZE
        params = (list(b.params) + [0.0] * PARAM_COUNT)[:PARAM_COUNT]
        struct.pack_into("<BBBBBBBBI15f", buf, base, 0x14, 0, 0x44, 0, i, 1 if b.enabled else 0,
                         0x0F, 0, b.code, *params)

    tail = _build_tail(p)
    buf[OFF_TAIL:OFF_TAIL + len(tail)] = tail
    struct.pack_into(">H", buf, USER_SIZE - 2, checksum(buf[:-2]))
    return bytes(buf)
