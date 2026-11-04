import dataclasses
import html
import re
from urllib.parse import quote, unquote

from ..checkers.urlchecker import URLChecker, extract_version
from .externaldata import ExternalBase, ExternalGitRef, ExternalState
from .releaselinks import download_release_url, http_url


def _version(data: ExternalBase, state: ExternalState) -> str:
    if isinstance(state, ExternalGitRef):
        parts = []
        if state.version:
            parts.append(state.version)
        elif state.tag:
            parts.append(f"tag {state.tag}")
        if state.commit:
            parts.append(f"commit {state.commit[:12]}")
        return ", ".join(parts) or "unknown"
    version = state.version
    if not version and data.checker_data.get("type") == URLChecker.CHECKER_DATA_TYPE:
        version = extract_version(data.checker_data, state.url)
    if not version:
        release_url = download_release_url(state.url)
        if release_url:
            return f"tag {unquote(release_url.rsplit('/', 1)[1])}"
    return version or "unknown"


@dataclasses.dataclass(frozen=True)
class SourceUpdate:
    source: str
    old_version: str
    new_version: str
    release_url: str | None

    @classmethod
    def from_source(cls, data: ExternalBase) -> "SourceUpdate":
        new = data.new_version
        assert new is not None
        source = data.filename
        if data.module:
            if data.module.name == data.filename.removesuffix(".git"):
                source = data.module.name
            else:
                source = f"{data.module}: {data.filename}"
        # data.arches defaults to x86_64 even for unrestricted sources.
        arches = data.checker_data.get("arches") or data.source.get("only-arches")
        if arches:
            source += f" ({', '.join(arches)})"
        template = data.checker_data.get("release-url-template")
        release_url = None
        if template and ("$version" not in template or new.version):
            url = template.replace("$version", new.version or "")
            release_url = http_url(url)
        elif not template:
            release_url = http_url(data.new_release_url) or download_release_url(
                new.url
            )
        return cls(
            source,
            _version(data, data.current_version),
            _version(data, new),
            release_url,
        )


def _escape(text: str) -> str:
    text = html.escape(" ".join(text.split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)


def format_source_updates(updates: list[SourceUpdate]) -> str:
    if not updates:
        return ""
    lines = [
        "### Source updates",
        "",
        "| Source | Previous | Updated | Release notes |",
        "| --- | --- | --- | --- |",
    ]
    for update in updates:
        link = "Unavailable"
        if update.release_url:
            url = quote(update.release_url, safe=":/?#@!$&'*+,;=%~.-_")
            link = f"[Release notes]({url})"
        lines.append(
            f"| {_escape(update.source)} | {_escape(update.old_version)} "
            f"| {_escape(update.new_version)} | {link} |"
        )
    return "\n".join(lines)
