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

import datetime as dt
import glob
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
import urllib.parse
import copy
import typing as t
from distutils.version import StrictVersion, LooseVersion
import asyncio
import shlex

from collections import OrderedDict
from ruamel.yaml import YAML
from elftools.elf.elffile import ELFFile
import aiohttp

from . import externaldata, TIMEOUT_CONNECT, HTTP_CHUNK_SIZE, OPERATORS
from .errors import CheckerRemoteError, CheckerQueryError

import gi

gi.require_version("Json", "1.0")
from gi.repository import GLib, Json  # noqa: E402

log = logging.getLogger(__name__)


def _extract_timestamp(info):
    date_str = info.get("Last-Modified") or info.get("Date")
    if not date_str:
        return dt.datetime.now()  # what else can we do?
    for date_fmt in [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d-%b-%Y %H:%M:%S %Z",
        "%a, %d-%b-%Y %H:%M:%S %z",
    ]:
        try:
            return dt.datetime.strptime(date_str, date_fmt)
        except ValueError:
            continue
    raise CheckerRemoteError(f"Cannot parse date/time: {date_str}")


def strip_query(url):
    """Sanitizes the query string from the given URL, if any. Parameters whose
    names begin with an underscore are assumed to be tracking identifiers and
    are removed."""
    parts = urllib.parse.urlparse(url)
    if not parts.query:
        return url
    qsl = urllib.parse.parse_qsl(parts.query)
    qsl_stripped = [(k, v) for (k, v) in qsl if not k.startswith("_")]
    query_stripped = urllib.parse.urlencode(qsl_stripped)
    stripped = urllib.parse.urlunparse(parts._replace(query=query_stripped))
    log.debug("Normalised %s to %s", url, stripped)
    return stripped


async def get_timestamp_from_url(url: str, session: aiohttp.ClientSession):
    async with session.head(url, allow_redirects=True) as response:
        return _extract_timestamp(response.headers)


async def get_extra_data_info_from_url(
    url: str,
    session: aiohttp.ClientSession,
    follow_redirects: bool = True,
    dest_io: t.Optional[t.IO] = None,
):
    async with aiohttp.ClientSession(
        connector=session.connector,
        connector_owner=False,
        cookie_jar=session.cookie_jar,
        trace_configs=session.trace_configs,
        timeout=session.timeout,
        headers=session.headers,
        raise_for_status=True,
        auto_decompress=False,
    ) as new_session:
        async with new_session.get(url) as response:
            real_url = str(response.url)
            info = response.headers
            checksum = hashlib.sha256()
            size = 0
            async for chunk in response.content.iter_chunked(HTTP_CHUNK_SIZE):
                checksum.update(chunk)
                size += len(chunk)
                if dest_io is not None:
                    dest_io.write(chunk)

    external_file = externaldata.ExternalFile(
        strip_query(real_url if follow_redirects else url),
        checksum.hexdigest(),
        size,
        None,
        _extract_timestamp(info),
    )

    return external_file


def filter_versions(
    versions: t.Iterable,
    constraints: t.List[t.Tuple[str, str]],
    to_string: t.Callable[..., str] = str,
    sort=False,
):
    new_versions = []
    for version in versions:
        version_str = to_string(version)
        matches = []
        for oper_str, version_limit in constraints:
            oper = OPERATORS[oper_str]
            try:
                match = oper(StrictVersion(version_str), StrictVersion(version_limit))
            except ValueError:
                match = oper(LooseVersion(version_str), LooseVersion(version_limit))
            matches.append(match)
        if all(matches):
            new_versions.append(version)

    if sort:
        return sorted(new_versions, key=lambda v: LooseVersion(to_string(v)))

    return new_versions


def clear_env(environ):
    new_env = copy.deepcopy(environ)
    for varname in new_env.keys():
        if any(i in varname.lower() for i in ["pass", "token", "secret", "auth"]):
            log.debug("Removing env %s", varname)
            new_env.pop(varname)
    return new_env


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
        def bwrap_args(self) -> t.List[str]:
            prefix = "ro-" if self.readonly else ""
            suffix = "-try" if self.optional else ""
            return [f"--{prefix}bind{suffix}", self.path, self.path]

    argv: t.List[str]
    cwd: str
    sandbox: bool

    def __init__(
        self,
        argv: t.List[str],
        cwd: t.Optional[str] = None,
        stdin: t.Optional[int] = subprocess.PIPE,
        stdout: t.Optional[int] = subprocess.PIPE,
        stderr: t.Optional[int] = None,
        timeout: t.Optional[float] = None,
        sandbox: t.Optional[bool] = None,
        allow_network: bool = False,
        allow_paths: t.Optional[t.List[t.Union[str, SandboxPath]]] = None,
    ):
        self.cwd = cwd or os.getcwd()
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
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

    async def run(self, input_data: t.Optional[bytes] = None) -> t.Tuple[bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
            env=clear_env(os.environ),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_data), self.timeout
            )
        except asyncio.TimeoutError as err:
            proc.kill()
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

    def run_sync(self, input_data: t.Optional[bytes] = None) -> t.Tuple[bytes, bytes]:
        proc = subprocess.run(
            self.argv,
            cwd=self.cwd,
            input=input_data,
            stdout=self.stdout,
            stderr=self.stderr,
            timeout=self.timeout,
            env=clear_env(os.environ),
            check=False,
        )
        proc.check_returncode()
        return proc.stdout, proc.stderr

    def __str__(self):
        return " ".join(shlex.quote(a) for a in self._orig_argv)


async def git_ls_remote(url: str) -> t.Dict[str, str]:
    git_cmd = Command(
        ["git", "ls-remote", "--exit-code", url],
        timeout=TIMEOUT_CONNECT,
        allow_network=True,
        allow_paths=[
            Command.SandboxPath("/etc/ssl", True, True),
            Command.SandboxPath("/etc/pki", True, True),
            Command.SandboxPath("/etc/resolv.conf", True, False),
        ],
    )
    try:
        git_stdout_raw, _ = await git_cmd.run()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise CheckerQueryError("Listing Git remote failed") from err
    git_stdout = git_stdout_raw.decode()

    return {r: c for c, r in (l.split() for l in git_stdout.splitlines())}


def extract_appimage_version(basename, appimg_io: t.IO):
    """
    Saves 'data' to a temporary file with the given basename, executes it (in a sandbox)
    with --appimage-extract to unpack it, and scrapes the version number out of the
    first .desktop file it finds.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        appimage_path = os.path.join(tmpdir, basename)

        header = ELFFile(appimg_io).header
        offset = header["e_shoff"] + header["e_shnum"] * header["e_shentsize"]
        appimg_io.seek(offset)

        log.info("Writing %s to %s with offset %i", basename, appimage_path, offset)

        with open(appimage_path, "wb") as fp:
            chunk = appimg_io.read(1024 ** 2)
            while chunk:
                fp.write(chunk)
                chunk = appimg_io.read(1024 ** 2)

        unsquashfs_cmd = Command(
            ["unsquashfs", "-no-progress", appimage_path],
            cwd=tmpdir,
            allow_paths=[tmpdir],
            stdout=None,
        )
        log.info("Running %s", unsquashfs_cmd)
        unsquashfs_cmd.run_sync()

        for desktop in glob.glob(os.path.join(tmpdir, "squashfs-root", "*.desktop")):
            kf = GLib.KeyFile()
            kf.load_from_file(desktop, GLib.KeyFileFlags.NONE)
            return kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "X-AppImage-Version")


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
    else:
        raise ValueError(f"{url!r} doesn't look like a Git URL")


def read_json_manifest(manifest_path):
    """Read manifest from 'manifest_path', which may contain C-style
    comments or multi-line strings (accepted by json-glib and hence
    flatpak-builder, but not Python's json module)."""

    # Round-trip through json-glib to get rid of comments, multi-line
    # strings, and any other invalid JSON
    parser = Json.Parser()
    try:
        parser.load_from_file(manifest_path)
    except GLib.Error as err:
        if err.matches(GLib.file_error_quark(), GLib.FileError.NOENT):
            raise FileNotFoundError(err.message) from err  # pylint: disable=no-member
        raise
    root = parser.get_root()
    clean_manifest = Json.to_string(root, False)

    return json.loads(clean_manifest, object_pairs_hook=OrderedDict)


_yaml = YAML()
# ruamel preserves some formatting (such as comments and blank lines) but
# not the indentation of the source file. These settings match the style
# recommended at <https://github.com/flathub/flathub/wiki/YAML-Style-Guide>.
_yaml.indent(mapping=2, sequence=4, offset=2)


def read_yaml_manifest(manifest_path):
    """Read a YAML manifest from 'manifest_path'."""
    with open(manifest_path, "r") as f:
        return _yaml.load(f)


def read_manifest(manifest_path):
    """Reads a JSON or YAML manifest from 'manifest_path'."""
    _, ext = os.path.splitext(manifest_path)
    if ext in (".yaml", ".yml"):
        return read_yaml_manifest(manifest_path)
    else:
        return read_json_manifest(manifest_path)


def dump_manifest(contents, manifest_path):
    """Writes back 'contents' to 'manifest_path'.

    For YAML, we make a best-effort attempt to preserve
    formatting; for JSON, we use the canonical 4-space indentation."""
    _, ext = os.path.splitext(manifest_path)
    with open(manifest_path, "w", encoding="utf-8") as fp:
        if ext in (".yaml", ".yml"):
            _yaml.dump(contents, fp)
        else:
            json.dump(obj=contents, fp=fp, indent=4)


def init_logging(level=logging.DEBUG):
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s")
    if level == logging.DEBUG:
        logging.getLogger("github.Requester").setLevel(logging.INFO)
