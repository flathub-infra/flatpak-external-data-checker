import hashlib
import unittest
from array import array

from src.lib.checksums import MultiDigest, MultiHash


class TestMultiHashConstructor(unittest.TestCase):
    def test_constructor_matches_hashlib(self):
        for args, kwargs in (
            ((), {}),
            ((b"initial",), {}),
            ((b"initial",), {"usedforsecurity": False}),
            ((bytearray(b"initial"),), {}),
            ((memoryview(b"initial"),), {}),
            ((array("B", b"initial"),), {}),
        ):
            with self.subTest(args=args, kwargs=kwargs):
                multihash = MultiHash(*args, **kwargs)
                multihash.update(b"more")
                digests = multihash.hexdigest()
                for kind in MultiDigest._fields:
                    expected = getattr(hashlib, kind)(*args, **kwargs)
                    expected.update(b"more")
                    self.assertEqual(getattr(digests, kind), expected.hexdigest())

    def test_keyword_inputs_match_hashlib(self):
        for keyword in ("string", "data"):
            with self.subTest(keyword=keyword):
                kwargs = {keyword: b"initial"}
                try:
                    expected = {
                        kind: getattr(hashlib, kind)(**kwargs).hexdigest()
                        for kind in MultiDigest._fields
                    }
                except TypeError:
                    with self.assertRaises(TypeError):
                        MultiHash(**kwargs)
                else:
                    digests = MultiHash(**kwargs).hexdigest()
                    self.assertEqual(digests._asdict(), expected)

    def test_update_accepts_buffers(self):
        for data in (bytearray(b"more"), memoryview(b"more"), array("B", b"more")):
            with self.subTest(buffer_type=type(data)):
                multihash = MultiHash(b"initial")
                multihash.update(data)
                digests = multihash.hexdigest()
                for kind in MultiDigest._fields:
                    expected = getattr(hashlib, kind)(b"initial")
                    expected.update(data)
                    self.assertEqual(getattr(digests, kind), expected.hexdigest())
