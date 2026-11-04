# Checks

```sh
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
