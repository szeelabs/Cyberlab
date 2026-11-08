import os

import pytest

from cyberlab.storage import EncryptedSnapshotStore, LocalSnapshotStore, StorageError
from cyberlab.storage.crypto import (
    HEADER,
    TAG,
    DecryptionError,
    decrypt_file,
    encrypt_file,
    generate_key,
    parse_key,
)

KEY = bytes(range(32))
OTHER = bytes(range(1, 33))
CHUNK = 64  # small chunks so multi-chunk cases stay fast


def roundtrip(tmp_path, data, key=KEY, chunk=CHUNK):
    src, enc, out = tmp_path / "in", tmp_path / "enc", tmp_path / "out"
    src.write_bytes(data)
    encrypt_file(key, src, enc, chunk=chunk)
    decrypt_file(key, enc, out, chunk=chunk)
    return out.read_bytes(), enc.read_bytes()


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK, 3 * CHUNK + 7])
def test_roundtrip_all_boundaries(tmp_path, size):
    data = os.urandom(size)
    plain, enc = roundtrip(tmp_path, data)
    assert plain == data
    chunks = max(1, -(-size // CHUNK))
    assert len(enc) == HEADER + size + chunks * TAG


def test_ciphertext_reveals_nothing_obvious(tmp_path):
    data = b"secret flag{cyberlab} " * 20
    _, enc = roundtrip(tmp_path, data)
    assert b"flag{cyberlab}" not in enc
    _, enc2 = roundtrip(tmp_path, data)
    assert enc != enc2  # fresh nonce prefix each time


def corrupt_and_decrypt(tmp_path, mutate, key=KEY):
    src, enc, out = tmp_path / "in", tmp_path / "enc", tmp_path / "out"
    src.write_bytes(os.urandom(3 * CHUNK + 5))
    encrypt_file(KEY, src, enc, chunk=CHUNK)
    enc.write_bytes(mutate(bytearray(enc.read_bytes())))
    with pytest.raises(DecryptionError) as exc:
        decrypt_file(key, enc, out, chunk=CHUNK)
    assert not out.exists() and not (tmp_path / "out.part").exists()  # nothing exposed
    return str(exc.value)


def flip(i):
    def f(b):
        b[i] ^= 1
        return bytes(b)

    return f


def test_tampered_byte_detected(tmp_path):
    assert "authentication" in corrupt_and_decrypt(tmp_path, flip(HEADER + 10))


def test_tampered_header_detected(tmp_path):
    corrupt_and_decrypt(tmp_path, flip(HEADER - 1))  # nonce prefix is authenticated


def test_truncation_detected(tmp_path):
    # dropping the final chunk: the new last chunk wasn't sealed as "last"
    corrupt_and_decrypt(tmp_path, lambda b: bytes(b[: HEADER + 2 * (CHUNK + TAG)]))
    corrupt_and_decrypt(tmp_path, lambda b: bytes(b[:HEADER]))


def test_reordered_chunks_detected(tmp_path):
    def swap(b):
        c = CHUNK + TAG
        first, second = b[HEADER : HEADER + c], b[HEADER + c : HEADER + 2 * c]
        return bytes(b[:HEADER] + second + first + b[HEADER + 2 * c :])

    corrupt_and_decrypt(tmp_path, swap)


def test_splicing_two_files_detected(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for p in (a, b):
        p.write_bytes(os.urandom(2 * CHUNK))
    encrypt_file(KEY, a, tmp_path / "a.enc", chunk=CHUNK)
    encrypt_file(KEY, b, tmp_path / "b.enc", chunk=CHUNK)
    ea, eb = (tmp_path / "a.enc").read_bytes(), (tmp_path / "b.enc").read_bytes()
    spliced = tmp_path / "s.enc"
    spliced.write_bytes(ea[: HEADER + CHUNK + TAG] + eb[HEADER + CHUNK + TAG :])
    with pytest.raises(DecryptionError):
        decrypt_file(KEY, spliced, tmp_path / "out", chunk=CHUNK)


def test_wrong_key_and_wrong_format(tmp_path):
    assert "different key" in corrupt_and_decrypt(tmp_path, bytes, key=OTHER)
    (tmp_path / "plain").write_bytes(b"just a tar.gz, not encrypted")
    with pytest.raises(DecryptionError, match="not a CyberLab encrypted"):
        decrypt_file(KEY, tmp_path / "plain", tmp_path / "out")


def test_key_parsing():
    k = generate_key()
    assert len(parse_key(k)) == 32
    assert parse_key("00" * 32) == bytes(32)
    for bad in ["short", "", "zz" * 32, "A" * 10]:
        with pytest.raises(ValueError):
            parse_key(bad)


def test_encrypted_store_wraps_any_store(tmp_path):
    inner = LocalSnapshotStore(tmp_path / "store")
    store = EncryptedSnapshotStore(inner, KEY, tmp_path / "work")
    assert store.name == "local+aes256gcm"
    src = tmp_path / "snap.tar.gz"
    src.write_bytes(b"workspace archive bytes")
    store.put("ws/s1.tar.gz", src)
    stored = (tmp_path / "store" / "ws" / "s1.tar.gz").read_bytes()
    assert b"workspace archive" not in stored  # at rest it's ciphertext
    store.get("ws/s1.tar.gz", tmp_path / "back")
    assert (tmp_path / "back").read_bytes() == b"workspace archive bytes"
    assert list((tmp_path / "work").iterdir()) == []  # temp files cleaned up
    wrong = EncryptedSnapshotStore(inner, OTHER, tmp_path / "work")
    with pytest.raises(StorageError, match="different key"):
        wrong.get("ws/s1.tar.gz", tmp_path / "back2")
