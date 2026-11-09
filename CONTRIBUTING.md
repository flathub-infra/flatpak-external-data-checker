# Native prerequisites

CI uses Debian trixie. From the repository root, install its runtime packages:

On Debian trixie, install the PyGObject and pycairo build dependencies before
running `uv sync`, as CI does:

```sh
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libcairo2-dev \
    libgirepository-2.0-dev python3-dev
```

Older distributions with only `libgirepository1.0-dev` cannot build the pinned
PyGObject. The Docker image uses Debian packages from `dependencies.apt.txt`,
not the versions in `uv.lock`.

```sh
sudo apt-get update
sudo xargs -a dependencies.apt.txt apt-get install --no-install-recommends -y
/usr/bin/python3 ./flatpak-external-data-checker --help
```

These include PyGObject, JSON-GLib introspection data, and Python APT bindings.
Use the system Python with these packages. uv's isolated tooling environment
does not include the APT bindings, which must match the interpreter's ABI.

For other systems, use the [container](README.md#running-in-a-container).
The helper requires Podman and a Git name/email. It uses `--privileged` and
mounts your home directory, so run only trusted code.

# Checks

Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
On Debian trixie, building PyGObject and Pycairo also requires these packages.
Older distributions may lack the native library versions required by `uv.lock`.

```sh
sudo apt-get install -y build-essential pkg-config libcairo2-dev \
    libgirepository-2.0-dev python3-dev
uv sync --all-extras --all-groups --frozen --allow-python-downloads

uv run ruff format
uv run ruff check . --fix
uv run ty check
```

# Tests

The default suite runs offline:

```sh
./run-in-container.sh python3 -m unittest discover
./run-in-container.sh python3 -m unittest tests.test_htmlchecker
```

For native tests on Debian trixie, install all packages in `dependencies.apt.txt`
and run `/usr/bin/python3 -m unittest discover`. The list includes `python3-apt`
for Debian metadata, GI bindings and GLib/JSON-GLib typelibs for manifest parsing,
and `python3-magic` for content detection. Debian installs their native libraries
and the libmagic database as dependencies.

`uv sync` does not install python-apt. Its compiled modules must match the
distribution's Python ABI, so a uv-managed Python is not a substitute. Use the
container if your distribution lacks these packages. The helper uses the
published image; rebuild locally when testing Dockerfile or package-list changes.

To test against live upstream services:

```sh
./run-in-container.sh env FEDC_TEST_UPSTREAM=1 python3 -m unittest discover
```

CI runs offline with a five-minute limit. Enable `upstream-tests` in the manual
Tests workflow to run live tests. Their 60-second timeout cannot interrupt
blocking native calls.

Keep new tests offline. Use `tests/support.py` for a local HTTP server with a
five-second request timeout.

# JSON schemas

Use the [v1 report schema](data/report.v1.schema.json) for `schema_version: 1`.
The [v1 source-state schema](data/source-state.v1.schema.json) covers report
snapshots and the JSON checker's `$parent.current` and `$parent.new` values,
not the enclosing object. Unknown metadata and absent candidates are `null`.

Both schemas use JSON Schema Draft 2020-12. For offline validation, register both
schemas by `$id`. Enable format checking for report timestamps and JSON Pointers.
Source-state timestamps use Python's ISO format and need not conform to RFC 3339.

For incompatible changes, add new schema files and increment `schema_version`.
Readers should reject unsupported versions. Retain older schemas, including the
unversioned [source-state schema](data/source-state.schema.json).
