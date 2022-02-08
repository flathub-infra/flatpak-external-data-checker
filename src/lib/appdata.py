# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
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

"""
Add a new <release> at the start of the <releases> element in an appdata file,
preserving as much formatting as is feasible and inserting that element if it is
missing.
"""

import io
import sys
import typing as t

# pylint: disable=wrong-import-position
if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    from typing_extensions import TypeAlias
# pylint: enable=wrong-import-position

import lxml.etree as ElementTree


XMLElement: TypeAlias = ElementTree._Element  # pylint: disable=protected-access

DEFAULT_INDENT = "  "


def _fill_padding(ele: XMLElement):
    parent = ele.getparent()
    assert parent is not None
    index = parent.index(ele)
    level = sum(1 for _ in ele.iterancestors())
    if len(parent) > 1:
        if index == len(parent) - 1:
            if len(parent) > 2:
                ele.tail = parent[index - 1].tail
                parent[index - 1].tail = parent[index - 2].tail
            else:
                ele.tail = "\n" + DEFAULT_INDENT * (level - 1)
                parent[index - 1].tail = parent.text
        else:
            ele.tail = parent.text
    else:
        parent.text = "\n" + DEFAULT_INDENT * level
        ele.tail = "\n" + DEFAULT_INDENT * (level - 1)


def add_release(
    src: t.Union[t.IO, str],
    dst: t.Union[t.IO, str],
    version: str,
    date: str,
):
    parser = ElementTree.XMLParser(load_dtd=False, resolve_entities=False)
    tree = ElementTree.parse(src, parser=parser)
    root = tree.getroot()

    releases = root.find("releases")

    if releases is None:
        releases = ElementTree.Element("releases")
        root.append(releases)
        _fill_padding(releases)

    release = ElementTree.Element("release", version=version, date=date)
    releases.insert(0, release)
    _fill_padding(release)

    tree.write(
        dst,
        # XXX: lxml uses single quotes for doctype line if generated with
        # xml_declaration=True,
        doctype='<?xml version="1.0" encoding="UTF-8"?>',  # type: ignore[call-arg]
        encoding="utf-8",
        pretty_print=True,
    )


def add_release_to_file(appdata_path: str, version: str, date: str):
    with io.BytesIO() as buf:
        add_release(appdata_path, buf, version, date)

        with open(appdata_path, "wb") as f:
            f.write(buf.getvalue())
