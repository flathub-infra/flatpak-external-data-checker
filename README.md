# Flatpak External Data Checker

![Tests](https://github.com/flathub-infra/flatpak-external-data-checker/workflows/Tests/badge.svg)
[![Coverage Status](https://coveralls.io/repos/github/flathub/flatpak-external-data-checker/badge.svg)](https://coveralls.io/github/flathub/flatpak-external-data-checker?branch=master)

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

### Offline linting

Check a manifest's `x-checker-data` offline:

```sh
flatpak-external-data-checker --lint MANIFEST_FILE
```

`--lint` reads JSON and YAML manifests, including referenced module and source
files. For each source with `x-checker-data`, it checks required fields and value
types. It rejects unknown or incompatible checkers and checks regex syntax,
capture groups, jq syntax, and version-template syntax and variable names.
It also checks parent references for missing sources and inheritance cycles.

Errors include the filename and field location. The command exits with status 0
on success or 1 on lint errors. It makes no network requests and changes no files.
`--max-manifest-size` applies to referenced files too.

Linting cannot tell whether a query matches an upstream response. It is not a full
Flatpak manifest validator. You cannot combine `--lint` with update options or
`--filter-type`.

### Installation

This tool itself is available in Flatpak format from Flathub. Install with

```bash
flatpak install --from https://dl.flathub.org/repo/appstream/org.flathub.flatpak-external-data-checker.flatpakref
```

And run with

```bash
flatpak run org.flathub.flatpak-external-data-checker MANIFEST_DIR/MANIFEST_FILE
```

If the manifest is not in your home directory, you need to punch a sandbox hole for it:

```bash
flatpak run --filesystem=MANIFEST_DIR org.flathub.flatpak-external-data-checker MANIFEST_DIR/MANIFEST_FILE
```

#### Running in a container

**flatpak-external-data-checker** is also available as an
[OCI image](https://github.com/orgs/flathub-infra/packages/container/package/flatpak-external-data-checker)
from GitHub Container Registry.

You can use the `run-in-container.sh` helper script to set up needed CLI
options for you and run the image using `podman`:

```bash
./run-in-container.sh \
    [ARGS …] \
    ~/src/flathub/com.example.App/com.example.App.json
```

### On Flathub

Flathub [runs this tool periodically](https://github.com/flathub/flathub/blob/master/.github/workflows/external-data-checker.yml) for all Flatpak repos under [github.com/flathub](https://github.com/flathub).
So, for those repos to receive update PRs, add `x-checker-data` [as needed to sources](#changes-to-flatpak-manifests).
Note Flathub's hosted tool only checks the default branch.

To stop Flathub's tool from checking your repo, add `"disable-external-data-checker": true` to `flathub.json` in the default branch.

### Custom workflow

Alternatively, you can use your own workflow, for example to update non-default branches.

Put this YAML file in `.github/workflows/update.yaml` on the default branch.
Replace `master` with the branches to check and `com.example.App.json` with the
path to your manifest relative to the repository root.

```yaml
name: Check for updates
on:
  schedule: # for scheduling to work this file must be in the default branch
  - cron: "0 * * * *" # run every hour
  workflow_dispatch: # can be manually dispatched under GitHub's "Actions" tab

jobs:
  flatpak-external-data-checker:
    runs-on: ubuntu-latest
    permissions:
      contents: write # push update branches in this repository
      pull-requests: write # open update pull requests

    strategy:
      matrix:
        branch: [ master ] # list all branches to check

    steps:
        # 7.0.1
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          ref: ${{ matrix.branch }}
          persist-credentials: false

      - uses: docker://ghcr.io/flathub-infra/flatpak-external-data-checker:latest
        env:
          GIT_AUTHOR_NAME: Flatpak External Data Checker
          GIT_COMMITTER_NAME: Flatpak External Data Checker
          # Attribute commits to github-actions[bot].
          GIT_AUTHOR_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
          GIT_COMMITTER_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          args: --update --never-fork com.example.App.json
```

In Settings, Actions, General, enable "Allow GitHub Actions to create and approve
pull requests" under Workflow permissions. `--never-fork` keeps update branches
in this repository. Only check trusted branches with a write token.
Organization policies and branch protection still apply. See GitHub's
[Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).

`GITHUB_TOKEN` does not trigger `push` workflows. GitHub currently requires a user
with write access to approve workflow runs for the resulting `pull_request`
events of type `opened`, `synchronize`, and `reopened`.
See [GitHub's trigger restrictions](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs).
For unattended CI, replace `${{ secrets.GITHUB_TOKEN }}`
with a secret containing a GitHub App installation token or personal access
token with the required repository permissions. The `permissions` block controls
only the built-in token. The checker does not dispatch CI itself.

### Automatically submitting PRs

`--update` commits changes and opens a GitHub pull request. It requires a token in
`GITHUB_TOKEN`, either the Actions token above or a
[personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
with access to the target repository.

### Selectively submitting PRs

The tool will by default open PRs if at least one source is updated. Alternatively, the tool can be configured to only open PRs if at least one "important" source is updated. This can avoid unwanted PRs, e.g. PRs that only update a single library.

To only open PRs when at least one important source is updated, first set `require-important-update` to `true` in `flathub.json`. If using a custom workflow, instead pass `--require-important-update` to the tool. Then, set property `is-important` to `true` in the checker metadata for any important source(s). Sources with `is-main-source` in their checker metadata are also considered important.

### Automatically merging PRs

The tool will also automatically merge previously opened pull request for
unavailable (`BROKEN`) sources if the change has successfully passed CI checks
and the token has sufficient privileges.

Under limited circumstances, automatically merging all submitted PRs, not just unavailable sources,
from the tool can be forcefully enabled by setting
`automerge-flathubbot-prs` to `true` in `flathub.json`,
or it can be completely disabled by setting `automerge-flathubbot-prs` to `false`. Use of this option without requesting an exception will cause the build to fail in the linting stage; see the [FlatHub docs](https://docs.flathub.org/blog/linter-restricting-automatic-merge/) for more info.

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
`<`, `>`, `<=`, `>=`, `==`, `!=`), and the value is the version to compare with.
So, `{"<": "3.38.0", "!=": "3.37.1"}` means *"any version less than 3.38.0
except 3.37.1"*. All constraints must match simultaneously, i.e. if one doesn't
match -> version is rejected.

The `!=` operator accepts either a single version string or a list of
versions to exclude.

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
* If downloads come from load-balanced URLs, the `"pattern"` regex must
  match all URL variants otherwise identical versions may be treated as
  updates. Eg.

```json
"x-checker-data": {
  "type": "rotating-url",
  "url": "http://example.com/last-version",
  "pattern": "https://(?:dl|stable.dl\\d).example.com/foo-v([0-9.]+).tar.gz"
}
```

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
    "url-template": "https://www.example.com/pub/foo-v$version.tar.gz)"
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

The [`versions`](#version-constraining) property is supported.

#### URL templates

The HTML checker also supports building the download URL using
the retrieved version string, its components according to our
LooseVersion implementation (see `src/lib/version.py` based on
distutils' implementation before it was removed from Python stdlib in
3.12). `$version` is the complete version string; `$version0`, `$version1`,
and so on are its components. `$major`, `$minor`, and `$patch` are aliases
for the first three components, when present.

Components retain their original spelling, including leading zeroes: for
`2021.06.28`, `$minor` and `$version1` are both `06`. Dots are discarded,
but mixed-format versions keep LooseVersion's component boundaries. For
example, `1.02b03` has components `1`, `02`, `b`, `03`, so `$patch` is `b`
and `$version3` is `03`. These template values do not change numeric version
comparison. The same rules apply to Anitya URL and tag templates.

For example:

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

If the tags are just the version without anything else e.g. `1.0`, you don't need to
specify a `tag-pattern`.

If the project follows [semver](https://semver.org/) specification, you can set
`version-scheme` property to `semantic` in order to use semantic version scheme for sorting.
In this case, make sure that `tag-pattern` extracts only valid semver strings.

The [`versions`](#version-constraining) property is supported.

### GitHub releases checker

Use `github` to follow a repository's latest published stable GitHub release.
Unlike the Git checker, it selects a **release**, not the highest Git tag.
The `repo` property is the repository's `owner/name`.

For an uploaded release asset, specify its exact name using `asset-name`:

```yaml
x-checker-data:
  type: github
  repo: example/project
  tag-pattern: '^v([0-9.]+)$'
  asset-name: 'project_${version}_amd64.deb'
```

`tag-pattern` is optional. It must match the entire release tag and contain
exactly one capture group for the version. Without it, the version is the raw
tag, including any prefix. `asset-name` supports `$tag` (the unmodified tag),
`$version`, and the [version-component template variables](#url-templates).
Names are matched exactly, not as globs or regular expressions. Keep separate
sources with `only-arches` for different architectures. For example, an ARM64
source could use `project_${version}_arm64.deb`.

For GitHub's generated source archive, use `source-archive` instead of
`asset-name`:

```yaml
x-checker-data:
  type: github
  repo: example/project
  source-archive: tarball  # or zipball
```

For a `type: git` source, omit both artifact selectors. The checker updates its
tag and commit to the release's tag. The source must use an HTTP(S) GitHub URL
for the same repository as `repo` (an optional `.git` suffix is supported).

Use the JSON checker if you need prereleases or custom release selection.

### Forgejo releases checker

Use `forgejo` to follow a repository's latest published stable release on
Codeberg or another Forgejo instance:

```yaml
x-checker-data:
  type: forgejo
  instance: https://codeberg.org
  repo: example/project
  tag-pattern: '^v([0-9.]+)$'
  asset-name: 'project_${version}_amd64.deb'
```

Set `instance` to the installation's HTTP or HTTPS base URL, including any
deployment subpath. This field is required.

`repo`, `asset-name` and `tag-pattern` work as described for the
[GitHub checker](#github-releases-checker). For a generated source archive,
replace `asset-name` with `source-archive: tarball` or `source-archive: zipball`.

Git sources omit `asset-name` and `source-archive`. Their HTTP or HTTPS URL must
point to the same instance and repository. The URL may end in `.git`.

Use the JSON checker if you need prereleases or custom release selection.

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
for git type sources, specify tag query and, optionally, commit and version queries:
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

`release-url-query` extracts an optional HTTP or HTTPS release-notes link from
the same response, for example `"release-url-query": ".html_url // empty"`.
It can use `$version` and `$tag` from earlier queries. Missing or invalid results
do not block updates.

See the [jq manual](https://stedolan.github.io/jq/manual/) for complete information about writing queries.

#### Inheriting parent source check results

If a parent source is specified, its check results will be accessible in the `$parent` variable.
The `$parent` object has `current` and `new` properties with objects representing current and new
parent source data, respectively. The latter is `null` if the parent check didn't get a new version.
The [source-state schema](data/source-state.schema.json) describes each non-null state,
not the enclosing `$parent` object.

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

Set `stable-only` to `false` to include unstable releases (as recognized by Anitya).

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

Set `stable-only` to `false` to check for pre-releases, too. By default, any
version that contains the string `alpha`, `beta` or `rc` is considered a
pre-release, following the dominant convention since GNOME 40. To override this,
set `version-scheme` to `odd-minor-is-unstable` to consider any version with an
odd second component to be a pre-release.

The [`versions`](#version-constraining) property is supported.

### PyPI checker

Check for Python package updates on PyPI.

```json
"x-checker-data": {
    "type": "pypi",
    "name": "Pillow"
}
```

The checker selects source packages by default. Set `packagetype` to
`bdist_wheel` to select wheels compatible with the current source wheel.
If the source URL is not a wheel filename, only `py3-none-any` wheels qualify.

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

### Release URL template

When updating the metainfo file with the release information of the main
source if a `release-url-template` is provided in the `x-checker-data`
like below a `<url type="details"></url>` tag will also be added.

The `$version` in `release-url-template` will be replaced by the release
version.

New pull requests list each source's old and new versions or Git revisions.
Missing versions appear as `unknown`. Set `release-url-template` on any source
to add an HTTP or HTTPS release-notes link. It overrides `release-url-query`
and automatic links. Templates containing `$version` need a known new version.

Automatic links use existing download URLs and checker responses:

- GitHub, Codeberg, and gitlab.com release downloads.
- GitHub, GitLab, and Forgejo/Gitea release API responses, including secondary
  JSON query responses and self-hosted GitLab and Forgejo/Gitea instances.
  Matches require the exact asset URL or Git tag. Git release pages must also
  belong to the source's HTTP or HTTPS repository.
- JetBrains `notesLink` and GNOME `.news` files, falling back to `.changes`.

Discovery makes no extra requests. Use a template or `release-url-query` for
other URL formats or release notes hosted by another project.

```yaml
x-checker-data:
    type: json
    url: https://api.github.com/repos/example/foobar/releases
    version-query: map(select(.prerelease)) | first | .tag_name
    url-query: map(select(.prerelease)) | first | .assets[] | select(.name=="foobar-x86_64.tar.gz")| .browser_download_url
    release-url-template: https://github.com/example/foobar/releases/tag/$version
    is-main-source: true
```

## robots.txt

robots.txt is respected when the `--enable-robots-txt` commandline
argument is passed, or when enabled in a source's checker metadata
using `enable-robots-txt: true`. The CLI argument overrides the
per-source metadata.

When enabled, the robots.txt files are cached upto 24 hours in
`$XDG_CACHE_HOME/fedc/robots`.

The Chromium, PyPI, Rust and SnapCraft checkers disables robots.txt
handling entirely as the checkers itself rely on some paths being
accesible.

## License and Copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
