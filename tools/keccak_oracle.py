"""Dual-implementation Keccak-256 oracle for test-vector generation.

Every hash is computed with two independent implementations (pycryptodome
and eth_hash) and the results must agree; a disagreement aborts the run.
This is the non-self-referential ground truth for the Merkle vectors:
nothing here shares code with the Fe implementation.

WARNING: Python's hashlib.sha3_256 is NIST SHA-3, NOT Keccak-256. Never
use it as an oracle for EVM hashing.
"""

from Crypto.Hash import keccak as _pycryptodome_keccak
from eth_hash.auto import keccak as _eth_hash_keccak


def keccak256(data: bytes) -> bytes:
    """Keccak-256 digest of `data`, cross-checked by two implementations."""
    h = _pycryptodome_keccak.new(digest_bits=256)
    h.update(data)
    r1 = h.digest()
    r2 = _eth_hash_keccak(data)
    assert r1 == r2, "keccak implementations disagree"
    return r1


def word(n: int) -> bytes:
    """A uint256 ABI word (32 bytes, big-endian)."""
    assert 0 <= n < 2**256
    return n.to_bytes(32, "big")


def hex_word(n: int) -> str:
    """A uint256 as a 0x-prefixed 64-hex-digit string."""
    return "0x" + word(n).hex()


if __name__ == "__main__":
    # Known vector: keccak256 of the empty input.
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    # Known vector: keccak256 of 32 zero bytes (the std fixture's LEAF_2_0).
    assert keccak256(bytes(32)).hex() == (
        "290decd9548b62a8d60345a988386fc84ba6bc95484008f6362f93160ef3e563"
    )
    print("keccak oracle self-test OK")
