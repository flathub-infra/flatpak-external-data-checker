from __future__ import annotations

import hashlib
import logging
import typing as t

from .errors import SourceUpdateError


log = logging.getLogger(__name__)


class MultiDigest(t.NamedTuple):
    md5: t.Optional[str] = None
    sha1: t.Optional[str] = None
    sha256: t.Optional[str] = None
    sha512: t.Optional[str] = None

    @classmethod
    def from_source(cls, source: t.Dict) -> MultiDigest:
        # pylint: disable=no-member
        digests = {k: source[k] for k in cls._fields if k in source}
        assert digests, source
        return cls(**digests)

    @property
    def digests(self) -> t.Set[str]:
        # pylint: disable=no-member
        return {k for k in self._fields if getattr(self, k) is not None}

    def __eq__(self, other):
        assert isinstance(other, type(self)), other
        # Iterate digest types from strongest to weakest,
        # if both sides have a type in common - compare it and return result
        for kind in reversed(self._fields):  # pylint: disable=no-member
            self_digest = getattr(self, kind)
            other_digest = getattr(other, kind)
            if self_digest is not None and other_digest is not None:
                return self_digest == other_digest
        # If no common digest type found, we can't compare, raise an error
        raise ValueError(f"No common digest type for {self} and {other}")

    def __ne__(self, other):
        return not self == other

    def update_source(self, source: t.Dict):
        # Find digest types that are both not null in self and set in the source
        to_update = {
            kind: digest
            for kind, digest in self._asdict().items()  # pylint: disable=no-member
            if kind in source and digest is not None
        }
        if not to_update:
            # We don't have a common digest type with the source, bail out
            raise SourceUpdateError(f"No matching digest type for {self} in {source}")
        log.debug("Updating %s in %s", to_update.keys(), source)
        source.update(to_update)


class MultiHash:
    __slots__ = ("md5", "sha1", "sha256", "sha512")

    def __init__(self, *args, **kwargs):
        self.md5 = hashlib.md5(*args, **kwargs)  # nosec
        self.sha1 = hashlib.sha1(*args, **kwargs)  # nosec
        self.sha256 = hashlib.sha256(*args, **kwargs)
        self.sha512 = hashlib.sha512(*args, **kwargs)

    def update(self, data):
        self.md5.update(data)
        self.sha1.update(data)
        self.sha256.update(data)
        self.sha512.update(data)

    def hexdigest(self):
        return MultiDigest(
            md5=self.md5.hexdigest(),
            sha1=self.sha1.hexdigest(),
            sha256=self.sha256.hexdigest(),
            sha512=self.sha512.hexdigest(),
        )
