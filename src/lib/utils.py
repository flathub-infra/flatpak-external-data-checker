# Copyright © 2018–2020 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
#       Will Thompson <wjt@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import asyncio
import datetime as dt
import io
import json
import logging
import math
import operator
import os
import re
import shlex
import subprocess
import tempfile
import typing as t
import urllib.parse
import urllib.request
import zoneinfo
from collections import OrderedDict
from pathlib import Path
from string import Template

import aiohttp
import apt_inst
import apt_pkg
import editorconfig
import gi
import magic
from elftools.elf.elffile import ELFFile
from packaging.version import Version
from ruamel.yaml import YAML
from ruamel.yaml.comments import TaggedScalar
from ruamel.yaml.constructor import RoundTripConstructor
from ruamel.yaml.emitter import RoundTripEmitter
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import HTTP_CHUNK_SIZE, NETWORK_ERRORS, OPERATORS, TIMEOUT_CONNECT, externaldata
from .checksums import MultiHash
from .errors import (
    CheckerFetchError,
    CheckerMetadataError,
    CheckerQueryError,
    CheckerRemoteError,
)
from .robots import RobotsCache
from .version import LooseVersion

gi.require_version("Json", "1.0")
from gi.repository import (  # noqa: E402
    GLib,
    Json,
)

T = t.TypeVar("T")

log = logging.getLogger(__name__)


def _extract_timestamp(info: t.Mapping[str, str]) -> dt.datetime:
    date_str = info.get("Last-Modified") or info.get("Date")
    return parse_date_header(date_str)


def parse_date_header(date_str: str | None) -> dt.datetime:
    """Parse a Last-Modified or Date header into a UTC-aware datetime.

    In addition to standard(ish) formats, non-standard formats where the
    timezone is a named zone rather than an offset are detected and handled.
    Empty headers default to now; malformed nonempty headers raise
    CheckerRemoteError.
    """
    if not date_str:
        return dt.datetime.now(tz=dt.timezone.utc)  # what else can we do?

    for tz in zoneinfo.available_timezones():
        if not date_str.endswith(f" {tz}"):
            continue

        date_str_notz = date_str[: -(len(tz) + 1)]
        for date_fmt in [
            "%a, %d %b %Y %H:%M:%S",
            "%a, %d-%b-%Y %H:%M:%S",
        ]:
            try:
                dt_obj = dt.datetime.strptime(date_str_notz, date_fmt).replace(
                    tzinfo=zoneinfo.ZoneInfo(tz)
                )
                return dt_obj.astimezone(dt.timezone.utc)
            except ValueError:
                continue

    for date_fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d-%b-%Y %H:%M:%S %z",
    ]:
        try:
            return dt.datetime.strptime(date_str, date_fmt).astimezone(dt.timezone.utc)
        except ValueError:
            continue
    raise CheckerRemoteError(f"Cannot parse date/time: {date_str}")


def _check_newline(fp):
    original_position = fp.tell()
    fp.seek(0, os.SEEK_END)
    fp.seek(fp.tell() - 1, os.SEEK_SET)
    last_char = fp.read()
    fp.seek(original_position, os.SEEK_SET)
    if last_char == "\n":
        return True
    return False


def _detect_json_flatpak_manifest_indent(text: str) -> int | str | None:
    def _indent_value(indent: str) -> int | str:
        return "\t" if "\t" in indent else len(indent)

    try:
        root = json.loads(text)
    except json.JSONDecodeError:
        return None

    lines = text.splitlines()

    if isinstance(root, list):
        for line in lines:
            stripped = line.lstrip(" \t")
            if stripped == "{":
                return _indent_value(line[: len(line) - len(stripped)])
        return None

    if isinstance(root, dict):
        if "app-id" in root:
            anchor_key = '"app-id"'
        elif "id" in root:
            anchor_key = '"id"'
        elif "modules" in root:
            anchor_key = '"modules"'
        elif "name" in root:
            anchor_key = '"name"'
        else:
            return None
        shallowest: tuple[int, str | int] | None = None
        for line in lines:
            stripped = line.lstrip(" \t")
            if not stripped or stripped == line:
                continue
            if stripped.startswith(anchor_key):
                indent = line[: len(line) - len(stripped)]
                depth = len(indent)
                if shallowest is None or depth < shallowest[0]:
                    shallowest = (depth, _indent_value(indent))
        if shallowest is not None:
            return shallowest[1]

    return None


def strip_query(url):
    """Sanitizes the query string from the given URL, if any. Parameters whose
    names begin with an underscore are assumed to be tracking identifiers and
    are removed."""
    parts = urllib.parse.urlparse(url)
    if not parts.query:
        return url
    qsl = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    qsl_stripped = [(k, v) for (k, v) in qsl if not k.startswith("_")]
    query_stripped = urllib.parse.urlencode(qsl_stripped)
    stripped = urllib.parse.urlunparse(parts._replace(query=query_stripped))
    log.debug("Normalised %s to %s", url, stripped)
    return stripped


async def get_timestamp_from_url(
    url: str,
    session: aiohttp.ClientSession,
    robots_cache: RobotsCache | None = None,
) -> dt.datetime:
    if robots_cache:
        await robots_cache.ensure_allowed(url)

    try:
        async with session.head(url, allow_redirects=True) as response:
            real_url = str(response.url)

            if robots_cache and real_url != url:
                await robots_cache.ensure_allowed(real_url)

            return _extract_timestamp(response.headers)
    except (asyncio.TimeoutError, *NETWORK_ERRORS) as err:
        raise CheckerQueryError(f"Failed to query timestamp for {url}") from err


def version_parts(version: str) -> dict[str, str]:
    version_list = LooseVersion.split_components(version)
    tmpl_vars = {"version": version}
    for i, version_part in enumerate(version_list):
        tmpl_vars[f"version{i}"] = version_part
        if i == 0:
            tmpl_vars["major"] = version_part
        elif i == 1:
            tmpl_vars["minor"] = version_part
        elif i == 2:
            tmpl_vars["patch"] = version_part
    return tmpl_vars


def substitute_template(template_string: str, variables: dict[str, t.Any]) -> str:
    try:
        return Template(template_string).substitute(**variables)
    except (KeyError, ValueError) as err:
        raise CheckerMetadataError("Error substituting template") from err


async def get_extra_data_info_from_url(
    url: str,
    session: aiohttp.ClientSession,
    follow_redirects: bool = True,
    dest_io: t.IO | None = None,
    content_type_deny: t.Iterable[re.Pattern] | None = None,
    robots_cache: RobotsCache | None = None,
    mirror_urls: t.Iterable[str]
    | t.Callable[[t.Sequence[str]], tuple[str, t.Iterable[str]]]
    | None = None,
    preserve_primary_url_on_mirror: bool = False,
    on_primary_url: t.Callable[[str], None] | None = None,
):
    """Download with fallback; a resolver maps primary URL history to a
    canonical primary URL and its mirrors after a failed primary download.
    """
    primary_url = url
    primary_urls = [url]
    track_primary_urls = callable(mirror_urls) or on_primary_url is not None

    def remember_primary_url(resolved_url: str) -> None:
        if primary_urls[-1] != resolved_url:
            primary_urls.append(resolved_url)
        if on_primary_url is not None:
            on_primary_url(resolved_url)

    candidates = [url]
    for attempt, candidate in enumerate(candidates):
        if dest_io is not None:
            dest_io.seek(0)
            dest_io.truncate()
        try:
            result = await _download_extra_data_info_from_url(
                candidate,
                session,
                follow_redirects,
                dest_io,
                content_type_deny,
                robots_cache,
                remember_primary_url if attempt == 0 and track_primary_urls else None,
            )
            if attempt > 0 and preserve_primary_url_on_mirror:
                result = result._replace(url=strip_query(primary_url))
            return result
        except NETWORK_ERRORS as err:
            if attempt == 0:
                if (
                    isinstance(err, aiohttp.ClientResponseError)
                    and err.request_info is not None
                ):
                    remember_primary_url(str(err.request_info.real_url))
                if callable(mirror_urls):
                    primary_url, mirrors = mirror_urls(primary_urls)
                else:
                    mirrors = mirror_urls or ()
                candidates.extend(mirrors)
            if attempt == len(candidates) - 1:
                raise


async def _download_extra_data_info_from_url(
    url: str,
    session: aiohttp.ClientSession,
    follow_redirects: bool,
    dest_io: t.IO | None,
    content_type_deny: t.Iterable[re.Pattern] | None,
    robots_cache: RobotsCache | None,
    on_response_url: t.Callable[[str], None] | None = None,
    redirect_count: int = 0,
):
    if on_response_url is not None:
        on_response_url(url)
    if robots_cache:
        await robots_cache.ensure_allowed(url)

    async with session.get(
        url,
        skip_auto_headers=[aiohttp.hdrs.ACCEPT_ENCODING],
        allow_redirects=on_response_url is None,
    ) as response:
        real_url = str(response.url)
        if on_response_url is not None:
            on_response_url(real_url)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location") or response.headers.get(
                    "URI"
                )
                if location:
                    if redirect_count >= 9:
                        raise aiohttp.TooManyRedirects(response.request_info, ())
                    target = urllib.parse.urljoin(real_url, location)
                    if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
                        raise aiohttp.InvalidURL(target)
                    response.release()
                    return await _download_extra_data_info_from_url(
                        target,
                        session,
                        follow_redirects,
                        dest_io,
                        content_type_deny,
                        robots_cache,
                        on_response_url,
                        redirect_count + 1,
                    )

        if robots_cache and real_url != url:
            await robots_cache.ensure_allowed(real_url)

        info = response.headers

        def content_type_rejected(content_type: str | None) -> bool:
            return (
                content_type is not None
                and content_type_deny is not None
                and any(r.match(content_type) for r in content_type_deny)
            )

        checksum = MultiHash()
        first_chunk = True
        size = 0
        async for chunk in response.content.iter_chunked(HTTP_CHUNK_SIZE):
            if first_chunk:
                first_chunk = False
                # determine content type from magic number since http header may be
                # wrong
                actual_content_type = magic.from_buffer(chunk, mime=True)
                if content_type_rejected(actual_content_type):
                    if real_url != url:
                        suffix = f" (redirected from '{url}')"
                    else:
                        suffix = ""
                    raise CheckerFetchError(
                        f"Wrong content type '{actual_content_type}' received "
                        f"from '{real_url}'{suffix}"
                    )

            checksum.update(chunk)
            size += len(chunk)
            if dest_io is not None:
                dest_io.write(chunk)

        if dest_io is not None:
            dest_io.flush()

    external_file = externaldata.ExternalFile(
        url=strip_query(real_url if follow_redirects else url),
        checksum=checksum.hexdigest(),
        size=size,
        version=None,
        timestamp=_extract_timestamp(info),
    )

    return external_file


class VersionComparisonError(CheckerQueryError):
    def __init__(self, left, right):
        self.left = left
        self.right = right
        super().__init__(f"Can't compare {self.left} and {self.right}")


class FallbackVersion(t.NamedTuple):  # noqa: PLW1641
    s: str

    def __compare(self, oper: t.Callable[[t.Any, t.Any], bool], other) -> bool:
        if self.s is None or other.s is None:
            raise VersionComparisonError(self.s, other.s)
        try:
            return oper(Version(self.s), Version(other.s))
        except ValueError:
            try:
                return oper(LooseVersion(self.s), LooseVersion(other.s))
            except TypeError as err:
                raise VersionComparisonError(self.s, other.s) from err

    def __lt__(self, other) -> bool:
        return self.__compare(operator.lt, other)

    def __le__(self, other):
        return self.__compare(operator.le, other)

    def __gt__(self, other):
        return self.__compare(operator.gt, other)

    def __ge__(self, other):
        return self.__compare(operator.ge, other)

    def __eq__(self, other):
        return self.__compare(operator.eq, other)

    def __ne__(self, other):
        return self.__compare(operator.ne, other)


class _SupportsComparison(t.Protocol):
    def __lt__(self, other: t.Any, /) -> bool: ...


_VersionedObj = t.TypeVar("_VersionedObj")
_ComparableObj = t.TypeVar("_ComparableObj", bound=_SupportsComparison)


def filter_versioned_items(
    items: t.Iterable[_VersionedObj],
    constraints: t.Iterable[tuple[str, _ComparableObj]],
    to_version: t.Callable[[_VersionedObj], _ComparableObj],
    sort=False,
) -> list[_VersionedObj]:
    constraints = list(constraints)
    new_items = []
    for item in items:
        version = to_version(item)
        matches = []
        for oper_str, version_limit in constraints:
            oper = OPERATORS[oper_str]
            try:
                match = oper(version, version_limit)
            except VersionComparisonError as err:
                log.debug(err)
                match = False
            matches.append(match)
        if all(matches):
            new_items.append(item)

    if sort:
        return sorted(new_items, key=to_version)

    return new_items


def expand_version_constraints(
    versions_dict: dict[str, str | list[str]],
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []

    for op, val in versions_dict.items():
        if isinstance(val, list):
            if op != "!=":
                raise ValueError(f"Only '!=' supports multiple values (got '{op}')")
            result.extend((op, v) for v in val)
        else:
            result.append((op, val))

    return result


def filter_versions(
    versions: t.Iterable[str],
    constraints: t.Iterable[tuple[str, str]],
    sort=False,
) -> list[str]:
    return filter_versioned_items(
        versions,
        ((operator, FallbackVersion(version)) for operator, version in constraints),
        to_version=FallbackVersion,
        sort=sort,
    )


def wrap_in_bwrap(cmdline, bwrap_args=None):
    bwrap_cmd = ["bwrap", "--unshare-all", "--dev", "/dev"]
    for path in ("/usr", "/lib", "/lib64", "/bin", "/proc"):
        bwrap_cmd.extend(["--ro-bind", path, path])
    if bwrap_args is not None:
        bwrap_cmd.extend(bwrap_args)
    return bwrap_cmd + ["--"] + cmdline


def check_bwrap():
    try:
        subprocess.run(
            wrap_in_bwrap(["/bin/true"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
    except FileNotFoundError as err:
        log.debug("bwrap unavailable: %s", err)
        return False
    except subprocess.CalledProcessError as err:
        log.debug("bwrap unavailable: %s", err.output.strip())
        return False
    return True


class Command:
    class SandboxPath(t.NamedTuple):
        path: str
        readonly: bool = False
        optional: bool = False

        @property
        def bwrap_args(self) -> list[str]:
            prefix = "ro-" if self.readonly else ""
            suffix = "-try" if self.optional else ""
            return [f"--{prefix}bind{suffix}", self.path, self.path]

    argv: list[str]
    cwd: str
    sandbox: bool

    def __init__(
        self,
        argv: list[str],
        cwd: str | None = None,
        stdin: int | None = subprocess.PIPE,
        stdout: int | None = subprocess.PIPE,
        stderr: int | None = None,
        timeout: float | None = None,
        sandbox: bool | None = None,
        allow_network: bool = False,
        allow_paths: list[str | SandboxPath] | None = None,
        env: dict[str, str] | None = None,
    ):
        self.cwd = cwd or os.getcwd()
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.env = env
        # If sandbox not explicitly enabled or disabled, try to use it if available,
        # and proceed unsandboxed if sandbox is unavailable
        if sandbox is None:
            self.sandbox = check_bwrap()
        else:
            self.sandbox = sandbox
        if self.sandbox:
            bwrap_args = ["--die-with-parent"]
            if allow_network:
                bwrap_args.append("--share-net")
            if allow_paths:
                for path in allow_paths:
                    if isinstance(path, str):
                        mount = self.SandboxPath(path)
                    else:
                        mount = path
                    bwrap_args.extend(mount.bwrap_args)
            self.argv = wrap_in_bwrap(argv, bwrap_args)
        else:
            self.argv = argv
        self._orig_argv = argv

    async def run(self, input_data: bytes | None = None) -> tuple[bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
            env=self.env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_data), self.timeout
            )
        except asyncio.TimeoutError as err:
            try:
                proc.kill()
            except OSError as kill_err:
                log.warning("Failed to terminate timed out process: %s", kill_err)
            assert self.timeout is not None
            raise subprocess.TimeoutExpired(
                cmd=self.argv,
                timeout=self.timeout,
            ) from err
        if proc.returncode != 0:
            assert proc.returncode is not None
            raise subprocess.CalledProcessError(
                returncode=proc.returncode,
                cmd=self.argv,
                output=stdout,
                stderr=stderr,
            )
        return stdout, stderr

    def run_sync(self, input_data: bytes | None = None) -> tuple[bytes, bytes]:
        proc = subprocess.run(
            self.argv,
            cwd=self.cwd,
            input=input_data,
            stdout=self.stdout,
            stderr=self.stderr,
            timeout=self.timeout,
            env=self.env,
            check=False,
        )
        proc.check_returncode()
        return proc.stdout, proc.stderr

    def __str__(self):
        return " ".join(shlex.quote(a) for a in self._orig_argv)


async def git_ls_remote(url: str) -> dict[str, str]:
    git_cmd = Command(
        ["git", "ls-remote", "--exit-code", url],
        timeout=TIMEOUT_CONNECT,
        allow_network=True,
        allow_paths=[
            Command.SandboxPath("/etc/ssl", True, True),
            Command.SandboxPath("/etc/pki", True, True),
            Command.SandboxPath("/var/lib/ca-certificates", True, True),
            Command.SandboxPath("/etc/resolv.conf", True, False),
        ],
    )
    try:
        git_stdout_raw, _ = await git_cmd.run()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise CheckerQueryError("Listing Git remote failed") from err
    git_stdout = git_stdout_raw.decode()

    return {r: c for c, r in (line.split() for line in git_stdout.splitlines())}


async def extract_appimage_version(appimg_io: t.IO):
    """
    Saves 'data' to a temporary file with the given basename, executes it (in a sandbox)
    with --appimage-extract to unpack it, and scrapes the version number out of the
    first root .desktop file that provides X-AppImage-Version, if any.
    """
    assert appimg_io.name

    with tempfile.TemporaryDirectory() as tmpdir:
        header = ELFFile(appimg_io).header
        offset = header["e_shoff"] + header["e_shnum"] * header["e_shentsize"]

        unsquashfs_cmd = Command(
            ["unsquashfs", "-no-progress", "-offset", str(offset), appimg_io.name],
            cwd=tmpdir,
            allow_paths=[tmpdir, appimg_io.name],
            stdout=None,
        )
        log.info("Running %s", unsquashfs_cmd)
        await unsquashfs_cmd.run()

        for desktop in (Path(tmpdir) / "squashfs-root").glob("*.desktop"):
            kf = GLib.KeyFile()
            kf.load_from_file(str(desktop), GLib.KeyFileFlags.NONE)
            try:
                return kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "X-AppImage-Version")
            except GLib.Error as err:
                if not any(
                    err.matches(GLib.key_file_error_quark(), code)
                    for code in (
                        GLib.KeyFileError.KEY_NOT_FOUND,
                        GLib.KeyFileError.GROUP_NOT_FOUND,
                    )
                ):
                    raise


def extract_deb_version(deb_io: t.IO):
    assert deb_io.name
    control = apt_inst.DebFile(deb_io.name).control.extractdata("control")
    return apt_pkg.TagSection(control).get("Version")


_GITHUB_URL_PATTERN = re.compile(
    r"""
        ^git@github.com:
        (?P<org_repo>[^/]+/[^/]+?)
        (?:\.git)?$
    """,
    re.VERBOSE,
)


def parse_github_url(url):
    """
    Parses the organization/repo part out of a git remote URL.
    """
    if url.startswith("https:"):
        o = urllib.parse.urlparse(url)
        return o.path[1:]

    m = _GITHUB_URL_PATTERN.match(url)
    if m:
        return m.group("org_repo")
    raise ValueError(f"{url!r} doesn't look like a Git URL")


def read_json_manifest(manifest_path: Path):
    """Read manifest from 'manifest_path', which may contain C-style
    comments or multi-line strings (accepted by json-glib and hence
    flatpak-builder, but not Python's json module)."""

    parser = Json.Parser()
    try:
        parser.load_from_file(str(manifest_path))
    except GLib.Error as err:
        if err.matches(GLib.file_error_quark(), GLib.FileError.NOENT):
            raise FileNotFoundError(err.message) from err

        # If it's a parse/scanner error caused by legacy multi-line strings,
        # fallback to preprocessing. Otherwise, re-raise the GLib.Error
        # so test_non_noent_glib_error_is_reraised passes successfully.
        if (
            "parse error" in err.message.lower()
            or "unterminated string" in err.message.lower()
        ):
            with open(manifest_path, encoding="utf-8") as f:
                content = f.read()

            def escape_multiline_strings(match):
                s = match.group(0)
                return s.replace("\n", "\\n").replace("\r", "\\r")

            content = re.sub(r'"([^"\\]|\\.)*"', escape_multiline_strings, content)
            parser.load_from_data(content, len(content.encode("utf-8")))
        else:
            raise

    root = parser.get_root()
    if root is None:
        raise ValueError(f"Manifest is empty: {manifest_path}")
    clean_manifest = Json.to_string(root, False)

    return json.loads(clean_manifest, object_pairs_hook=OrderedDict)


class _ManifestEmitter(RoundTripEmitter):
    def write_plain(self, text, split=True):
        if self.root_context:
            if self.requested_indent is not None:
                self.write_line_break()
                if self.requested_indent != 0:
                    self.write_indent()
            else:
                self.open_ended = True
        if not text:
            return
        if not self.whitespace:
            data: t.Any = " "
            self.column += len(data)
            if self.encoding:
                data = data.encode(self.encoding)
            self.stream.write(data)
        self.whitespace = False
        self.indention = False
        spaces = False
        breaks = False
        start = end = 0
        while end <= len(text):
            ch = text[end] if end < len(text) else None
            if spaces:
                if ch != " ":
                    if start + 1 == end and self.column > self.best_width and split:
                        self.write_indent()
                        self.whitespace = False
                        self.indention = False
                    else:
                        data = text[start:end]
                        self.column += len(data)
                        if self.encoding:
                            data = data.encode(self.encoding)
                        self.stream.write(data)
                    start = end
            elif breaks:
                if ch is None or ch not in "\n\x85\u2028\u2029":
                    if text[start] == "\n":
                        self.write_line_break()
                    for br in text[start:end]:
                        self.write_line_break(None if br == "\n" else br)
                    self.write_indent()
                    self.whitespace = False
                    self.indention = False
                    start = end
            elif ch is None or ch in " \n\x85\u2028\u2029":
                data = text[start:end]
                self.column += len(data)
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                start = end
            if ch is not None:
                spaces = ch == " "
                breaks = ch in "\n\x85\u2028\u2029"
            end += 1


class _ManifestConstructor(RoundTripConstructor):
    def construct_yaml_timestamp(self, node, values=None):
        value = super().construct_yaml_timestamp(node, values)
        # Explicit timestamps can spell dates as 2024-7-2. Keep that spelling
        # available so Git revision validation can reject it instead of guessing.
        if (
            isinstance(value, dt.date)
            and not isinstance(value, dt.datetime)
            and node.value != value.isoformat()
        ):
            return TaggedScalar(value=node.value, style=node.style, tag=node.tag)
        return value


_ManifestConstructor.add_constructor(
    "tag:yaml.org,2002:timestamp", _ManifestConstructor.construct_yaml_timestamp
)


def _manifest_yaml():
    yaml = YAML()
    yaml.Constructor = _ManifestConstructor
    yaml.Emitter = _ManifestEmitter
    yaml.preserve_quotes = True
    # Default formatting for structural changes and newly added content.
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def read_yaml_manifest(manifest_path: Path):
    """Read a YAML manifest from 'manifest_path'."""
    with manifest_path.open("r") as f:
        has_yaml_header = (_ := f.read(3)) == "---"
        f.seek(0)
        data = _manifest_yaml().load(f)
    return data, has_yaml_header


def read_manifest(manifest_path: Path | str):
    """Reads a JSON or YAML manifest from 'manifest_path'."""
    manifest_path = Path(manifest_path)
    if manifest_path.suffix in (".yaml", ".yml"):
        return read_yaml_manifest(manifest_path)
    return read_json_manifest(manifest_path)


def _yaml_scalar_edits(original, rendered, *, reflow=False):
    """Locate scalar-only changes, declining structural changes and aliases."""
    before_yaml = _manifest_yaml()
    after_yaml = _manifest_yaml()
    before = before_yaml.compose(original)
    after = after_yaml.compose(rendered)
    edits = []
    seen = set()

    def scalar_equal(old, new):
        if old.tag != new.tag:
            return False
        if old.value == new.value:
            return True
        if old.tag not in {
            "tag:yaml.org,2002:bool",
            "tag:yaml.org,2002:null",
            "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:float",
            "tag:yaml.org,2002:timestamp",
        }:
            return False
        # Node values retain lexical spellings (e.g. True versus true).
        # Construct with each document's resolver version, retaining tag checks
        # so Python's True == 1 cannot hide an actual YAML type change.
        old_value = before_yaml.constructor.construct_object(old)
        new_value = after_yaml.constructor.construct_object(new)
        return old_value == new_value or (
            old.tag == "tag:yaml.org,2002:float"
            and math.isnan(old_value)
            and math.isnan(new_value)
        )

    def visit(old, new):
        if old is None or new is None or id(old) in seen or old.anchor or new.anchor:
            return False
        seen.add(id(old))
        if isinstance(old, ScalarNode) and isinstance(new, ScalarNode):
            if scalar_equal(old, new):
                if (
                    reflow
                    and old.style not in ("|", ">")
                    and (
                        old.start_mark.line != old.end_mark.line
                        or new.start_mark.line != new.end_mark.line
                    )
                ):
                    old_lines = original[old.start_mark.index : old.end_mark.index]
                    new_lines = rendered[new.start_mark.index : new.end_mark.index]
                    # Honor explicit width-induced wrapping, but don't normalize
                    # local indentation when the wrapping itself is unchanged.
                    if [line.strip() for line in old_lines.splitlines()] != [
                        line.strip() for line in new_lines.splitlines()
                    ]:
                        return False
                return True
            # Multiline scalars need context-sensitive indentation and chomping.
            if (
                old.start_mark.line != old.end_mark.line
                or new.start_mark.line != new.end_mark.line
                or old.style in ("|", ">")
                or new.style in ("|", ">")
            ):
                return False
            edits.append(
                (
                    old.start_mark.index,
                    old.end_mark.index,
                    rendered[new.start_mark.index : new.end_mark.index],
                )
            )
            return True
        if old.tag != new.tag or len(old.value) != len(new.value):
            return False
        if isinstance(old, MappingNode) and isinstance(new, MappingNode):
            for (old_key, old_value), (new_key, new_value) in zip(
                old.value, new.value, strict=True
            ):
                if not (
                    isinstance(old_key, ScalarNode)
                    and isinstance(new_key, ScalarNode)
                    and scalar_equal(old_key, new_key)
                    and visit(old_value, new_value)
                ):
                    return False
            return True
        if isinstance(old, SequenceNode) and isinstance(new, SequenceNode):
            return all(visit(a, b) for a, b in zip(old.value, new.value, strict=True))
        return False

    return edits if visit(before, after) else None


def _preserve_yaml_scalars(original, rendered, *, reflow=False):
    # A global ruamel indentation setting cannot reproduce local mixed layouts.
    # Edit only parser-delimited scalar spans, then verify the entire document.
    edits = _yaml_scalar_edits(original, rendered, reflow=reflow)
    if edits is None:
        return rendered
    result = original
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    try:
        if _yaml_scalar_edits(result, rendered) == []:
            return result
    except YAMLError:
        # A scalar emitted in block context may not be valid in flow context.
        pass
    return rendered


def dump_manifest(
    contents: dict, manifest_path: Path | str, has_yaml_header: bool = False
):
    manifest_path = Path(manifest_path)
    assert manifest_path.is_absolute()

    with manifest_path.open("r", encoding="utf-8") as fp:
        original_manifest = fp.read()

    conf = editorconfig.get_properties(str(manifest_path))

    json_indent: str | int
    if conf.get("indent_style") == "space":
        json_indent = int(conf.get("indent_size", 4))
    elif conf.get("indent_style") == "tab":
        json_indent = "\t"
    else:
        json_indent = _detect_json_flatpak_manifest_indent(original_manifest) or 4

    yaml_width = None
    if yaml_max_line_length := conf.get("max_line_length"):
        try:
            yaml_width = int(yaml_max_line_length)
        except ValueError:
            log.warning("Ignoring invalid max_line_length %r", yaml_max_line_length)

    json_newline_pref: bool | None
    if "insert_final_newline" in conf:
        json_newline_pref = {"true": True, "false": False}.get(
            conf["insert_final_newline"]
        )
    else:
        json_newline_pref = _check_newline(io.StringIO(original_manifest))

    with manifest_path.open("w", encoding="utf-8") as fp:
        if manifest_path.suffix in (".yaml", ".yml"):
            yaml = _manifest_yaml()
            # Retain this document's version directives without leaking them to
            # another manifest read or write.
            yaml.compose(original_manifest)
            yaml.width = yaml_width
            yaml.explicit_start = has_yaml_header
            stream = io.StringIO()
            yaml.dump(contents, stream)
            rendered = stream.getvalue()
            if has_yaml_header == original_manifest.startswith("---"):
                rendered = _preserve_yaml_scalars(
                    original_manifest, rendered, reflow=yaml_width is not None
                )
            fp.write(rendered)
        else:
            json.dump(obj=contents, fp=fp, indent=json_indent)
            if json_newline_pref:
                fp.write("\n")


def init_logging(level=logging.DEBUG):
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s")
    if level == logging.DEBUG:
        logging.getLogger("github.Requester").setLevel(logging.INFO)


async def asyncio_gather_failfast(
    awaitables: t.Iterable[t.Awaitable[T]],
) -> list[T]:
    tasks: list[asyncio.Task[T]] = [asyncio.ensure_future(a) for a in awaitables]
    if not tasks:
        return []
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    exc: BaseException | None = None
    for task in done:
        if task.cancelled():
            continue
        try:
            e = task.exception()
        except asyncio.CancelledError:
            continue
        if e is not None:
            exc = e
            break
    if exc:
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise exc
    return [task.result() for task in tasks]
