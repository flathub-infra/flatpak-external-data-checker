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

### Changes to Flatpak manifests

For simple checks to see if a URL is broken, no changes are needed.
However, the DebianRepoChecker and the RotatingURLChecker can read
metadata from the manifest, in order to try to inform about new
versions.

#### Debian repo checker

For the **DebianRepoChecker**, which deals only with deb packages, it
can read the following metadata (add it to manifest element it refers
to, e.g. where "type": "extra-data" is declared):

```
  "x-checker-data": {
                     "type": "debian-repo",
                     "package-name": "YOUR_PACKAGE_NAME",
                     "root": "ROOT_URL_TO_THE_DEBIAN_REPO",
                     "dist": "DEBIAN_DIST",
                     "component": "DEBIAN_COMPONENT"
                    }
```

#### Rotating URL checker

For the **RotatingURLChecker** you can use simply the following, as
it will compare the URL in the *checker-data* section with the
external data's one:

```
  "x-checker-data": {
                     "type": "rotating-url",
                     "url": "http://example.com/last-version"
                    }
```

## License and Copyright

License: GPLv2

Copyright (c) 2018 Endless Mobile, Inc.
