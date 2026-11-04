# Native prerequisites

CI uses Debian trixie. From the repository root, install its runtime packages:

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

For a native run, install the runtime dependencies, including Python APT bindings,
then run `python3 -m unittest discover`. The uv environment alone is not enough.

To test against live upstream services:

```sh
./run-in-container.sh env FEDC_TEST_UPSTREAM=1 python3 -m unittest discover
```

CI runs offline with a five-minute limit. Enable `upstream-tests` in the manual
Tests workflow to run live tests. Their 60-second timeout cannot interrupt
blocking native calls.

Keep new tests offline. Use `tests/support.py` for a local HTTP server with a
five-second request timeout.
