# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
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

import hashlib
import urllib.request

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = 'flatpak-external-data-checker (+https://github.com/joaquimrocha/flatpak-external-data-checker)'  # noqa: E501
HEADERS = {'User-Agent': USER_AGENT}


def get_url_contents(url):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request) as response:
        return response.read()

    return None


def check_url_reachable(url):
    request = urllib.request.Request(url, method='HEAD', headers=HEADERS)
    response = urllib.request.urlopen(request)
    return response


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

    return real_url, checksum, size
