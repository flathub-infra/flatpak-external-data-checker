# Flatpak External Data Checker

![Tests](https://github.com/flathub/flatpak-external-data-checker/workflows/Tests/badge.svg)
[![Coverage Status](https://coveralls.io/repos/github/flathub/flatpak-external-data-checker/badge.svg)](https://coveralls.io/github/flathub/flatpak-external-data-checker)
[![Total alerts](https://img.shields.io/lgtm/alerts/g/flathub/flatpak-external-data-checker.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/flathub/flatpak-external-data-checker/alerts/)
[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/flathub/flatpak-external-data-checker.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/flathub/flatpak-external-data-checker/context:python)
[![CodeFactor](https://www.codefactor.io/repository/github/flathub/flatpak-external-data-checker/badge)](https://www.codefactor.io/repository/github/flathub/flatpak-external-data-checker)

This is a tool for checking for outdated or broken links of external
data in Flatpak manifests.

---

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

### Installation

This tool itself is available in flatpak format from Flathub. Install with

```bash
flatpak install --from https://dl.flathub.org/repo/appstream/org.flathub.flatpak-external-data-checker.flatpakref
```

And run with

```bash
flatpak run org.flathub.flatpak-external-data-checker MANIFEST_FILE
```

#### Running in a container

**flatpak-external-data-checker** is also avaiable as an
[OCI image](https://github.com/orgs/flathub/packages/container/package/flatpak-external-data-checker)
from GitHub Container Registry.

You can use the `run-in-container.sh` helper script to set up needed CLI
options for you and run the image using `podman`:

```bash
~/src/endlessm/flatpak-external-data-checker/run-in-container.sh \
    [ARGS …] \
    ~/src/flathub/com.example.App/com.example.App.json
```

### On Flathub

Flathub runs this tool hourly for all Flatpak repos under [github.com/flathub](https://github.com/flathub). 
So, for those repos to receive update PRs, add `x-checker-data` [as needed to sources](#changes-to-flatpak-manifests). 
Note Flathub's hosted tool only checks the default branch.

To stop Flathub's tool from checking your repo, add `"disable-external-data-checker": true` to `flathub.json` in the default branch.

### Custom workflow

Alternatively, you can use own workflow. This can be useful if e.g. wanting to update non-default branches.

Put this yaml file under `.github/workflows`, e.g. put it in `.github/workflows/update.yaml`. Ensure to put the correct path to the manifest in the last line.

```yaml
name: Check for updates
on:
  schedule: # for scheduling to work this file must be in the default branch
  - cron: "0 * * * *" # run every hour
  workflow_dispatch: # can be manually dispatched under GitHub's "Actions" tab 

jobs:
  flatpak-external-data-checker:
    runs-on: ubuntu-latest

    strategy:
      matrix:
        branch: [ master ] # list all branches to check
    
    steps:
      - uses: actions/checkout@v3
        with:
          ref: ${{ matrix.branch }}

      - uses: docker://ghcr.io/flathub/flatpak-external-data-checker:latest
        env:
          GIT_AUTHOR_NAME: Flatpak External Data Checker
          GIT_COMMITTER_NAME: Flatpak External Data Checker
          # email sets "github-actions[bot]" as commit author, see https://github.community/t/github-actions-bot-email-address/17204/6
          GIT_AUTHOR_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
          GIT_COMMITTER_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
          EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          args: --update --never-fork $PATH_TO_MANIFEST # e.g. com.organization.myapp.json
```

### Automatically submitting PRs

When run with the `--update` flag, this tool can commit any necessary changes
to Git and open a GitHub pull request. In order to do this, it requires
a [GitHub access token](https://help.github.com/en/articles/creating-a-personal-access-token-for-the-command-line),
specified in the `GITHUB_TOKEN` environment variable.

### Automatically merging PRs

The tool will also automatically merge previously opened pull request for
unavailable (`BROKEN`) sources if the change has successfully passed CI checks
and the token has sufficient privileges.

Automatically merging all submitted PRs, not just unavailable sources,
from the tool can be forcefully enabled by setting
`automerge-flathubbot-prs` to `true` in `flathub.json`,
or it can be completely disabled by setting `automerge-flathubbot-prs` to `false`.

### Formatting manifests

When writing back JSON files, this tool defaults to four-space indentation, preserving or omitting a trailing newline based on the source file. If you prefer a different formatting, create and commit an `.editorconfig` file describing your preferred formatting for `.json` files. At present, this tool respects a subset of [EditorConfig](https://editorconfig.org/) settings:

```ini
root = true

[*.json]
indent_style = space
# Only integer values are supported; ignored if indent_style=tab
indent_size = 2
insert_final_newline = true
```

Unfortunately, it is not feasible to preserve JSON-GLib's non-standard `/* */` syntax for comments. As an alternative, dictionary keys beginning with `//` are ignored by `flatpak-builder` and can be used for comments in many cases.

For YAML files, this tool attempts to preserve existing formatting and comments automatically. `.editorconfig` is not used. We recommend you follow the [Flathub YAML Style Guide](https://github.com/flathub/flathub/wiki/YAML-Style-Guide).

## Changes to Flatpak manifests

For simple checks to see if a URL is broken, no changes are needed.  However,
you can add additional metadata to the manifest to allow the checker to
discover new versions.

Some of the following checkers are able to determine upstream version number,
and automatically add it to releases list in metainfo. To specify which source
is the app upstream source, set property `is-main-source` to `true` in the
checker metadata for that source.

### Version constraining

The checkers can support version constraining, making it possible to e.g. limit
version checking to a certain major version. The property is `versions`. It
should contain key-value pairs where the key is the comparison operator (one of
`<`, `>`, `<=`, `>=`, `==`, `!=`), and the value is the version to compare with
So, `{"<": "3.38.0", "!=": "3.37.1"}` means *"any version less than 3.38.0
except 3.37.1"*. All constraints must match simultaneously, i.e. if one doesn't
match -> version is rejected.

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

Some upstream vendors may add unwanted GET query parameters to
the download URL, such as identifiers for counting unique downloads.
This may result in URL change without change in the actual data.
To remove GET query parameters, set `strip-query` property to `true`.

### HTML checker

Both the version number and the download URL will be gathered from a static
HTML page which contains this information:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]+)",
    "url-pattern": "(https://www.example.com/pub/foo/v([\\d\\.-]+)/foo.tar.gz)"
}
```

If the HTML page contains multiple versions with download links,
set single pattern containing two nested match groups for both url and version:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://sourceforge.net/projects/qrupdate/rss",
    "pattern": "<link>(https://sourceforge.net/.+/qrupdate-([\\d\\.]+\\d).tar.gz)/download</link>"
}
```

To disable sorting and get first matched version/url, set `sort-matches` to `false`.

#### URL templates

The HTML checker also supports building the download URL using
the retrieved version string, its components according to the Python
[LooseVersion](http://epydoc.sourceforge.net/stdlib/distutils.version.LooseVersion-class.html)
class and semantic versioning fields:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]*)",
    "url-template": "https://www.example.com/$version/v$version.tar.gz"
}
```

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]*)",
    "url-template": "https://www.example.com/$major.$minor/v$version.tar.gz"
}
```

If the placeholder is immediately followed by an underscore, you need to add braces:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]*)",
    "url-template": "https://www.example.com/$version0.$version1/v${version0}_${version1}_version2.tar.gz"
}
```

### Git checker

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

If the project follows [semver](https://semver.org/) specification, you can set
`version-scheme` property to `semantic` in order to use semantic version scheme for sorting.
In this case, make sure that `tag-pattern` extracts only valid semver strings.

The [`versions`](#version-constraining) property is supported.

### JSON checker

The JSON checker allows using [jq](https://stedolan.github.io/jq/) to query
JSON data with arbitrary schema to get version and download url.

To use the **JSONChecker**, specify JSON data URL, version query and url query
(you can use `$version` variable got from the version query in url query):

```json
"x-checker-data": {
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "version-query": ".tag_name | sub(\"^jq-\"; \"\")",
    "url-query": ".assets[] | select(.name==\"jq-\" + $version + \".tar.gz\") | .browser_download_url"
}
```
for git type sources, specify tag query and, optionaly, commit and version queries:
```json
"x-checker-data": {
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "tag-query": ".tag_name",
    "version-query": "$tag | sub(\"^jq-\"; \"\")",
    "timestamp-query": ".published_at"
}
```

`timestamp-query` is optional, but if provided - must return a string with timestamp in ISO format.

See the [jq manual](https://stedolan.github.io/jq/manual/) for complete information about writing queries.

#### Inheriting parent source check results

If a parent source is specified, its check results will be accessible in the `$parent` variable.
The `$parent` object has `current` and `new` properties with objects representing current and new
parent source data, respectively. The later can be `null` if parent check didn't get a new version.
JSON schema for these objects can be found [here](data/source-state.schema.json).


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
and add a template for source download URL.
[Template syntax](#URL-templates) is the same as for the **HTMLChecker**:

```json
"x-checker-data": {
    "type": "anitya",
    "project-id": 6377,
    "stable-only": false,
    "versions": {"<": "1.12.0"},
    "url-template": "https://github.com/flatpak/flatpak/releases/download/$version/flatpak-$version.tar.xz"
}
```
Set `stable-only` to `true` to retrieve latest stable version (as recognized by Anitya).

The [`versions`](#version-constraining) property is supported.

For git type sources, instead of `url-template`, set `tag-template` to derive git tag from version.

### GNOME checker

Check for latest source tarball for a GNOME project.

```json
"x-checker-data": {
    "type": "gnome",
    "name": "pygobject",
    "versions": {
        "<": "3.38.0"
    },
    "stable-only": true
}
```

Set `stable-only` to `false` to check for pre-releases, too.

The [`versions`](#version-constraining) property is supported.

### PyPI checker

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

The [`versions`](#version-constraining) property is supported.

### Electron Auto Update checker

Electron [auto update](https://www.electron.build/auto-update.html) mechanism uses remotely stored
metadata files to check for updates. This metadata can be tracked for Flatpak manifest updates.

```yaml
x-checker-data:
  type: electron-updater
  url: https://example.com/download/latest-linux.yml
```

The `url`, if set, must link to a Electron Auto Update metadata file (usually `latest-linux.yml`).  
If `url` is omitted, the checker will try to guess it based on the current source url.

Make sure to use `sha512` checksum for the source, unless it's an `extra-data` (which supports `sha256` only).

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

### Chromium checker

Special checker that will check for available updates to the
[Chromium](https://www.chromium.org/) tarballs, as well as the toolchain
binaries or sources used to build it.

```json
"x-checker-data": {
    "type": "chromium",
    "component": "chromium, llvm-prebuilt, or llvm-git; defaults to chromium"
}
```

The following components are supported:

- `chromium`: updates the Chromium tarball itself, used on URL-based sources
  (e.g. `type: archive`).
- `llvm-prebuilt`: updates a tarball pointing to the official LLVM prebuilt
  toolchain archive matching the latest Chromium version. Used on URL-based
  sources.
- `llvm-git`: updates a `type: git` source for its commit to point to the LLVM
  sources for the toolchain used by the latest Chromium version.

## License and Copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
