import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path

import aiohttp

from . import NETWORK_ERRORS, USER_AGENT
from .errors import CheckerFetchError

log = logging.getLogger(__name__)

CACHE_TTL = 60 * 60 * 24


def _get_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base).expanduser() / "fedc" / "robots"
    return Path.home() / ".cache" / "fedc" / "robots"


CACHE_DIR = _get_cache_dir()


class RobotsCache:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        cache_dir: Path | None = None,
    ):
        self._session = session
        self._memory: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}

        self._cache_dir = cache_dir or CACHE_DIR

        if self._cache_dir.is_symlink():
            raise RuntimeError("Cache directory must not be a symlink")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._base_dir = self._cache_dir.resolve()

    def _cache_path(self, netloc: str) -> Path:
        digest = hashlib.sha256(netloc.encode("utf-8")).hexdigest()
        path = self._cache_dir / f"{digest}.json"
        resolved = path.resolve(strict=False)

        try:
            resolved.relative_to(self._base_dir)
        except ValueError as err:
            raise RuntimeError("Cache path is outside cache directory") from err

        return resolved

    def _load_from_disk(self, netloc: str) -> urllib.robotparser.RobotFileParser | None:
        path = self._cache_path(netloc)
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        except OSError as err:
            log.debug("Failed to read robots cache for %s: %s", netloc, err)
            return None

        if not isinstance(data, dict):
            return None

        if time.time() - data.get("timestamp", 0) > CACHE_TTL:
            path.unlink(missing_ok=True)
            return None

        lines = data.get("lines")
        if not isinstance(lines, list):
            return None

        rp = urllib.robotparser.RobotFileParser()
        rp.parse(lines)
        return rp

    def _save_to_disk(self, netloc: str, lines: list[str]):
        path = self._cache_path(netloc)
        try:
            path.write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "lines": lines,
                    }
                )
            )
        except OSError as err:
            log.debug("Failed to write robots cache for %s: %s", netloc, err)

    async def _fetch_parser(
        self, robots_url: str, netloc: str
    ) -> urllib.robotparser.RobotFileParser:
        rp = urllib.robotparser.RobotFileParser()
        lines: list[str]

        try:
            async with self._session.get(robots_url, raise_for_status=False) as resp:
                if resp.status == 404:
                    log.debug("No robots.txt at %s, assuming allow all", robots_url)
                    lines = []
                elif resp.status in (401, 403):
                    log.debug(
                        "robots.txt at %s returned %d, assuming disallow all",
                        robots_url,
                        resp.status,
                    )
                    lines = ["User-agent: *", "Disallow: /"]
                else:
                    text = await resp.text()
                    lines = text.splitlines()
        except (asyncio.TimeoutError, *NETWORK_ERRORS) as err:
            log.debug(
                "Failed to fetch %s: %s, failing open (not cached)",
                robots_url,
                err,
            )
            rp.parse([])
            return rp

        rp.parse(lines)
        self._save_to_disk(netloc, lines)
        return rp

    async def _is_allowed(
        self,
        url: "str | urllib.parse.ParseResult | aiohttp.typedefs.StrOrURL",
    ) -> bool:
        if isinstance(url, urllib.parse.ParseResult):
            url = urllib.parse.urlunparse(url)
        else:
            url = str(url)

        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc

        if netloc in self._memory:
            rp = self._memory[netloc]
        else:
            if netloc not in self._locks:
                self._locks[netloc] = asyncio.Lock()
            async with self._locks[netloc]:
                if netloc in self._memory:
                    rp = self._memory[netloc]
                else:
                    cached = self._load_from_disk(netloc)
                    if cached is not None:
                        rp = cached
                    else:
                        robots_url = f"{parsed.scheme}://{netloc}/robots.txt"
                        rp = await self._fetch_parser(robots_url, netloc)
                    self._memory[netloc] = rp

        allowed = rp.can_fetch(USER_AGENT, url)
        if not allowed:
            log.warning("robots.txt disallows fetching %s", url)

        return allowed

    async def ensure_allowed(
        self, url: "str | urllib.parse.ParseResult | aiohttp.typedefs.StrOrURL"
    ):
        if not await self._is_allowed(url):
            raise CheckerFetchError(f"Blocked by robots.txt: {url}")
