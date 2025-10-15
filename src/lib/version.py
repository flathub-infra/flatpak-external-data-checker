import re
import typing as t
from functools import wraps


def compare_result(
    operator: t.Callable[[int], bool],
) -> t.Callable[[t.Callable[..., bool]], t.Callable[..., t.Union[bool, t.Any]]]:
    def decorator(
        method: t.Callable[..., bool],
    ) -> t.Callable[..., t.Union[bool, t.Any]]:
        @wraps(method)
        def wrapper(self: "LooseVersion", other: t.Any) -> t.Union[bool, t.Any]:
            result = self._compare_to(other)
            if result is NotImplemented:
                return NotImplemented
            return operator(result)

        return wrapper

    return decorator


class LooseVersion:
    def __init__(self, vstring: str) -> None:
        self.vstring: str = vstring
        self.version: t.List[t.Union[int, str]] = self._parse_components(vstring)

    def _parse_components(self, vstring: str) -> t.List[t.Union[int, str]]:
        pattern: t.Pattern[str] = re.compile(r"(\d+|[a-z]+|\.)", re.VERBOSE)
        parts: t.List[str] = [p for p in pattern.split(vstring) if p and p != "."]

        result: t.List[t.Union[int, str]] = []
        for part in parts:
            try:
                result.append(int(part))
            except ValueError:
                result.append(part)
        return result

    def _compare_to(self, other: t.Any) -> t.Union[int, t.Any]:
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
    def __eq__(self, other: t.Any) -> t.Union[bool, t.Any]: ...

    @compare_result(lambda result: result < 0)
    def __lt__(self, other: t.Any) -> t.Union[bool, t.Any]: ...

    @compare_result(lambda result: result <= 0)
    def __le__(self, other: t.Any) -> t.Union[bool, t.Any]: ...

    @compare_result(lambda result: result > 0)
    def __gt__(self, other: t.Any) -> t.Union[bool, t.Any]: ...

    @compare_result(lambda result: result >= 0)
    def __ge__(self, other: t.Any) -> t.Union[bool, t.Any]: ...

    @compare_result(lambda result: result != 0)
    def __ne__(self, other: t.Any) -> t.Union[bool, t.Any]: ...
