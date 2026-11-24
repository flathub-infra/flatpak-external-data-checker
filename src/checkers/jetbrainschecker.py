import datetime
import logging
import re

from lxml import etree, html

from ..lib.appdata import ReleaseDescription, XMLElement
from ..lib.checksums import MultiDigest
from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.releaselinks import http_url
from . import Checker

log = logging.getLogger(__name__)

_JB_ARCH_MAP = {
    "x86_64": "linux",
    "aarch64": "linuxARM64",
}

_MAX_WHATSNEW_LENGTH = 16_384
_DISCARD = {"hr", "img", "iframe", "script", "style", "video"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_VERSION = r"(?P<version>20\d\d(?:\.\d+)+)"
_VERSION_LABELS = (
    re.compile(
        rf".+? {_VERSION}(?: hotfix)? "
        r"(?:is (?:out|(?:now )?available)|release notes)\b",
        re.IGNORECASE,
    ),
    re.compile(rf"what['\u2019]s new in {_VERSION}\b", re.IGNORECASE),
)
_BOILERPLATE = re.compile(
    r"^(?:for (?:more details|details|the list of changes)\b"
    r"|(?:please )?(?:see|refer to|visit) (?:the |our )?release notes\b"
    r"|the full list of changes\b"
    r"|(?:discover|explore) .*what['\u2019]s new"
    r"|tweet us\b|report bugs\b)",
    re.IGNORECASE,
)
_NAVIGATION_LINK = re.compile(
    r"^(?:download\b|(?:read more|learn more|more information|release notes)$)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://", re.IGNORECASE)
_INVALID_XML_CHARACTERS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def _copy_inline(source: XMLElement, target: XMLElement) -> None:
    def add_text(text: str | None) -> None:
        if text:
            if len(target):
                last = target[-1]
                last.tail = (last.tail or "") + text
            else:
                target.text = (target.text or "") + text

    add_text(source.text)
    for child in source:
        if isinstance(child.tag, str) and child.tag not in _DISCARD:
            if child.tag in {"em", "i", "code"}:
                tag = "em" if child.tag == "i" else child.tag
                if target.tag in {"em", "code"}:
                    _copy_inline(child, target)
                else:
                    inline = etree.Element(tag)
                    _copy_inline(child, inline)
                    if inline.text and inline.text.strip():
                        target.append(inline)
                    else:
                        add_text(inline.text)
            elif child.tag in {"a", "b", "span", "strong"}:
                _copy_inline(child, target)
            elif child.tag == "br":
                add_text(" ")
            else:
                raise ValueError("Unsupported markup inside release notes prose")
        add_text(child.tail)


def _release_prose(source: XMLElement, tag: str, version: str) -> XMLElement | None:
    node = etree.Element(tag)
    _copy_inline(source, node)
    text = " ".join("".join(node.itertext()).split())
    for label in _VERSION_LABELS:
        match = label.match(text)
        if match is None:
            continue
        if match["version"] != version:
            raise ValueError("Release notes announce a different version")
        if not text[match.end() :].strip(" .!:"):
            return None
        break
    if not text or _BOILERPLATE.match(text):
        return None
    links = " ".join("".join(link.itertext()) for link in source.iter("a"))
    label_text = text.rstrip(" .!")
    if label_text == " ".join(links.split()).rstrip(" .!") and _NAVIGATION_LINK.match(
        label_text
    ):
        return None
    if _URL.search(text):
        raise ValueError("Bare URL in release notes")
    return node


def _release_description(notes: object, version: str) -> ReleaseDescription | None:
    if (
        not isinstance(notes, str)
        or len(notes) > _MAX_WHATSNEW_LENGTH
        or _INVALID_XML_CHARACTERS.search(notes)
    ):
        return None

    try:
        parser = html.HTMLParser(no_network=True)
        fragment = html.fragment_fromstring(notes, create_parent=True, parser=parser)
        if (
            parser.error_log.filter_from_level(etree.ErrorLevels.FATAL)
            or (fragment.text or "").strip()
        ):
            return None

        blocks = []
        has_paragraph_or_list = False
        for child in fragment:
            if (child.tail or "").strip():
                return None
            tag = child.tag
            if not isinstance(tag, str) or tag in _DISCARD:
                continue
            if tag in _HEADINGS or tag in {"p", "div", "strong", "b"}:
                if (block := _release_prose(child, "p", version)) is not None:
                    blocks.append(block)
                    has_paragraph_or_list |= tag in {"p", "div"}
            elif tag in {"ul", "ol"}:
                if (child.text or "").strip():
                    return None
                block = etree.Element(tag)
                for item in child:
                    if (item.tail or "").strip():
                        return None
                    if not isinstance(item.tag, str):
                        continue
                    if item.tag != "li":
                        return None
                    if (entry := _release_prose(item, "li", version)) is not None:
                        block.append(entry)
                if len(block):
                    blocks.append(block)
                    has_paragraph_or_list = True
            else:
                return None
    except (etree.LxmlError, ValueError):
        return None

    return tuple(blocks) if has_paragraph_or_list else None


class JetBrainsChecker(Checker):
    CHECKER_DATA_TYPE = "jetbrains"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "release-type": {
                "type": "string",
                "enum": ["release", "eap", "rc", "preview"],
            },
            "include-release-notes": {"type": "boolean"},
        },
        "required": ["code"],
    }

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        code = external_data.checker_data["code"]
        release_type = external_data.checker_data.get("release-type", "release")

        url = "https://data.services.jetbrains.com/products/releases"
        query = {"code": code, "latest": "true", "type": release_type}

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(url)

        async with self.session.get(url, params=query) as response:
            result = await response.json()
            data = result[code][0]

        release = data["downloads"][_JB_ARCH_MAP[external_data.arches[0]]]

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(release["checksumLink"])

        async with self.session.get(release["checksumLink"]) as response:
            result = await response.text()
            checksum = result.split(" ")[0]

        new_version = ExternalFile(
            url=release["link"],
            checksum=MultiDigest(sha256=checksum),
            size=release["size"],
            version=data["version"],
            timestamp=datetime.datetime.strptime(data["date"], "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            ),
        )

        external_data.set_new_version(new_version)
        external_data.new_release_url = (
            http_url(data.get("notesLink")) if external_data.new_version else None
        )
        external_data.new_release_description = (
            _release_description(data.get("whatsnew"), new_version.version)
            if release_type == "release"
            and external_data.checker_data.get("include-release-notes")
            and external_data.has_version_changed
            and new_version.version is not None
            else None
        )
