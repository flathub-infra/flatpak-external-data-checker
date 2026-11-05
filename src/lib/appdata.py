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
import typing as t
from pathlib import Path
from typing import TypeAlias

import lxml.etree as ElementTree

XMLElement: TypeAlias = ElementTree._Element

DEFAULT_INDENT = "  "


def _fill_padding(ele: XMLElement, indent: str = DEFAULT_INDENT):
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
                ele.tail = "\n" + indent * (level - 1)
                parent[index - 1].tail = parent.text
        else:
            ele.tail = parent.text
    else:
        parent.text = "\n" + indent * level
        ele.tail = "\n" + indent * (level - 1)


def _trailing_whitespace(data: bytes | str, encoding: str) -> bytes:
    if isinstance(data, str):
        text = data
    else:
        # Without an XML declaration, docinfo may report UTF-8 despite a BOM.
        if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            encoding = "utf-32"
        elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        text = data.decode(encoding)
    return text[len(text.rstrip(" \t\r\n")) :].encode("utf-8")


def add_release(
    src: t.IO | str,
    dst: t.IO | str,
    version: str,
    date: str,
    release_url_template: str | None,
    release_url: str | None = None,
):
    data = Path(src).read_bytes() if isinstance(src, str) else src.read()

    parser = ElementTree.XMLParser(load_dtd=False, resolve_entities=False)
    source = io.StringIO(data) if isinstance(data, str) else io.BytesIO(data)
    tree = ElementTree.parse(source, parser=parser)
    trailing_whitespace = _trailing_whitespace(data, tree.docinfo.encoding)
    root = tree.getroot()

    releases = root.find("releases")

    if releases is None:
        releases = ElementTree.Element("releases")
        root.append(releases)
        _fill_padding(releases)

    # Use the indentation before <releases> when it has no children to copy.
    previous = releases.getprevious()
    padding = previous.tail if previous is not None else root.text
    indent = DEFAULT_INDENT
    if padding and padding.isspace():
        indent = padding.rsplit("\n", 1)[-1] or DEFAULT_INDENT

    release = ElementTree.Element("release", version=version, date=date)
    releases.insert(0, release)
    _fill_padding(release, indent)

    # Indent the opening <description> tag one level
    # deeper than the <release> tag.
    if releases.text:
        release.text = "\n" + ((releases.text[1::2]) * 3)

    if release_url_template:
        release_url = release_url_template.replace("$version", version)
    if release_url:
        rel_url_elem = ElementTree.SubElement(release, "url", type="details")
        rel_url_elem.text = release_url
        rel_url_elem.tail = release.text

    description = ElementTree.Element("description")

    # Give <description> a closing </description> rather than it being
    # self-closing
    description.text = ""

    # Indent the closing </release> tag by the same amount as the opening
    # <release> tag (which we know to be the first child of <releases> since
    # we just prepended it above)
    description.tail = releases.text
    release.append(description)

    output = ElementTree.tostring(
        tree,
        # XXX: lxml uses single quotes for doctype line if generated with
        # xml_declaration=True,
        doctype='<?xml version="1.0" encoding="UTF-8"?>',
        encoding="utf-8",
        pretty_print=True,
    )
    # Parsing discards document-level whitespace; restore it after pretty printing.
    output = output.rstrip(b" \t\r\n") + trailing_whitespace
    if isinstance(dst, str):
        Path(dst).write_bytes(output)
    else:
        dst.write(output)


def add_release_to_file(
    appdata_path: str,
    version: str,
    date: str,
    release_url_template: str | None,
    release_url: str | None = None,
):
    with io.BytesIO() as buf:
        add_release(appdata_path, buf, version, date, release_url_template, release_url)

        with open(appdata_path, "wb") as f:
            f.write(buf.getvalue())
