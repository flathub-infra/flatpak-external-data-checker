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
from functools import lru_cache
import operator
from distutils.version import StrictVersion, LooseVersion

from collections import OrderedDict
from ruamel.yaml import YAML
from elftools.elf.elffile import ELFFile
import aiohttp

from . import externaldata

import gi

gi.require_version("Json", "1.0")
from gi.repository import GLib, Json  # noqa: E402

log = logging.getLogger(__name__)

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = (
    "flatpak-external-data-checker "
    "(+https://github.com/flathub/flatpak-external-data-checker)"
)
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT_SECONDS = 60
HTTP_CHUNK_SIZE = 1024 * 64

OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def _extract_timestamp(info):
    date_str = info.get("Last-Modified") or info.get("Date")
    if date_str:
        try:
            return dt.datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
        except ValueError:
            return dt.datetime.strptime(date_str, "%a, %d-%b-%Y %H:%M:%S %Z")
    else:
        return dt.datetime.now()  # what else can we do?


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


def get_timestamp_from_url(url):
    request = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return _extract_timestamp(response.info())


async def get_extra_data_info_from_url(
    url: str,
    follow_redirects: bool = True,
    session: t.Optional[aiohttp.ClientSession] = None,
    dest_io: t.Optional[t.IO] = None,
):
    if session is None:
        private_session = True
        session = aiohttp.ClientSession(raise_for_status=True)
    else:
        private_session = False

    async with session.get(
        url,
        headers=HEADERS,
        timeout=aiohttp.ClientTimeout(connect=TIMEOUT_SECONDS),
        # auto_decompress=False,
    ) as response:
        real_url = str(response.url)
        info = response.headers
        checksum = hashlib.sha256()
        size = 0
        async for chunk in response.content.iter_chunked(HTTP_CHUNK_SIZE):
            checksum.update(chunk)
            size += len(chunk)
            if dest_io is not None:
                dest_io.write(chunk)

    if private_session:
        session.close()

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
    bwrap_cmd = ["bwrap", "--unshare-all"]
    for path in ("/usr", "/lib", "/lib64", "/bin", "/proc"):
        bwrap_cmd.extend(["--ro-bind", path, path])
    if bwrap_args is not None:
        bwrap_cmd.extend(bwrap_args)
    return bwrap_cmd + ["--"] + cmdline


def run_command(argv, cwd=None, env=None, bwrap=True, bwrap_args=None):
    if bwrap:
        command = wrap_in_bwrap(argv, bwrap_args)
    else:
        command = argv
    if env is None:
        env = os.environ
    p = subprocess.run(
        command, cwd=cwd, env=env, stderr=subprocess.PIPE, encoding="utf-8"
    )
    return p


def check_bwrap():
    try:
        p = run_command(["/bin/true"])
        if p.returncode == 0:
            return True
    except FileNotFoundError:
        pass

    logging.warning("bwrap is not available")
    return False


@lru_cache()
def git_ls_remote(url: str) -> t.Dict[str, str]:
    git_cmd = ["git", "ls-remote", "--exit-code", url]
    if check_bwrap():
        git_cmd = wrap_in_bwrap(
            git_cmd,
            bwrap_args=[
                # fmt: off
                "--share-net",
                "--dev", "/dev",
                "--ro-bind", "/etc/ssl", "/etc/ssl",
                "--ro-bind-try", "/etc/pki", "/etc/pki",
                "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
                # fmt: on
            ],
        )
    git_proc = subprocess.run(
        git_cmd,
        check=True,
        stdout=subprocess.PIPE,
        env=clear_env(os.environ),
        timeout=5,
    )
    git_stdout = git_proc.stdout.decode()

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

        bwrap = check_bwrap()
        bwrap_args = [
            "--bind",
            tmpdir,
            tmpdir,
            "--die-with-parent",
            "--new-session",
        ]

        args = ["unsquashfs", "-no-progress", appimage_path]

        log.debug("$ %s", " ".join(args))
        p = run_command(
            args,
            cwd=tmpdir,
            env=clear_env(os.environ),
            bwrap=bwrap,
            bwrap_args=bwrap_args,
        )

        if p.returncode != 0:
            log.error("AppImage extraction failed\n%s", p.stderr)
            p.check_returncode()

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
    parser.load_from_file(manifest_path)
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
    logging.basicConfig(
        level=level, format="+ %(asctime)s %(levelname)7s %(name)s: %(message)s"
    )
