import dataclasses
import html
import re
from urllib.parse import quote, unquote, urlsplit

import semver

from ..checkers.electronchecker import ElectronChecker
from ..checkers.urlchecker import URLChecker, extract_version
from .externaldata import ExternalBase, ExternalGitRef, ExternalState
from .releaselinks import (
    archive_tag,
    comparison_url,
    download_comparison_url,
    download_release_url,
    http_url,
)


def _electron_previous_version(old_url: str, new: ExternalState) -> str | None:
    version = new.version
    if not version or not semver.Version.is_valid(version):
        return None
    try:
        old_url_parts = urlsplit(old_url)
        new_url_parts = urlsplit(new.url)
    except ValueError:
        return None
    if (
        new_url_parts.scheme not in ("http", "https")
        or not new_url_parts.netloc
        or old_url_parts._replace(path="") != new_url_parts._replace(path="")
    ):
        return None
    old_dir, _, old_name = old_url_parts.path.rpartition("/")
    new_dir, _, new_name = new_url_parts.path.rpartition("/")
    if old_dir != new_dir or new_name.count(version) != 1:
        return None
    prefix, _, suffix = new_name.partition(version)
    if not re.fullmatch(r"(?:.*[-_])?v?", prefix) or (
        suffix and not re.match(r"(?:[-_]|\.[A-Za-z])", suffix)
    ):
        return None
    if not old_name.startswith(prefix) or not old_name.endswith(suffix):
        return None
    candidate = old_name[len(prefix) : len(old_name) - len(suffix)]
    return candidate if semver.Version.is_valid(candidate) else None


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
    if (
        not version
        and data.checker_data.get("type") == ElectronChecker.CHECKER_DATA_TYPE
        and state is data.current_version
        and data.new_version is not None
    ):
        version = _electron_previous_version(state.url, data.new_version)
    if not version:
        release_url = download_release_url(state.url)
        if release_url:
            return f"tag {unquote(release_url.rsplit('/', 1)[1])}"
        tag = archive_tag(state.url)
        if tag:
            return f"tag {tag}"
    return version or "unknown"


@dataclasses.dataclass(frozen=True)
class SourceUpdate:
    source: str
    old_version: str
    new_version: str
    release_url: str | None
    comparison_url: str | None = None

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
        old = data.current_version
        compare = None
        if isinstance(old, ExternalGitRef) and isinstance(new, ExternalGitRef):
            if old.url == new.url:
                if old.tag and new.tag and old.tag != new.tag:
                    compare = comparison_url(old.url, old.tag, new.tag)
                elif old.commit and new.commit:
                    compare = comparison_url(old.url, old.commit, new.commit)
        else:
            compare = download_comparison_url(old.url, new.url)
        return cls(
            source,
            _version(data, data.current_version),
            _version(data, new),
            release_url,
            compare,
        )


def _escape(text: str) -> str:
    text = html.escape(" ".join(text.split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)


def _url(url: str) -> str:
    return quote(url, safe=":/?#@!$&'*+,;=%~.-_")


def _code(text: str) -> str:
    escaped = _escape(text)
    return f"<code>{escaped}</code>"


def format_source_updates(updates: list[SourceUpdate]) -> str:
    if not updates:
        return ""
    lines = [
        "### Source updates",
        "",
        "| Source | Change | Release notes |",
        "| --- | --- | --- |",
    ]
    for update in updates:
        previous = _code(update.old_version)
        updated = _code(update.new_version)
        change = f"{previous} → {updated}"
        if update.comparison_url:
            url = _url(update.comparison_url)
            change = f"[{change}]({url})"
        source = _escape(update.source)
        release = (
            f"[Release notes]({_url(update.release_url)})"
            if update.release_url
            else "Unavailable"
        )
        lines.append(f"| {source} | {change} | {release} |")
    return "\n".join(lines)
