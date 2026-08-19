from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

MOD32 = 2**32


def u8(data: bytes, off: int = 0) -> int:
    return data[off] & 0xFF


def u16(data: bytes, off: int = 0) -> int:
    return int.from_bytes(data[off:off+2], 'little', signed=False)


def u32(data: bytes, off: int = 0) -> int:
    return int.from_bytes(data[off:off+4], 'little', signed=False)


def read_n(data: bytes, off: int, n: int) -> int:
    return 0 if n == 0 else int.from_bytes(data[off:off+n], 'little', signed=False)


def content_len(code: int) -> int:
    return (0,1,2,4)[code & 3]


def dataclass_dict(value: Any) -> Any:
    if hasattr(value, '__dataclass_fields__'):
        return asdict(value)
    return value
