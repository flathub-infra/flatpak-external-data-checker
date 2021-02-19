# Flatpak External Data Checker

![Tests](https://github.com/flathub/flatpak-external-data-checker/workflows/Tests/badge.svg)
[![Coverage Status](https://coveralls.io/repos/github/flathub/flatpak-external-data-checker/badge.svg)](https://coveralls.io/github/flathub/flatpak-external-data-checker)
[![Total alerts](https://img.shields.io/lgtm/alerts/g/flathub/flatpak-external-data-checker.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/flathub/flatpak-external-data-checker/alerts/)
[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/flathub/flatpak-external-data-checker.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/flathub/flatpak-external-data-checker/context:python)

This is a tool for checking for outdated or broken links of external
data in Flatpak manifests.

## Motivation

Flatpak apps are built using external data (git checkouts, tarballs,
simple files, etc.). A very specific case of this is the use of *extra
data*, which works as a way to download third party binaries at
installation time.

Of course, the links pointing to external data can become obsolete, so
it is very important to account for and correct such issues.
This is especially critical for the *extra data*, in which a broken link
impedes the installation of the application.

This project offers ways to easily check or monitor the state of such
links, as well as the suggestion of new versions.

It works by extracting all the external data of a Flatpak manifest and
giving it to a collection of *checkers*, which will set up the right
state and, possibly, new versions of each external data.

## Use

The simplest use of this tool is by calling:

```
flatpak-external-data-checker MANIFEST_FILE
```

it should display messages about any broken or outdated external data.

### Running in a container

**flatpak-external-data-checker** is avaiable as an
[OCI image](https://github.com/orgs/flathub/packages/container/package/flatpak-external-data-checker)
from GitHub Container Registry.
This is a convenient way to get all necessary dependencies regardless of your
host system's distribution.

You can use the `run-in-container.sh` helper script to set up needed CLI
options for you and run the image using `podman`:

```bash
~/src/endlessm/flatpak-external-data-checker/run-in-container.sh \
    [ARGS …] \
    ~/src/flathub/com.example.App/com.example.App.json
```

### Automatically submitting PRs

When run with the `--update` flag, this tool can commit any necessary changes
to Git and open a GitHub pull request. In order to do this, it requires
a [GitHub access token](https://help.github.com/en/articles/creating-a-personal-access-token-for-the-command-line),
specified in the `GITHUB_TOKEN` environment variable.

The tool will also automatically merge previously opened pull request for
unavailable (`BROKEN`) sources if the change has successfully passed CI checks
and the token has sufficient privileges. It can be disabled by setting
`automerge-flathubbot-prs` to `false` in `flathub.json`.

## Changes to Flatpak manifests

For simple checks to see if a URL is broken, no changes are needed.  However,
you can add additional metadata to the manifest to allow the checker to
discover new versions.

Some of the following checkers are able to determine upstream version number,
and automatically add it to releases list in metainfo. To specify which source
is the app upstream source, set property `is-main-source` to `true` in the
checker metadata for that source.

### URL checker

If the upstream vendor has an URL that redirects to the latest version of the
application, you can add something like the following to check and update the URL for
the latest version:

```json
"x-checker-data": {
    "type": "rotating-url",
    "url": "http://example.com/last-version",
    "pattern": "http://example.com/foo-v([0-9.]+).tar.gz"
}
```

The version number for the latest version can be detected in two ways:

* If the filename ends with `.AppImage`, the version number is extracted
  from the AppImage. (It is run in a `bwrap` sandbox.)
* Otherwise, if `"pattern"` is specified in `"x-checker-data"`, the given
  regular expression is matched against the full
  URL for the latest version, and the first match group is taken to be the
  version. (This follows the convention used by
  [`debian/watch`](https://wiki.debian.org/debian/watch) files.)

### HTML checker

Both the version number and the download URL will be gathered from a static
HTML page which contains this information:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\d\.-]*)",
    "url-pattern": "https://www.example.com/pub/foo/v(\d+)/foo.tar.gz"
}
```

The HTML checker also supports building the download URL using
the retrieved version:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\d\.-]*)",
    "url-template": "https://www.example.com/$version/v$version.tar.gz"
}
```

### Git checker ###

To check for latest git tag in corresponding git source repo, add checker
metadata with type `git` and set `tag-pattern` to a regular expression with 
exactly one match group (the pattern will be used to extract version from tag):

```json
"x-checker-data": {
    "type": "git",
    "tag-pattern": "^v([\\d.]+)$"
}
```

By default tags are sorted based on version number extracted from tag.
To disable sorting and keep order from `git ls-remote`, set `sort-tags` to `false`.


### JSON checker

The JSON checker allows using [jq](https://stedolan.github.io/jq/) to query
JSON data with arbitrary schema to get version and download url.

To use the **JSONChecker**, specify JSON data URL, version query and url query
(you can use `$version` variable got from the version query in url query):

```json
{
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "version-query": ".tag_name | sub(\"^jq-\"; \"\")",
    "url-query": ".assets[] | select(.name==\"jq-\" + $version + \".tar.gz\") | .browser_download_url"
}
```
for git type sources, specify tag query and, optionaly, commit and version queries:
```json
{
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "tag-query": ".tag_name",
    "version-query": "$tag | sub(\"^jq-\"; \"\")"
}
```

See the [jq manual](https://stedolan.github.io/jq/manual/) for complete information about writing queries.

### Debian repo checker

For the **DebianRepoChecker**, which deals only with deb packages, it
can read the following metadata (add it to manifest element it refers
to, e.g. where "type": "extra-data" is declared):

```json
"x-checker-data": {
    "type": "debian-repo",
    "package-name": "YOUR_PACKAGE_NAME",
    "root": "ROOT_URL_TO_THE_DEBIAN_REPO",
    "dist": "DEBIAN_DIST",
    "component": "DEBIAN_COMPONENT"
}
```

### Anitya (release-monitoring) checker

[Anitya](https://github.com/fedora-infra/anitya) is an upstream release monitoring 
project by Fedora. It supports multiple backends for retrieving version information 
from different services, including GitHub, GitLab, Sourceforge, etc.  
To use the **AnityaChecker**, specify numeric project ID on release-monitoring.org
and add a template for source download URL:

```json
"x-checker-data": {
    "type": "anitya",
    "project-id": 6377,
    "stable-only": false,
    "url-template": "https://github.com/flatpak/flatpak/releases/download/$version/flatpak-$version.tar.xz"
}
```
Set `stable-only` to `true` to retrieve latest stable version (as recognized by Anitya).

For git type sources, instead of `url-template`, set `tag-template` to derive git tag from version.

### PyPI checker ###

Check for Python package updates on PyPI.

```json
"x-checker-data": {
    "type": "pypi",
    "name": "Pillow"
}
```

By default it will check for source package (`sdist` package type).
To check for binary package instead, set `packagetype` to `bdist_wheel`
(only noarch wheels are supported currently).

### JetBrains checker

Special checker that will check for available updates
for [JetBrains](https://www.jetbrains.com/) products:

```json
"x-checker-data": {
    "type": "jetbrains",
    "code": "PRODUCT-CODE",
    "release-type": "release or eap (defaults to release)"
}
```

### Snapcraft checker

Special checker that will check for available updates
for [Snapcraft](https://snapcraft.io/) packages:

```json
"x-checker-data": {
    "type": "snapcraft",
    "name": "PACKAGE-NAME",
    "channel": "stable, beta, or any other tag the project uses"
}
```

### Rust checker

Special checker that will check for available updates
for [Rust](https://www.rust-lang.org/):

```json
"x-checker-data": {
    "type": "rust",
    "package": "package name, for example: rust",
    "channel": "nightly, stable or beta",
    "target": "target triple, for example: x86_64-unknown-linux-gnu"
}
```


## License and Copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
