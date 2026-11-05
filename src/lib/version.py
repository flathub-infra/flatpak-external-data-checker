import re
import typing as t
from functools import wraps


def _number_key(value: str) -> tuple[int, str]:
    match = re.match(r"[0-9]*", value)
    assert match is not None
    digits = match.group().lstrip("0")
    return len(digits), digits


def _compare_appstream_part(left: str, right: str) -> int:
    i = j = 0
    while i < len(left) or j < len(right):
        a, b = left[i : i + 1], right[j : j + 1]
        if a and b and a.isdigit() and b.isdigit():
            start_i, start_j = i, j
            while i < len(left) and left[i].isdigit():
                i += 1
            while j < len(right) and right[j].isdigit():
                j += 1
            a_key, b_key = _number_key(left[start_i:i]), _number_key(right[start_j:j])
            if a_key != b_key:
                return 1 if a_key > b_key else -1
            continue
        if a == b:
            i += 1
            j += 1
            continue
        if "~" in (a, b):
            return -1 if a == "~" else 1
        # AppStream stops at a zero-prefixed suffix opposite an empty part.
        if (not a and b == "0") or (not b and a == "0"):
            a_key, b_key = _number_key(left[i:]), _number_key(right[j:])
            return (a_key > b_key) - (a_key < b_key)
        if not a or not b:
            return -1 if not a else 1
        if a.isdigit() != b.isdigit():
            return -1 if a.isdigit() else 1
        if a.isalpha() != b.isalpha():
            return -1 if a.isalpha() else 1
        return -1 if a < b else 1
    return 0


def compare_appstream_versions(left: str | None, right: str | None) -> int | None:
    """Compare release versions using AppStream's epoch, version and revision rules."""
    # Reference: https://github.com/ximion/appstream/blob/main/src/as-vercmp.c
    if not left or not right or not left.isascii() or not right.isascii():
        return None
    parsed = []
    for value in (left, right):
        epoch, separator, version = value.partition(":")
        if not separator:
            epoch, version = "0", value
        if not epoch.isdigit() or ":" in version:
            return None
        upstream, separator, revision = version.rpartition("-")
        parsed.append(
            (epoch, upstream if separator else version, revision if separator else "0")
        )
    a_epoch, b_epoch = _number_key(parsed[0][0]), _number_key(parsed[1][0])
    if a_epoch != b_epoch:
        return 1 if a_epoch > b_epoch else -1
    for a, b in zip(parsed[0][1:], parsed[1][1:], strict=True):
        result = _compare_appstream_part(a, b)
        if result:
            return result
    return 0


def compare_result(
    operator: t.Callable[[int], bool],
) -> t.Callable[[t.Callable[..., bool]], t.Callable[..., bool | t.Any]]:
    def decorator(
        method: t.Callable[..., bool],
    ) -> t.Callable[..., bool | t.Any]:
        @wraps(method)
        def wrapper(self: "LooseVersion", other: t.Any) -> bool | t.Any:
            result = self._compare_to(other)
            if result is NotImplemented:
                return NotImplemented
            return operator(result)

        return wrapper

    return decorator


class LooseVersion:
    def __init__(self, vstring: str) -> None:
        self.vstring: str = vstring
        self.version: list[int | str] = self._parse_components(vstring)

    def __hash__(self):
        return hash(self.vstring)

    @staticmethod
    def split_components(vstring: str) -> list[str]:
        """Split a version without normalizing the spelling of its components."""
        pattern: t.Pattern[str] = re.compile(r"(\d+|[a-z]+|\.)", re.VERBOSE)
        return [p for p in pattern.split(vstring) if p and p != "."]

    def _parse_components(self, vstring: str) -> list[int | str]:
        result: list[int | str] = []
        for part in self.split_components(vstring):
            try:
                result.append(int(part))
            except ValueError:
                result.append(part)
        return result

    def _compare_to(self, other: t.Any) -> int | t.Any:
        if isinstance(other, str):
            other = LooseVersion(other)
        elif not isinstance(other, LooseVersion):
            return NotImplemented

        max_len: int = max(len(self.version), len(other.version))
        for i in range(max_len):
            if i >= len(self.version):
                return -1
            if i >= len(other.version):
                return 1

            left = self.version[i]
            right = other.version[i]

            # Python 2 semantics: instead of raising TypeError, int < str
            if isinstance(left, int) and isinstance(right, str):
                return -1
            if isinstance(left, str) and isinstance(right, int):
                return 1
            if isinstance(left, int) and isinstance(right, int):
                if left < right:
                    return -1
                if left > right:
                    return 1
            elif isinstance(left, str) and isinstance(right, str):
                if left < right:
                    return -1
                if left > right:
                    return 1

        return 0

    @compare_result(lambda result: result == 0)
    def __eq__(self, other: t.Any) -> bool | t.Any: ...

    @compare_result(lambda result: result < 0)
    def __lt__(self, other: t.Any) -> bool | t.Any: ...

    @compare_result(lambda result: result <= 0)
    def __le__(self, other: t.Any) -> bool | t.Any: ...

    @compare_result(lambda result: result > 0)
    def __gt__(self, other: t.Any) -> bool | t.Any: ...

    @compare_result(lambda result: result >= 0)
    def __ge__(self, other: t.Any) -> bool | t.Any: ...

    @compare_result(lambda result: result != 0)
    def __ne__(self, other: t.Any) -> bool | t.Any: ...
