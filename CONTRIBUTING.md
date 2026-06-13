# Code style

```
uv sync --all-extras --all-groups --frozen --allow-python-downloads

uv run ruff format
uv run ruff check . --fix
uv run mypy .
```

# Running tests

Currently the test suite requires, network connection and may take a
while to run. All new tests should try to work in a non-networked
setup where possible or only depend on external URLs from GitHub.

The tests can be run in the Docker image:

```bash
# Run all the tests
./run-in-container.sh python3 -m unittest discover

# Run one of the tests test_htmlchecker
./run-in-container.sh python3 -m unittest tests.test_htmlchecker

# Run a particular test class
./run-in-container.sh python3 -m unittest -vvv tests.test_anityachecker.TestParseTimestamp
```
