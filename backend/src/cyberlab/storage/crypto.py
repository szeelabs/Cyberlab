"""Streaming, authenticated encryption for snapshot files (AES-256-GCM, chunked).

File format (all integers big-endian)::

    magic      8 bytes  b"CLSNAP1\\0"
    key id     8 bytes  first 8 bytes of SHA-256(key)   (detects the wrong key; not secret)
    nonce pfx  7 bytes  random per file
    chunks     ...      AES-GCM(chunk) + 16-byte tag, plaintext chunks of CHUNK bytes

Each chunk's 12-byte nonce is ``prefix || counter (4 bytes) || last (1 byte)``, and the
header is authenticated with every chunk. Changing, reordering, dropping or truncating
chunks, or splicing two files, makes decryption fail. This is the "STREAM" construction
(Hoang, Reyhanitabar, Rogaway, Vizár, 2015).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"CLSNAP1\0"
CHUNK = 1024 * 1024
TAG = 16
HEADER = len(MAGIC) + 8 + 7
MAX_CHUNKS = 2**32 - 1


class DecryptionError(ValueError):
    pass


def parse_key(value: str) -> bytes:
    """Accept a 32-byte key as base64 (standard or URL-safe) or 64 hex characters."""
    value = value.strip()
    candidates = []
    if len(value) == 64:
        try:
            candidates.append(binascii.unhexlify(value))
        except (binascii.Error, ValueError):
            pass
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            candidates.append(decoder(value + "=" * (-len(value) % 4)))
        except (binascii.Error, ValueError):
            pass
    for key in candidates:
        if len(key) == 32:
            return key
    raise ValueError("encryption key must be 32 bytes, as base64 or 64 hex characters")


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:8]


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    if counter > MAX_CHUNKS:
        raise ValueError("file too large to encrypt")
    return prefix + struct.pack(">IB", counter, 1 if last else 0)


def encrypt_file(key: bytes, source: Path, destination: Path, chunk: int = CHUNK) -> None:
    aead = AESGCM(key)
    prefix = os.urandom(7)
    header = MAGIC + key_id(key) + prefix
    with open(source, "rb") as src, open(destination, "wb") as dst:
        dst.write(header)
        counter = 0
        block = src.read(chunk)
        while True:
            following = src.read(chunk)
            last = not following
            dst.write(aead.encrypt(_nonce(prefix, counter, last), block, header))
            if last:
                break
            block, counter = following, counter + 1


def decrypt_file(key: bytes, source: Path, destination: Path, chunk: int = CHUNK) -> None:
    aead = AESGCM(key)
    with open(source, "rb") as src:
        header = src.read(HEADER)
        if len(header) != HEADER or not header.startswith(MAGIC):
            raise DecryptionError("not a CyberLab encrypted snapshot")
        if header[len(MAGIC) : len(MAGIC) + 8] != key_id(key):
            raise DecryptionError("snapshot was encrypted with a different key")
        prefix = header[-7:]
        tmp = Path(str(destination) + ".part")
        try:
            with open(tmp, "wb") as dst:
                counter = 0
                block = src.read(chunk + TAG)
                if not block:
                    raise DecryptionError("snapshot is truncated")
                while True:
                    following = src.read(chunk + TAG)
                    last = not following
                    try:
                        dst.write(aead.decrypt(_nonce(prefix, counter, last), block, header))
                    except InvalidTag as exc:
                        raise DecryptionError(
                            "snapshot failed authentication (tampered, truncated or corrupt)"
                        ) from exc
                    if last:
                        break
                    block, counter = following, counter + 1
            os.replace(tmp, destination)  # only fully verified plaintext is ever exposed
        finally:
            tmp.unlink(missing_ok=True)
