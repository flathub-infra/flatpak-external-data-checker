# Flatpak External Data Checker

![Tests](https://github.com/flathub-infra/flatpak-external-data-checker/workflows/Tests/badge.svg)
[![Coverage Status](https://coveralls.io/repos/github/flathub/flatpak-external-data-checker/badge.svg)](https://coveralls.io/github/flathub/flatpak-external-data-checker?branch=master)

Check Flatpak manifests for broken source URLs and new upstream releases.
The tool can update manifests and open GitHub pull requests.

Broken `extra-data` URLs prevent users from installing an app. Other broken
source URLs prevent builds.

## Use

Check a manifest:

```
flatpak-external-data-checker MANIFEST_FILE
```

The command reports broken or outdated sources.

The checker skips apps with `end-of-life` or `end-of-life-rebase` in the
repository's `flathub.json`. Outside a Git checkout, it looks beside the manifest.

### Offline linting

Check a manifest's `x-checker-data` offline:

```sh
flatpak-external-data-checker --lint MANIFEST_FILE
```

`--lint` checks checker settings in JSON and YAML manifests and their referenced
files. It prints the filename and field for each error and exits with status 1
if it finds any. It does not change files or test queries against upstream data.
Run it without update options or `--filter-type`.

### JSON reports

Write a JSON report:

```sh
flatpak-external-data-checker --report-json report.json MANIFEST_FILE
```

Create the destination directory first. Reports include failed and skipped runs.
You can also use `--report-json` with `--lint`.

### Runtime branches

For extensions with `build-extension: true`, the checker looks for the same
manifest path on each `branch/*` branch on `origin`. Each update pull request
targets its own branch. The checker skips branches marked as end-of-life in
`flathub.json` and branches whose parent runtime or SDK has reached end of life
on all matching architectures.

Linting and current-checkout modes check only the supplied manifest. The checker
also uses that manifest when it finds no runtime branches on `origin` or runs
outside a Git checkout.

### Git submodules

Git submodules such as `shared-modules` need no `x-checker-data`. The checker
proposes an update only if a referenced manifest or local source changes. It
follows the branch configured in `.gitmodules`, or the remote's default branch.

### Installation

Install from Flathub:

```bash
flatpak install --from https://dl.flathub.org/repo/appstream/org.flathub.flatpak-external-data-checker.flatpakref
```

Run it:

```bash
flatpak run org.flathub.flatpak-external-data-checker MANIFEST_DIR/MANIFEST_FILE
```

If the manifest is outside your home directory, grant access to its directory:

```bash
flatpak run --filesystem=MANIFEST_DIR org.flathub.flatpak-external-data-checker MANIFEST_DIR/MANIFEST_FILE
```

#### Running in a container

Use `run-in-container.sh` to run the
[container image](https://github.com/orgs/flathub-infra/packages/container/package/flatpak-external-data-checker)
with Podman:

```bash
./run-in-container.sh \
    [ARGS ...] \
    ~/src/flathub/com.example.App/com.example.App.json
```

### On Flathub

Flathub [runs this tool periodically](https://github.com/flathub/flathub/blob/master/.github/workflows/external-data-checker.yml) for all Flatpak repos under [github.com/flathub](https://github.com/flathub).
To receive update PRs, add `x-checker-data` [to your sources](#changes-to-flatpak-manifests).
Flathub starts the checker on the default branch. For extensions, see
[Runtime branches](#runtime-branches).

To stop Flathub's tool from checking your repo, add `"disable-external-data-checker": true` to `flathub.json` in the default branch.

### Custom workflow

Use your own workflow to choose which branches to check.

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
only the built-in token.

### Automatically submitting PRs

`--update` commits changes and opens a GitHub pull request. It requires a token in
`GITHUB_TOKEN`, either the Actions token above or a
[personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
with access to the target repository.

### Selectively submitting PRs

By default, the tool opens a PR whenever a source has an update. To avoid PRs
that only update dependencies, you can require an update to an important source.

Set `require-important-update` to `true` in `flathub.json`, or pass
`--require-important-update` in a custom workflow. Mark important sources with
`is-important: true` in their checker metadata. Sources marked with
`is-main-source: true` also count as important.

### Automatically merging PRs

The tool automatically merges its existing PRs for broken source URLs after
CI passes, if the token has permission to merge.

Set `automerge-flathubbot-prs` to `false` in `flathub.json` to disable automatic
merging. Setting it to `true` enables automatic merging for all of the tool's
PRs, but requires a Flathub exception. Without an exception, linting fails.
See the [Flathub docs](https://docs.flathub.org/blog/linter-restricting-automatic-merge/).

### Formatting manifests

The checker writes JSON with four-space indentation and preserves the file's
trailing newline if present. To change these defaults, commit an
[EditorConfig](https://editorconfig.org/) file with settings for `*.json`:

```ini
root = true

[*.json]
indent_style = space
# Only integer values are supported; ignored if indent_style=tab
indent_size = 2
insert_final_newline = true
```

The tool does not preserve `/* */` comments in JSON. You can use keys beginning
with `//` for comments instead. `flatpak-builder` ignores these keys.

For YAML, the checker tries to preserve formatting and comments. It ignores
`.editorconfig`. See the [Flathub YAML Style Guide](https://github.com/flathub/flathub/wiki/YAML-Style-Guide).

## Changes to Flatpak manifests

Checking for broken URLs needs no manifest changes. To find new versions,
add `x-checker-data` to each source you want to update.

Set `is-main-source` to `true` in the app's upstream source checker metadata
to add new versions to the metainfo release list, if the checker provides a
version number.

### Synchronized update groups

Set the same nonempty `update-group` in each related source's `x-checker-data`.
A group must contain at least two distinct sources and can span referenced files
and modules. Architectures and `parent-id` do not create groups automatically.

For example, add this field to both the `x86_64` and `aarch64` sources:

```yaml
update-group: app-binaries
```

Each checker must confirm the same version string. For example, `2`, `2.0`
and `v2` differ. Use a version query or pattern if the sources spell the
version differently. If any check fails, skips a source, or finds a different
version, the checker reports an error and leaves the group unchanged. Sources
outside the group can still update.

### Version constraining

Use `versions` to limit updates, for example to a particular major version.
Each key is a comparison operator, one of `<`, `>`, `<=`, `>=`, `==`, or `!=`.
Each value is a version to compare against.
For example, `{"<": "3.38.0", "!=": "3.37.1"}` accepts versions below 3.38.0
except 3.37.1. A version must satisfy every constraint.

The `!=` operator accepts either a single version string or a list of
versions to exclude.

### URL checker

Use `rotating-url` when upstream provides a URL that redirects to the latest
release:

```json
"x-checker-data": {
    "type": "rotating-url",
    "url": "http://example.com/last-version",
    "pattern": "http://example.com/foo-v([0-9.]+).tar.gz"
}
```

The checker detects the latest version as follows:

* If the requested URL ends with `.AppImage` or `.deb`, the checker attempts to
  extract the version from the downloaded file. Set `"no-extract-version": true`
  in `"x-checker-data"` to skip extraction.
* If extraction is skipped or returns no version, the checker uses `"pattern"`
  from `"x-checker-data"` to match the final download URL. A single capture
  group extracts the version number. Without a capture group, the version is
  the whole match, including any URL prefix. Use `(?:...)` for additional
  grouping. Multiple capture groups are not allowed.
* If the download host varies, match every host in `pattern` so a host change
  does not count as a version change. For example:

```json
"x-checker-data": {
  "type": "rotating-url",
  "url": "http://example.com/last-version",
  "pattern": "https://(?:dl|stable.dl\\d).example.com/foo-v([0-9.]+).tar.gz"
}
```

If upstream adds a tracking query that changes on each download, set
`strip-query` to `true` to remove the query from the URL.

### HTML checker

Use `html` to find a version and download URL on a static web page:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]+)",
    "url-template": "https://www.example.com/pub/foo-v$version.tar.gz"
}
```

`version-pattern` uses its capture group as the version, or the whole match if
there is no capture group. For example, `\b[0-9]+(?:\.[0-9]+)+\b` matches version
numbers directly. Use `(?:...)` for additional grouping. The checker rejects
multiple capture groups.

To extract both the download URL and version, use `pattern` with two capture
groups. The first captures the URL and the second captures the version:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://sourceforge.net/projects/qrupdate/rss",
    "pattern": "<link>(https://sourceforge.net/.+/qrupdate-([\\d\\.]+\\d).tar.gz)/download</link>"
}
```

Set `sort-matches` to `false` to use the first match.

The [`versions`](#version-constraining) property is supported.

#### URL templates

Use `url-template` to build a download URL from the version.
`$version` is the complete version string. `$version0`, `$version1`, and so on
are its components. `$major`, `$minor`, and `$patch` are aliases for the first
three components, when present.

Components retain their original spelling, including leading zeroes: for
`2021.06.28`, `$minor` and `$version1` are both `06`. Dots are discarded,
and letters and numbers form separate components. For
example, `1.02b03` has components `1`, `02`, `b`, `03`, so `$patch` is `b`
and `$version3` is `03`. The same rules apply to Anitya URL and tag templates.

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

If an underscore follows a placeholder, enclose its name in braces:

```json
"x-checker-data": {
    "type": "html",
    "url": "https://www.example.com/download.html",
    "version-pattern": "The latest version is ([\\d\\.-]*)",
    "url-template": "https://www.example.com/$version0.$version1/v${version0}_${version1}_version2.tar.gz"
}
```

### Git checker

Use `git` to follow the latest tag in a Git source repository.
`tag-pattern` uses its capture group as the version, or the whole match if
there is no capture group:

```json
"x-checker-data": {
    "type": "git",
    "tag-pattern": "^v([\\d.]+)$"
}
```

The checker sorts tags by version. Set `sort-tags` to `false` to keep the order
from `git ls-remote`. Omit `tag-pattern` if the tag is the version, such as `1.0`.

For a tag such as `v1.2.3`, `^v[0-9.]+$` uses `v1.2.3` as the version.
Add a capture group, `^v([0-9.]+)$`, to use `1.2.3` instead.

Use `(?:...)` for additional grouping. Multiple capture groups are not allowed.
To remove a prefix, place it outside the capture group. Patterns match from the
start of the tag. End the pattern with `$` to match the entire tag.

Set `version-scheme` to `semantic` to sort tags by [semantic version](https://semver.org/).
The `tag-pattern` must extract valid semantic versions.

The [`versions`](#version-constraining) property is supported.

### GitHub releases checker

Set `type: github` and `repo: owner/name` to track GitHub releases.
By default, the checker uses only the latest stable release. If none exists
or its tag does not match `tag-pattern`, the check fails.

Set `include-prereleases: true` to include prereleases. The checker skips drafts
and selects the first release in GitHub API order that matches the optional
`tag-pattern`.

For an uploaded release asset, specify its exact name using `asset-name`:

```yaml
x-checker-data:
  type: github
  repo: example/project
  tag-pattern: '^v([0-9.]+)$'
  asset-name: 'project_${version}_amd64.deb'
```

`tag-pattern` is optional and must match the entire release tag. It uses the
same extraction rules as the [Git checker](#git-checker). Without a capture
group or a `tag-pattern`, the version is the raw tag, including any prefix.
`asset-name` supports `$tag` for the unmodified tag,
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
tag and commit to the release's tag. The source must use an HTTP or HTTPS GitHub
URL for the same repository as `repo`. The URL may end in `.git`.

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

Use `json` to extract a version and download URL from a JSON response with
[jq](https://stedolan.github.io/jq/). Set the response URL and both queries.
The URL query can use `$version` from the version query:

```json
"x-checker-data": {
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "version-query": ".tag_name | sub(\"^jq-\"; \"\")",
    "url-query": ".assets[] | select(.name==\"jq-\" + $version + \".tar.gz\") | .browser_download_url"
}
```
For Git sources, specify a tag query and optional commit and version queries:
```json
"x-checker-data": {
    "type": "json",
    "url": "https://api.github.com/repos/stedolan/jq/releases/latest",
    "tag-query": ".tag_name",
    "version-query": "$tag | sub(\"^jq-\"; \"\")",
    "timestamp-query": ".published_at"
}
```

The optional `timestamp-query` must return an ISO timestamp string.

`release-url-query` extracts an optional HTTP or HTTPS release-notes link from
the same response, for example `"release-url-query": ".html_url // empty"`.
It can use `$version` and `$tag` from earlier queries. Missing or invalid results
do not block updates.

See the [jq manual](https://stedolan.github.io/jq/manual/) for query syntax.

#### Inheriting parent source check results

When you specify a parent source, use `$parent.current` for its current source
data and `$parent.new` for its update. `$parent.new` is `null` if the parent
has no update.
See the [source-state schema](data/source-state.schema.json) for the available fields.

### Debian repo checker

Use `debian-repo` to check for Debian package updates. Add this metadata to the
source, such as an `extra-data` source:

```json
"x-checker-data": {
    "type": "debian-repo",
    "package-name": "YOUR_PACKAGE_NAME",
    "root": "ROOT_URL_TO_THE_DEBIAN_REPO",
    "dist": "DEBIAN_DIST",
    "component": "DEBIAN_COMPONENT"
}
```

### Anitya checker

[Anitya](https://release-monitoring.org/) tracks upstream releases.
Set `project-id` to the project's numeric ID on release-monitoring.org and
provide a download URL using the [URL template syntax](#url-templates):

```json
"x-checker-data": {
    "type": "anitya",
    "project-id": 6377,
    "stable-only": false,
    "versions": {"<": "1.12.0"},
    "url-template": "https://github.com/flatpak/flatpak/releases/download/$version/flatpak-$version.tar.xz"
}
```

Set `stable-only` to `false` to include releases that Anitya marks as unstable.

The [`versions`](#version-constraining) property is supported.

For Git sources, the original Anitya version is used as the tag by default:

```json
"x-checker-data": {
    "type": "anitya",
    "project-id": 1353
}
```

This preserves tag prefixes such as `v` when present. `stable-only` and
`versions` still select the normalized version.

Set `tag-template` to override the default. `$version` is the normalized version;
`$original_version` is the original spelling, for example
`"tag-template": "release-$original_version"`.

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

### Electron auto-update checker

Use `electron-updater` for apps that publish
[Electron update metadata](https://www.electron.build/auto-update.html):

```yaml
x-checker-data:
  type: electron-updater
  url: https://example.com/download/latest-linux.yml
```

Set `url` to an Electron update metadata file, usually `latest-linux.yml`.
If you omit it, the checker tries to find it using the current source URL.

Use a `sha512` checksum for the source. For `extra-data`, use `sha256` instead.

### JetBrains checker

Check for [JetBrains](https://www.jetbrains.com/) product updates:

```json
"x-checker-data": {
    "type": "jetbrains",
    "code": "PRODUCT-CODE",
    "release-type": "release"
}
```

`release-type` accepts `release`, `eap`, `rc`, or `preview` and defaults to
`release`. Not every product offers every release type.

For OSS archives from JetBrains' GitHub releases, add `"oss": true`.
Use `"code": "IIC"` for IntelliJ IDEA or `"code": "PCC"` for PyCharm.
OSS mode supports only stable releases for `x86_64` and `aarch64`.

### Snapcraft checker

Check for [Snapcraft](https://snapcraft.io/) package updates:

```json
"x-checker-data": {
    "type": "snapcraft",
    "name": "PACKAGE-NAME",
    "channel": "stable, beta, or any other tag the project uses"
}
```

### Rust checker

Check for [Rust](https://www.rust-lang.org/) updates:

```json
"x-checker-data": {
    "type": "rust",
    "package": "package name, for example: rust",
    "channel": "nightly, stable or beta",
    "target": "target triple, for example: x86_64-unknown-linux-gnu"
}
```

### Chromium checker

Check for [Chromium](https://www.chromium.org/) source or LLVM toolchain updates:

```json
"x-checker-data": {
    "type": "chromium",
    "component": "chromium, llvm-prebuilt, or llvm-git; defaults to chromium"
}
```

The following components are supported:

- `chromium`: updates the Chromium tarball for URL-based sources such as
  `type: archive`.
- `llvm-prebuilt`: updates a tarball pointing to the official LLVM prebuilt
  toolchain archive matching the latest Chromium version. Used on URL-based
  sources.
- `llvm-git`: updates a `type: git` source for its commit to point to the LLVM
  sources for the toolchain used by the latest Chromium version.

### Release URL template

Set `release-url-template` to add a release-notes link to update PRs.
For the main source, the tool also adds the link to the metainfo release entry.
`$version` expands to the new version, which must be known if the template uses it.

The tool finds release-notes links automatically for supported GitHub, GitLab,
Forgejo/Gitea, JetBrains, and GNOME sources. Use a template or the JSON checker's
`release-url-query` when the automatic link is missing or points to the wrong
page. A template overrides both queried and automatic links.

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

Pass `--enable-robots-txt` to respect robots.txt rules, or set
`enable-robots-txt: true` in a source's checker metadata. The command-line
option overrides per-source settings.

The Chromium, PyPI, Rust, and Snapcraft checkers do not support this option.

## License and copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
