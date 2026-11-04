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

import datetime
import io
import logging
import typing as t
from pathlib import Path
from typing import TypeAlias

import lxml.etree as ElementTree

from .version import compare_appstream_versions

log = logging.getLogger(__name__)
XMLElement: TypeAlias = ElementTree._Element

DEFAULT_INDENT = "  "


def read_current_version(src: t.IO | str, app_id: str) -> str | None:
    parser = ElementTree.XMLParser(load_dtd=False, resolve_entities=False)
    root = ElementTree.parse(src, parser=parser).getroot()
    if root.findtext("id") not in (None, app_id):
        return None
    releases = root.find("releases")
    if releases is None or releases.get("type") == "external":
        return None
    release = releases.find("release")
    if release is None:
        return None
    return release.get("version") or None


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
) -> bool:
    data = Path(src).read_bytes() if isinstance(src, str) else src.read()

    parser = ElementTree.XMLParser(load_dtd=False, resolve_entities=False)
    source = io.StringIO(data) if isinstance(data, str) else io.BytesIO(data)
    tree = ElementTree.parse(source, parser=parser)
    trailing_whitespace = _trailing_whitespace(data, tree.docinfo.encoding)
    root = tree.getroot()

    releases = root.find("releases")

    if not version or (releases is not None and releases.get("type") == "external"):
        log.warning(
            "Not adding release %r: missing version or external history", version
        )
        return False
    try:
        release_date = datetime.date.fromisoformat(date)
    except ValueError:
        log.warning("Not adding release %s: invalid date %r", version, date)
        return False
    if releases is not None:
        for existing in releases.findall("release"):
            existing_version = existing.get("version")
            comparison = compare_appstream_versions(version, existing_version)
            if comparison is None or comparison <= 0:
                log.warning(
                    "Not adding release %s: existing version %r is not older",
                    version,
                    existing.get("version"),
                )
                return False
            existing_date = existing.get("date")
            existing_timestamp = existing.get("timestamp")
            try:
                if existing_date:
                    previous_date = datetime.date.fromisoformat(existing_date)
                elif existing_timestamp:
                    previous_date = datetime.datetime.fromtimestamp(
                        int(existing_timestamp), tz=datetime.timezone.utc
                    ).date()
                else:
                    continue
            except (ValueError, OverflowError, OSError):
                log.warning("Not adding release %s: invalid date in history", version)
                return False
            if release_date < previous_date:
                log.warning("Not adding release %s: date precedes history", version)
                return False

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
    return True


def add_release_to_file(
    appdata_path: str,
    version: str,
    date: str,
    release_url_template: str | None,
    release_url: str | None = None,
):
    with io.BytesIO() as buf:
        if not add_release(
            appdata_path, buf, version, date, release_url_template, release_url
        ):
            return

        with open(appdata_path, "wb") as f:
            f.write(buf.getvalue())
