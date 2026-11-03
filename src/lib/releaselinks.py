import re
from urllib.parse import quote, urlsplit


def http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            return value
    except ValueError:
        pass
    return None


def github_release_api_url(repository_url: str, tag: str) -> str | None:
    if not http_url(repository_url):
        return None
    parsed = urlsplit(repository_url)
    if parsed.netloc.lower() != "github.com":
        return None
    project = parsed.path.rstrip("/").removesuffix(".git")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", project):
        return None
    return f"https://api.github.com/repos{project}/releases/tags/{quote(tag, safe='')}"


def download_release_url(url: str) -> str | None:
    if not http_url(url):
        return None
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    if host in ("github.com", "codeberg.org"):
        match = re.fullmatch(
            r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases/download/([^/]+)/[^/]+",
            parsed.path,
        )
        if match:
            owner, repo, tag = match.groups()
            return f"https://{host}/{owner}/{repo}/releases/tag/{tag}"
    elif host == "gitlab.com":
        match = re.fullmatch(
            r"/((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)/-/releases/([^/]+)/downloads/.+",
            parsed.path,
        )
        if match:
            project, tag = match.groups()
            return f"https://gitlab.com/{project}/-/releases/{tag}"
    return None


def api_release_url(
    api_url: str,
    payload: object,
    *,
    tag: str | None,
    asset_url: str | None,
    repository_url: str | None = None,
) -> str | None:
    if not http_url(api_url):
        return None
    parsed = urlsplit(api_url)
    endpoint = r"/repos/[^/]+/[^/]+/releases(?:/latest|/tags/[^/]+|/\d+)?"
    github = parsed.netloc.lower() == "api.github.com" and re.fullmatch(
        endpoint, parsed.path
    )
    forgejo = re.fullmatch(r"(?:/[^/]+)*/api/v1" + endpoint, parsed.path)
    gitlab = re.fullmatch(
        r"(?:/[^/]+)*/api/v4/projects/[^/]+/releases(?:/permalink/latest|/[^/]+)?",
        parsed.path,
    )
    if not (github or forgejo or gitlab):
        return None
    releases = payload if isinstance(payload, list) else [payload]
    links = set()
    for release in releases:
        if not isinstance(release, dict):
            continue
        if asset_url:
            if asset_url not in _asset_urls(release):
                continue
        elif not tag or release.get("tag_name") != tag:
            continue
        if gitlab:
            release_links = release.get("_links")
            link = (
                http_url(release_links.get("self"))
                if isinstance(release_links, dict)
                else None
            )
        else:
            link = http_url(release.get("html_url"))
        if link and (
            not repository_url or _belongs_to_repository(link, repository_url)
        ):
            links.add(link)
    return next(iter(links)) if len(links) == 1 else None


def _asset_urls(release: dict) -> list[object]:
    urls = [release.get("tarball_url"), release.get("zipball_url")]
    assets = release.get("assets")
    if isinstance(assets, list):
        urls.extend(
            asset.get("browser_download_url")
            for asset in assets
            if isinstance(asset, dict)
        )
    elif isinstance(assets, dict):
        for key in ("sources", "links"):
            entries = assets.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    urls.extend((entry.get("url"), entry.get("direct_asset_url")))
    return urls


def _belongs_to_repository(link: str, repository_url: str) -> bool:
    if not http_url(repository_url):
        return False
    repository = urlsplit(repository_url)
    release = urlsplit(link)
    project = repository.path.rstrip("/").removesuffix(".git")
    release_path = release.path
    if repository.hostname == release.hostname == "github.com":
        project = project.lower()
        release_path = release_path.lower()
    return repository.netloc.lower() == release.netloc.lower() and any(
        release_path.startswith(project + suffix)
        for suffix in ("/releases/tag/", "/-/releases/")
    )
