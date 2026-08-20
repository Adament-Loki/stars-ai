"""StarsAPI-compatible ship DesignBlock codec.

This module is a literal Python port of the byte layout implemented by
``org.starsautohost.starsapi.block.DesignBlock.encode()/decode()`` in StarsAPI.
It deliberately does *not* encode the two-byte Type-27 DesignChange wrapper;
StarsAPI itself leaves ``DesignChangeBlock.encode()`` unimplemented.  Keeping
those concerns separate lets the native writer distinguish:

* KNOWN: DesignBlock body layout (StarsAPI encoder/decoder).
* EMPIRICAL: the two leading bytes and sequencing used by Type-27 changes.

The Type-27 decoder in StarsAPI documents one important transformation: the
embedded DesignBlock may carry byte-1 bit 0 clear.  StarsAPI restores the bit
before calling DesignBlock.decode().  ``encode_type27_embedded_design()``
mirrors that behavior by starting with a normal DesignBlock encoding and then
clearing only that bit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from stars_ai.design_legality import ComponentRef


_ONE = " aehilnorst"
_B = "ABCDEFGHIJKLMNOP"
_C = "QRSTUVWXYZ012345"
_D = "6789bcdfgjkmpquv"
_E = "wxyz+-,!.?:;'*%$"


def encode_stars_string(text: str) -> bytes:
    """Encode the common Stars! nibble string representation.

    StarsAPI's DesignBlock encoder consumes pre-encoded ``nameBytes``.  The
    project already validated this nibble mapping against client-generated
    names, so the StarsAPI body port receives those bytes exactly as the Java
    encoder does.
    """
    nibbles: list[str] = []
    for ch in str(text):
        code = ord(ch)
        if code > 255:
            ch = "?"
            code = ord(ch)
        idx = _ONE.find(ch)
        if idx >= 0:
            nibbles.append(f"{idx:X}")
            continue
        for prefix, table in (("B", _B), ("C", _C), ("D", _D), ("E", _E)):
            idx = table.find(ch)
            if idx >= 0:
                nibbles.extend((prefix, f"{idx:X}"))
                break
        else:
            # Stars! escape stores swapped ASCII nibbles.
            nibbles.extend(("F", f"{code & 0x0F:X}", f"{(code >> 4) & 0x0F:X}"))
    if len(nibbles) % 2:
        nibbles.append("F")
    encoded = bytes.fromhex("".join(nibbles)) if nibbles else b""
    if len(encoded) > 255:
        raise ValueError("Stars! encoded design name exceeds 255 bytes")
    return bytes([len(encoded)]) + encoded


def decode_stars_string(data: bytes) -> tuple[str, int]:
    if not data:
        raise ValueError("Missing Stars! string length byte")
    size = int(data[0])
    if len(data) < 1 + size:
        raise ValueError("Stars! string is truncated")
    raw = data[1 : 1 + size]
    tables = [_ONE, _B, _C, _D, _E]
    nibs: list[int] = []
    for b in raw:
        nibs.extend((b >> 4, b & 0x0F))
    out: list[str] = []
    i = 0
    while i < len(nibs):
        x = nibs[i]
        i += 1
        if x <= 10:
            out.append(tables[0][x])
        elif x in (11, 12, 13, 14):
            if i >= len(nibs):
                break
            y = nibs[i]
            i += 1
            out.append(tables[x - 10][y])
        elif x == 15:
            if i + 1 >= len(nibs):
                break
            lo_nibble = nibs[i]
            hi_nibble = nibs[i + 1]
            i += 2
            out.append(chr((hi_nibble << 4) | lo_nibble))
    return "".join(out).rstrip("\x00"), 1 + size


def _u16(v: int) -> bytes:
    return int(v).to_bytes(2, "little", signed=False)


def _u32(v: int) -> bytes:
    return int(v).to_bytes(4, "little", signed=False)


@dataclass(frozen=True)
class StarsApiDesign:
    design_number: int
    hull_id: int
    pic: int
    armor: int
    slot_count: int
    turn_designed: int
    total_built: int
    total_remaining: int
    slots: tuple[ComponentRef, ...]
    name: str
    is_full_design: bool = True
    is_transferred: bool = False
    is_starbase: bool = False
    # Non-full DesignBlocks store mass instead of full-design metadata.
    mass: int = 0


@dataclass(frozen=True)
class DecodedStarsApiDesign:
    design_number: int
    hull_id: int
    pic: int
    armor: int | None
    slot_count: int
    turn_designed: int | None
    total_built: int | None
    total_remaining: int | None
    slots: tuple[ComponentRef, ...]
    name: str
    name_bytes: bytes
    is_full_design: bool
    is_transferred: bool
    is_starbase: bool
    mass: int | None
    raw_second_byte: int
    normalized_second_byte: int
    type27_bit0_was_clear: bool


def _validate_design(d: StarsApiDesign) -> None:
    if not 0 <= int(d.design_number) <= 15:
        raise ValueError("StarsAPI design number must be 0..15")
    if not 0 <= int(d.hull_id) <= 255 or not 0 <= int(d.pic) <= 255:
        raise ValueError("StarsAPI hull/picture IDs must fit one byte")
    if d.is_full_design:
        if len(d.slots) != int(d.slot_count):
            raise ValueError(
                f"StarsAPI full DesignBlock slot_count={d.slot_count} but {len(d.slots)} slots supplied"
            )
        if not 0 <= int(d.armor) <= 0xFFFF:
            raise ValueError("StarsAPI armor must fit uint16")
        if not 0 <= int(d.turn_designed) <= 0xFFFF:
            raise ValueError("StarsAPI turnDesigned must fit uint16")
        for name, value in (("totalBuilt", d.total_built), ("totalRemaining", d.total_remaining)):
            if not 0 <= int(value) <= 0xFFFFFFFF:
                raise ValueError(f"StarsAPI {name} must fit uint32")
        for slot in d.slots:
            if not 0 <= int(slot.category) <= 0xFFFF:
                raise ValueError("StarsAPI component category must fit uint16")
            if not 0 <= int(slot.item_id) <= 255 or not 0 <= int(slot.count) <= 255:
                raise ValueError("StarsAPI component item/count must fit one byte")
    elif not 0 <= int(d.mass) <= 0xFFFF:
        raise ValueError("StarsAPI abbreviated-design mass must fit uint16")


def encode_design_block(design: StarsApiDesign) -> bytes:
    """Port of StarsAPI ``DesignBlock.encode()``.

    The byte layout follows the Java source directly:
      byte0  = 7 for full designs, 3 otherwise
      byte1  = 1 | designNumber<<2 | transferred/starbase flags
      byte2  = hull
      byte3  = picture
      full: armor, slotCount, turnDesigned, totalBuilt, totalRemaining,
            four bytes per slot, then nameBytes
      short: mass, then nameBytes
    """
    _validate_design(design)
    name_bytes = encode_stars_string(design.name)
    data = bytearray()
    data.append(7 if design.is_full_design else 3)
    second = 1 | ((int(design.design_number) & 0x0F) << 2)
    if design.is_transferred:
        second |= 0x80
    if design.is_starbase:
        second |= 0x40
    data.append(second)
    data.append(int(design.hull_id) & 0xFF)
    data.append(int(design.pic) & 0xFF)
    if design.is_full_design:
        data += _u16(design.armor)
        data.append(int(design.slot_count) & 0xFF)
        data += _u16(design.turn_designed)
        data += _u32(design.total_built)
        data += _u32(design.total_remaining)
        for slot in design.slots:
            data += _u16(slot.category)
            data += bytes([int(slot.item_id) & 0xFF, int(slot.count) & 0xFF])
    else:
        data += _u16(design.mass)
    data += name_bytes
    return bytes(data)


def decode_design_block(data: bytes, *, allow_type27_bit0_clear: bool = False) -> DecodedStarsApiDesign:
    """Port of StarsAPI ``DesignBlock.decode()`` with its Type27 normalization.

    ``DesignChangeBlock.decode()`` in StarsAPI strips the two wrapper bytes and,
    when embedded byte 1 bit 0 is clear, sets that bit before delegating to
    ``DesignBlock.decode()``.  Setting ``allow_type27_bit0_clear`` performs that
    exact normalization while recording the original value.
    """
    if len(data) < 6:
        raise ValueError("StarsAPI DesignBlock is too short")
    if (data[0] & 3) != 3 or (data[0] & 0xF8) != 0:
        raise ValueError(f"Unexpected StarsAPI design first byte 0x{data[0]:02x}")
    raw_second = int(data[1])
    normalized_second = raw_second
    bit0_clear = (normalized_second & 0x01) == 0
    if bit0_clear:
        if not allow_type27_bit0_clear:
            raise ValueError(f"Unexpected StarsAPI design second byte 0x{raw_second:02x}: bit0 clear")
        normalized_second |= 0x01
    if normalized_second & 0x02:
        raise ValueError(f"Unexpected StarsAPI design second byte 0x{normalized_second:02x}: bit1 set")
    if (normalized_second & 0x01) != 0x01:
        raise ValueError(f"Unexpected StarsAPI design second byte 0x{normalized_second:02x}: bit0 not set")

    is_full = bool(data[0] & 0x04)
    is_transferred = bool(normalized_second & 0x80)
    is_starbase = bool(normalized_second & 0x40)
    design_number = (normalized_second & 0x3C) >> 2
    hull_id = int(data[2])
    pic = int(data[3])

    if is_full:
        if len(data) < 17:
            raise ValueError("StarsAPI full DesignBlock is shorter than 17-byte fixed header")
        armor = int.from_bytes(data[4:6], "little")
        slot_count = int(data[6])
        turn_designed = int.from_bytes(data[7:9], "little")
        total_built = int.from_bytes(data[9:13], "little")
        total_remaining = int.from_bytes(data[13:17], "little")
        index = 17
        slots: list[ComponentRef] = []
        for _ in range(slot_count):
            if len(data) < index + 4:
                raise ValueError("StarsAPI DesignBlock is truncated inside component slots")
            slots.append(ComponentRef(
                int.from_bytes(data[index : index + 2], "little"),
                int(data[index + 2]),
                int(data[index + 3]),
            ))
            index += 4
        mass = None
    else:
        armor = None
        slot_count = 0
        turn_designed = None
        total_built = None
        total_remaining = None
        slots = []
        mass = int.from_bytes(data[4:6], "little")
        index = 6

    name, used = decode_stars_string(data[index:])
    name_bytes = bytes(data[index : index + used])
    index += used
    if index != len(data):
        raise ValueError(
            f"Unexpected StarsAPI design size: parsed {index} bytes but block has {len(data)}"
        )
    return DecodedStarsApiDesign(
        design_number=design_number,
        hull_id=hull_id,
        pic=pic,
        armor=armor,
        slot_count=slot_count,
        turn_designed=turn_designed,
        total_built=total_built,
        total_remaining=total_remaining,
        slots=tuple(slots),
        name=name,
        name_bytes=name_bytes,
        is_full_design=is_full,
        is_transferred=is_transferred,
        is_starbase=is_starbase,
        mass=mass,
        raw_second_byte=raw_second,
        normalized_second_byte=normalized_second,
        type27_bit0_was_clear=bit0_clear,
    )


def encode_type27_embedded_design(design: StarsApiDesign) -> bytes:
    """Encode the StarsAPI DesignBlock body used inside Type27.

    Start with the authoritative normal DesignBlock encoding, then mirror the
    behavior documented by StarsAPI ``DesignChangeBlock.decode()`` by clearing
    only embedded byte-1 bit 0.  All other fields remain exactly the StarsAPI
    encoding.
    """
    normal = bytearray(encode_design_block(design))
    normal[1] &= 0xFE
    embedded = bytes(normal)
    # Fail closed on any body that cannot be parsed by the StarsAPI rules.
    parsed = decode_design_block(embedded, allow_type27_bit0_clear=True)
    if not parsed.type27_bit0_was_clear:
        raise RuntimeError("Type27 embedded StarsAPI body unexpectedly retained bit0")
    # Restore the one normalized bit and require exact ordinary DesignBlock bytes.
    restored = bytearray(embedded)
    restored[1] |= 0x01
    if bytes(restored) != encode_design_block(design):
        raise RuntimeError("StarsAPI Type27 body normalization did not round-trip exactly")
    return embedded


def starsapi_body_roundtrip(data: bytes, *, type27_embedded: bool) -> bytes:
    """Decode and re-encode a body for pre-host byte-level verification."""
    parsed = decode_design_block(data, allow_type27_bit0_clear=type27_embedded)
    design = StarsApiDesign(
        design_number=parsed.design_number,
        hull_id=parsed.hull_id,
        pic=parsed.pic,
        armor=int(parsed.armor or 0),
        slot_count=parsed.slot_count,
        turn_designed=int(parsed.turn_designed or 0),
        total_built=int(parsed.total_built or 0),
        total_remaining=int(parsed.total_remaining or 0),
        slots=parsed.slots,
        name=parsed.name,
        is_full_design=parsed.is_full_design,
        is_transferred=parsed.is_transferred,
        is_starbase=parsed.is_starbase,
        mass=int(parsed.mass or 0),
    )
    if type27_embedded:
        return encode_type27_embedded_design(design)
    return encode_design_block(design)
