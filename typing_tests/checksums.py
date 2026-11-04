from array import array

from typing_extensions import assert_type

from src.lib.checksums import MultiDigest, MultiHash


def buffer_inputs() -> None:
    assert_type(MultiHash(), MultiHash)
    assert_type(MultiHash(b"initial"), MultiHash)
    assert_type(MultiHash(bytearray(b"initial")), MultiHash)
    assert_type(MultiHash(memoryview(b"initial")), MultiHash)
    assert_type(MultiHash(array("B", b"initial")), MultiHash)
    assert_type(MultiHash(b"initial", usedforsecurity=False), MultiHash)
    hasher = MultiHash()
    assert_type(hasher.update(b"more"), None)
    assert_type(hasher.update(bytearray(b"more")), None)
    assert_type(hasher.update(memoryview(b"more")), None)
    assert_type(hasher.update(array("B", b"more")), None)
    assert_type(hasher.hexdigest(), MultiDigest)
