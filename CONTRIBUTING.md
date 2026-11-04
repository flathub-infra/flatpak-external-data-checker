# Code style

```
uv sync --all-extras --all-groups --frozen --allow-python-downloads

uv run ruff format
uv run ruff check . --fix
uv run mypy .
```

# Running tests

The default suite needs no upstream services. Run it in the container:

```bash
./run-in-container.sh python3 -m unittest discover

# Run one test module
./run-in-container.sh python3 -m unittest tests.test_htmlchecker
```

For a native run, use `python3 -m unittest discover`. Install the runtime
dependencies first, including Python APT bindings. The uv tooling alone is not enough.

Live upstream tests require `FEDC_TEST_UPSTREAM=1` inside the container:

```bash
./run-in-container.sh env FEDC_TEST_UPSTREAM=1 python3 -m unittest discover
```

Live tests can fail when upstream services change or go down. Each has a
60-second async timeout, which cannot interrupt blocking native calls.
CI runs the default suite with `--network=none` and a five-minute limit.
To run live tests in CI, enable `upstream-tests` in the manual Tests workflow.

Keep new tests offline. Use local servers, local Git repositories, or mocks.
`tests/support.py` has a local HTTP fixture with a five-second request timeout.
