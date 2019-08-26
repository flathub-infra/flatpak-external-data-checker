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

from io import StringIO
from xml.sax import make_parser
from xml.sax.handler import property_lexical_handler
from xml.sax.saxutils import XMLFilterBase, XMLGenerator


class AddVersionFilter(XMLFilterBase):
    def __init__(self, version, date, parent=None):
        super().__init__(parent)

        self._version = version
        self._date = date
        self._context = []
        self._emitted_release = False
        self._releases_padding = ""

    @property
    def outside_root_element(self):
        return not self._context

    @property
    def _in_releases(self):
        return self._context[1:] == ["releases"]

    def _emit_release(self):
        super().startElement("release", {"version": self._version, "date": self._date})
        # TODO: add placeholder <description> if other <release> entries have one
        super().endElement("release")
        super().characters(self._releases_padding)
        self._emitted_release = True

    def startElement(self, name, attrs):
        if self._in_releases and not self._emitted_release:
            self._emit_release()

        super().startElement(name, attrs)

        self._context.append(name)

    def characters(self, chars):
        if self._in_releases and not self._emitted_release and chars.isspace():
            self._releases_padding += chars

        super().characters(chars)

    def endElement(self, name):
        if self._in_releases and not self._emitted_release:
            super().characters("\n    ")
            self._emit_release()
            super().characters("\n  ")

        self._context.pop()

        if not self._context and not self._emitted_release:
            # No <releases> found; synthesize one
            super().characters("  ")
            super().startElement("releases", {})
            super().characters("\n    ")
            self._emit_release()
            super().characters("\n  ")
            super().endElement("releases")
            super().characters("\n")

        super().endElement(name)


class VerbatimLexicalHandler:
    """Implements the poorly-documented LexicalHandler interface."""

    def __init__(self, reader, stream):
        self._reader = reader
        self._stream = stream

    def comment(self, text):
        self._stream.write("<!--")
        self._stream.write(text)
        self._stream.write("-->")

        # Aggravating hack alert!
        #
        # appdata files often include a copyright comment between the <?xml
        # declaration and the root element. In some cases, there are two.
        # Unfortunately, there is no representation in the SAX API for
        # whitespace that occurs outside any element, and there's normally
        # a newline between the comment and the opening element (or the next
        # comment).
        #
        # Work around this by writing a newline in this case; this requires some
        # coordination between the reader and this writer.
        if self._reader.outside_root_element:
            self._stream.write("\n")

    def startCDATA(self, *args):
        raise NotImplementedError

    def endCDATA(self, *args):
        raise NotImplementedError

    def endDTD(self, *args):
        raise NotImplementedError


def add_release(in_path_or_stream, out, version, date):
    reader = AddVersionFilter(version, date, make_parser())
    lexical_handler = VerbatimLexicalHandler(reader, out)
    handler = XMLGenerator(out, encoding="UTF-8", short_empty_elements=True)
    reader.setContentHandler(handler)
    reader.setProperty(property_lexical_handler, lexical_handler)
    reader.parse(in_path_or_stream)


def add_release_to_file(appdata_path, version, date):
    with StringIO() as buf:
        add_release(appdata_path, buf, version, date)

        with open(appdata_path, "w") as f:
            xml = buf.getvalue()
            f.write(xml)
            if not xml.endswith("\n"):
                f.write("\n")
