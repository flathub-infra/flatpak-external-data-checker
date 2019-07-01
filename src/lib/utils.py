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

import glob
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import urllib.request

from gi.repository import GLib

log = logging.getLogger(__name__)

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = 'flatpak-external-data-checker (+https://github.com/endlessm/flatpak-external-data-checker)'  # noqa: E501
HEADERS = {'User-Agent': USER_AGENT}


def get_extra_data_info_from_url(url):
    request = urllib.request.Request(url, headers=HEADERS)
    data = None
    checksum = ''
    size = -1
    real_url = None

    with urllib.request.urlopen(request) as response:
        real_url = response.geturl()
        data = response.read()
        size = int(response.info().get('Content-Length', -1))

    if size == -1:
        size = len(data)

    checksum = hashlib.sha256(data).hexdigest()

    return real_url, data, checksum, size


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
        args.extend([
            "--bind", tmpdir, tmpdir,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            appimage_path,
            "--appimage-extract"
        ])
        log.debug('$ %s', ' '.join(args))

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
