# Flatpak External Data Checker

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

### Running in a container with Podman

`run-in-container.sh` builds a container using `podman` and the `Dockerfile` in
this repository, then runs it with appropriate UID and filesystem mappings.
This is a convenient way to get all necessary dependencies regardless of your
host system's distribution.

```bash
~/src/endlessm/flatpak-external-data-checker/run-in-container.sh \
    ~/src/endlessm/flatpak-external-data-checker/flatpak-external-data-checker \
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

#### URL checker

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

#### HTML checker

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

#### Debian repo checker

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

### Firefox checker

For the **FirefoxChecker**, which deals only with new versions of Firefox and
its translations, it can read the following metadata (opposed to other checkers,
this metadata should be added to the module element instead of the source element
in the manifest):

```json
"x-checker-data": {
    "type": "firefox",
}
```

The checker will try to match the module sources to be checked as follows:
* The module source with filename "firefox.tar.bz2" will be considered to refer to
  the firefox tarball (at least one source should refer to the firefox tarball)
* All sources whose filename has ".xpi" as extension will be considered translations

Also note that translations will be automatically added and removed depending on what
is available upstream.

### Flash player checker

For the **FlashChecker**, which deals only with new version of Adobe
Flash player, it can read the following metadata (add it to the manifest
element it refers to, e.g. where "type": "extra-data" is declared):

```json
"x-checker-data": {
    "type": "flash",
    "browser": "chrome|firefox"
}
```

The value for "browser" is used to determine whether to download the
Firefox-compatible Flash binaries (npapi) or the Chrome-compatible
ones (ppapi).

In addition, if you add that metadata, you **must** set only-arches
on the extra-data source itself to i386 or x86_64:

```json
  "type": "extra-data",
  "only-arches": ["i386"],
  // ...
```

FlashChecker will use this to determine which architecture to check
the binaries for.

#### JetBrains checker

Special checker that will check for available updates
for [JetBrains](https://www.jetbrains.com/) products:

```json
"x-checker-data": {
    "type": "jetbrains",
    "code": "PRODUCT-CODE",
    "release-type": "release or eap (defaults to release)"
}
```

## License and Copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
