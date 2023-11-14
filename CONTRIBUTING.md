# Code style

All Python code should be formatted by [Black](https://github.com/psf/black).
We don't make the rules! Install Black, then run:

```
black .
```

# Running tests

There is a moderately-comprehensive test suite. Currently, it requires an
internet connection and takes a few minutes to run. 

```bash
# Run all the tests (some of which need an internet connection):
python3 -m unittest discover

# Run one suite of tests
python3 -m unittest tests.test_appdata

# More information
python3 -m unittest --help
```

# Dependencies

See the `Dockerfile` for the Debian and PyPI dependencies. unappimage is
optional, as is bubblewrap: we recommend one or the other.

## Using a `podman` container

The easiest way to get all the dependencies this tool needs is to build & run
the container image specified in the `Dockerfile`. There's a wrapper script,
whose only dependency is the `podman` command:

```bash
# Run all the tests (some of which need an internet connection):
./run-in-container.sh python3 -m unittest discover

# Run one suite of tests
./run-in-container.sh python3 -m unittest tests.test_appdata

# More information
./run-in-container.sh python3 -m unittest --help
```
