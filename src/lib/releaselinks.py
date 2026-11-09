import re
from collections.abc import Mapping
from urllib.parse import quote, unquote, urlsplit


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


def github_release_api_url(repository_url: str, tag: object) -> str | None:
    if not isinstance(tag, str) or not tag or not http_url(repository_url):
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


def archive_tag(url: str) -> str | None:
    """Read an explicitly named tag, without guessing from an ambiguous Git ref."""
    if not http_url(url):
        return None
    parsed = urlsplit(url)
    if parsed.netloc.lower() != "github.com":
        return None
    match = re.fullmatch(
        r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/archive/refs/tags/(.+)\.(?:tar\.gz|zip)",
        parsed.path,
    )
    if not match:
        return None
    tag = unquote(match[1])
    if any(part in (".", "..") for part in tag.split("/")):
        return None
    return tag


def comparison_url(repository_url: str, old: object, new: object) -> str | None:
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    if not old or not new or old == new or not http_url(repository_url):
        return None
    parsed = urlsplit(repository_url)
    host = parsed.netloc.lower()
    project = parsed.path.rstrip("/").removesuffix(".git")
    if host in ("github.com", "codeberg.org"):
        pattern = r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
        route = "/compare/"
    elif host == "gitlab.com":
        pattern = r"/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
        route = "/-/compare/"
    else:
        return None
    if not re.fullmatch(pattern, project):
        return None
    return (
        f"https://{host}{project}{route}{quote(old, safe='')}...{quote(new, safe='')}"
    )


def download_comparison_url(old_url: str, new_url: str) -> str | None:
    old_release = download_release_url(old_url)
    new_release = download_release_url(new_url)
    if not old_release or not new_release:
        return None
    separator = (
        "/-/releases/"
        if urlsplit(old_release).hostname == "gitlab.com"
        else "/releases/tag/"
    )
    old_repo, old_tag = old_release.rsplit(separator, 1)
    separator = (
        "/-/releases/"
        if urlsplit(new_release).hostname == "gitlab.com"
        else "/releases/tag/"
    )
    new_repo, new_tag = new_release.rsplit(separator, 1)
    if old_repo != new_repo:
        return None
    return comparison_url(old_repo, unquote(old_tag), unquote(new_tag))


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
            not repository_url
            or _belongs_to_repository(link, repository_url, forgejo=bool(forgejo))
        ):
            links.add(link)
    return next(iter(links)) if len(links) == 1 else None


def _asset_urls(release: Mapping[str, object]) -> list[object]:
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


def forgejo_repository_path(path: str) -> str:
    """Normalize owner/repository casing without changing the deployment path."""
    parts = path.rsplit("/", 2)
    if len(parts) != 3:
        return path
    prefix, owner, repo = parts
    return f"{prefix}/{owner.lower()}/{repo.lower()}"


def _belongs_to_repository(
    link: str, repository_url: str, *, forgejo: bool = False
) -> bool:
    if not http_url(repository_url):
        return False
    repository = urlsplit(repository_url)
    release = urlsplit(link)
    project = repository.path.rstrip("/").removesuffix(".git")
    # Separate the repository from the release route, which may contain tag slashes.
    depth = project.count("/") + 1
    parts = release.path.split("/", depth)
    if len(parts) != depth + 1:
        return False
    release_project = "/".join(parts[:-1])
    route = parts[-1]
    if forgejo:
        if depth < 3:
            return False
        project = forgejo_repository_path(project)
        release_project = forgejo_repository_path(release_project)
    elif repository.hostname == release.hostname == "github.com":
        project = project.lower()
        release_project = release_project.lower()
    return (
        repository.netloc.lower() == release.netloc.lower()
        and release_project == project
        and (
            route.startswith("releases/tag/")
            or (not forgejo and route.startswith("-/releases/"))
        )
    )
