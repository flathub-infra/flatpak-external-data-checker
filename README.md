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

## Running in a container

First build the container image with:
```
podman build -t flatpak-external-data-checker \
    --build-arg USER_ID=$(id -u) \
    --build-arg GROUP_ID=$(id -g) \
    .
```

And then run the checker in the container with:
```
podman run --rm \
    --privileged \
    -u $(id -u):$(id -g) \
    -w $(pwd) \
    -v $(pwd):$(pwd) \
    -v "$SSH_AUTH_SOCK":/ssh-agent \
    -e SSH_AUTH_SOCK=/ssh-agent \
    -it flatpak-external-data-checker \
    ./src/flatpak-external-data-checker ./PATH/TO/MANIFEST_FILE
```

## Automatically submitting PRs

When run with the `--update` flag, this tool can commit any necessary changes
to Git and open a GitHub pull request. In order to do this, it needs to have
two credentials:

* A [GitHub access token](https://help.github.com/en/articles/creating-a-personal-access-token-for-the-command-line),
  specified in the `GITHUB_TOKEN` environment variable
* `git push` access for the repository containing the manifest passed on the
  command line; ie an appropriate `ssh` key

### Changes to Flatpak manifests

For simple checks to see if a URL is broken, no changes are needed.
However, the DebianRepoChecker, FlashChecker and the URLChecker can
read metadata from the manifest, in order to try to inform about new
versions.

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

#### URL checker

If the upstream vendor has an URL that redirects to the latest version of the
application, you can add something like the following to check and update the URL for
the latest version:

```json
  "x-checker-data": {
                     "type": "rotating-url",
                     "url": "http://example.com/last-version"
                    }
```

## License and Copyright

License: GPLv2

Copyright © 2018–2019 Endless Mobile, Inc.
