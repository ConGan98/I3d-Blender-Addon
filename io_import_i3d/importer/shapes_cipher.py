"""
Port of Donkie/I3DShapesTool's I3DCipher (MIT) and FileHeader/CipherStream.

Algorithm: 16-uint32 state initialised from KEY_CONST[seed*16 .. seed*16+15];
state[8]/[9] act as a block counter (set to 0 in __init__; overridden per call
to process() with the caller-supplied block_index). Per 64-byte block:
copy state to tempKey, run 10 rounds of Shuffle1/Shuffle2 (Rol/Ror + add + XOR),
XOR each of 16 buffer words with state[j]+tempKey[j], advance counter.

NOTE: the cipher is *read-pattern-dependent*. Each call to .process() advances
the block counter by ceil(len/CRYPT_BLOCK_SIZE) regardless of how much of the
last block is real data — so the caller MUST read in the same chunk widths
used by the writer. EndianBinaryReader's per-primitive reads naturally produce
this pattern.
"""
from __future__ import annotations

from typing import BinaryIO

from .shapes_keyconst import KEY_CONST

CRYPT_BLOCK_SIZE = 64
WORDS_PER_BLOCK = 16


def _rol32(v: int, n: int) -> int:
    v &= 0xFFFFFFFF
    return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF


def _ror32(v: int, n: int) -> int:
    v &= 0xFFFFFFFF
    return ((v >> n) | (v << (32 - n))) & 0xFFFFFFFF


def _shuffle1(k: list[int], i1: int, i2: int, i3: int, i4: int) -> None:
    k[i3] = (k[i3] ^ _rol32((k[i2] + k[i1]) & 0xFFFFFFFF, 7)) & 0xFFFFFFFF
    k[i4] = (k[i4] ^ _rol32((k[i3] + k[i1]) & 0xFFFFFFFF, 9)) & 0xFFFFFFFF
    k[i2] = (k[i2] ^ _rol32((k[i3] + k[i4]) & 0xFFFFFFFF, 13)) & 0xFFFFFFFF
    k[i1] = (k[i1] ^ _ror32((k[i2] + k[i4]) & 0xFFFFFFFF, 14)) & 0xFFFFFFFF


def _shuffle2(k: list[int], i1: int, i2: int, i3: int, i4: int) -> None:
    k[i3] = (k[i3] ^ _rol32((k[i2] + k[i1]) & 0xFFFFFFFF, 7)) & 0xFFFFFFFF
    k[i4] = (k[i4] ^ _rol32((k[i2] + k[i3]) & 0xFFFFFFFF, 9)) & 0xFFFFFFFF
    k[i1] = (k[i1] ^ _rol32((k[i3] + k[i4]) & 0xFFFFFFFF, 13)) & 0xFFFFFFFF
    k[i2] = (k[i2] ^ _ror32((k[i4] + k[i1]) & 0xFFFFFFFF, 14)) & 0xFFFFFFFF


def _process_blocks(words: list[int], key: list[int]) -> None:
    if len(words) % WORDS_PER_BLOCK != 0:
        raise ValueError(f"words length {len(words)} not multiple of {WORDS_PER_BLOCK}")
    block_counter = key[8] | (key[9] << 32)
    for i in range(0, len(words), WORDS_PER_BLOCK):
        temp = key[:]
        for _ in range(10):
            _shuffle1(temp, 0x0, 0xC, 0x4, 0x8)
            _shuffle1(temp, 0x5, 0x1, 0x9, 0xD)
            _shuffle1(temp, 0xA, 0x6, 0xE, 0x2)
            _shuffle1(temp, 0xF, 0xB, 0x3, 0x7)
            _shuffle2(temp, 0x3, 0x0, 0x1, 0x2)
            _shuffle2(temp, 0x4, 0x5, 0x6, 0x7)
            _shuffle1(temp, 0xA, 0x9, 0xB, 0x8)
            _shuffle2(temp, 0xE, 0xF, 0xC, 0xD)
        for j in range(WORDS_PER_BLOCK):
            words[i + j] = (words[i + j] ^ ((key[j] + temp[j]) & 0xFFFFFFFF)) & 0xFFFFFFFF
        block_counter += 1
        key[8] = block_counter & 0xFFFFFFFF
        key[9] = (block_counter >> 32) & 0xFFFFFFFF


class I3DCipher:
    def __init__(self, seed: int):
        if not 0 <= seed <= 255:
            raise ValueError(f"seed must be 0..255, got {seed}")
        start = seed << 4
        self._initial_key = list(KEY_CONST[start:start + 16])
        self._initial_key[8] = 0
        self._initial_key[9] = 0
        self.seed = seed

    def process(self, buffer: bytearray, block_index: int) -> int:
        """In-place XOR cipher (encrypt and decrypt are the same op).

        Pads `buffer` to a 64-byte multiple with zeros internally; only the
        first len(buffer) bytes of decrypted output are written back. Returns
        next block_index = block_index + ceil(len(buffer)/64).
        """
        n = len(buffer)
        if n == 0:
            return block_index
        padded_n = ((n + CRYPT_BLOCK_SIZE - 1) // CRYPT_BLOCK_SIZE) * CRYPT_BLOCK_SIZE
        copy = bytearray(padded_n)
        copy[:n] = buffer
        words = [int.from_bytes(copy[i:i + 4], 'little') for i in range(0, padded_n, 4)]
        key = list(self._initial_key)
        key[8] = block_index & 0xFFFFFFFF
        key[9] = (block_index >> 32) & 0xFFFFFFFF
        _process_blocks(words, key)
        for i, w in enumerate(words):
            copy[i * 4:(i + 1) * 4] = w.to_bytes(4, 'little')
        buffer[:n] = copy[:n]
        return block_index + (padded_n // CRYPT_BLOCK_SIZE)


# ---------------------------------------------------------------------------
# File header (4 bytes; 2-byte version, 2-byte seed, layout depends on version)
# ---------------------------------------------------------------------------

class FileHeader:
    def __init__(self, version: int, seed: int):
        self.version = version
        self.seed = seed

    @classmethod
    def read(cls, stream: BinaryIO) -> "FileHeader":
        b = stream.read(4)
        if len(b) != 4:
            raise ValueError("Truncated .i3d.shapes header")
        b1, b2, b3, b4 = b[0], b[1], b[2], b[3]
        if b1 >= 4:
            return cls(version=b1, seed=b3)
        if b4 == 2 or b4 == 3:
            return cls(version=b4, seed=b2)
        raise ValueError(
            f"Unknown .i3d.shapes header: {b.hex()} — first byte must be >=4 or last byte must be 2/3"
        )

    def __repr__(self):
        return f"FileHeader(version={self.version}, seed=0x{self.seed:02X})"


# ---------------------------------------------------------------------------
# Stream wrapper. Read-only. Read pattern matters; see module docstring.
# ---------------------------------------------------------------------------

class CipherStream:
    def __init__(self, base_stream: BinaryIO, cipher: I3DCipher):
        self._base = base_stream
        self._cipher = cipher
        self._block_offset = 0

    def read(self, count: int) -> bytes:
        if count <= 0:
            return b""
        buf = bytearray()
        while len(buf) < count:
            chunk = self._base.read(count - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        if not buf:
            return b""
        self._block_offset = self._cipher.process(buf, self._block_offset)
        return bytes(buf)

    @property
    def block_offset(self) -> int:
        return self._block_offset

    def close(self):
        self._base.close()
