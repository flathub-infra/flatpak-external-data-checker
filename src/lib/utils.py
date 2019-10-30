# Copyright © 2018–2019 Endless Mobile, Inc.
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
import logging
import os
import re
import socket
import subprocess
import tempfile
import urllib.request

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)
from .externaldata import ExternalFile

from gi.repository import GLib

log = logging.getLogger(__name__)

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = (
    "flatpak-external-data-checker "
    "(+https://github.com/endlessm/flatpak-external-data-checker)"
)
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT_SECONDS = 60


def _extract_timestamp(info):
    date_str = info["Last-Modified"] or info["Date"]
    if date_str:
        return dt.datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
    else:
        return dt.datetime.now()  # what else can we do?


def get_timestamp_from_url(url):
    request = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return _extract_timestamp(response.info())


@retry(
    retry=retry_if_exception_type((ConnectionResetError, socket.timeout)),
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    before_sleep=before_sleep_log(log, logging.DEBUG),
)
def get_extra_data_info_from_url(url):
    request = urllib.request.Request(url, headers=HEADERS)
    data = None
    checksum = ""
    size = -1
    real_url = None

    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        real_url = response.geturl()
        data = response.read()
        info = response.info()

    if "Content-Length" in info:
        size = int(info["Content-Length"])
    else:
        size = len(data)

    checksum = hashlib.sha256(data).hexdigest()
    external_file = ExternalFile(
        real_url, checksum, size, None, _extract_timestamp(info)
    )

    return external_file, data


def extract_appimage_version(basename, data):
    """
    Saves 'data' to a temporary file with the given basename, executes it (in a sandbox)
    with --appimage-extract to unpack it, and scrapes the version number out of the
    first .desktop file it finds.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        appimage_path = os.path.join(tmpdir, basename)
        with open(appimage_path, "wb") as fp:
            fp.write(data)

        os.chmod(appimage_path, 0o755)
        args = ["bwrap"]
        for path in ("/usr", "/lib", "/lib64", "/bin", "/proc"):
            args.extend(["--ro-bind", path, path])
        args.extend(
            [
                "--bind",
                tmpdir,
                tmpdir,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                appimage_path,
                "--appimage-extract",
            ]
        )
        log.debug("$ %s", " ".join(args))

        p = subprocess.run(args, cwd=tmpdir, stderr=subprocess.PIPE, encoding="utf-8")
        if p.returncode != 0:
            log.error("--appimage-extract failed\n%s", p.stderr)
            p.check_returncode()

        for desktop in glob.glob(os.path.join(tmpdir, "squashfs-root", "*.desktop")):
            kf = GLib.KeyFile()
            kf.load_from_file(desktop, GLib.KeyFileFlags.NONE)
            return kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "X-AppImage-Version")


_GITHUB_URL_PATTERN = re.compile(
    r"^(?:git@github.com:|https://github.com/)"
    r"(?P<org_repo>[^/]+/[^/]+?)"
    r"(?:\.git)?$"
)


def parse_github_url(url):
    """
    Parses the organization/repo part out of a git remote URL.
    """
    m = _GITHUB_URL_PATTERN.match(url)
    if m:
        return m.group("org_repo")
    else:
        raise ValueError("{!r} doesn't look like a Git URL")
