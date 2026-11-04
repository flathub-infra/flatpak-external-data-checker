"""Default discovery is offline; live upstream smoke tests require opt-in."""

import ipaddress
import os
import socket
import sys


def _offline_network_guard(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
        # A null host requests local wildcard/loopback addresses, not DNS.
        if host is None:
            return
    elif event == "socket.sendto" and args[0].family in (
        socket.AF_INET,
        socket.AF_INET6,
    ):
        host = args[2][0]
    elif event == "socket.connect" and args[0].family in (
        socket.AF_INET,
        socket.AF_INET6,
    ):
        host = args[1][0]
    else:
        return
    try:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if isinstance(host, str) and host.casefold() == "localhost":
            return
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise AssertionError(
        f"External network access in offline tests: {host!r}; use a local fixture "
        "or mark an upstream smoke test (FEDC_TEST_UPSTREAM=1)"
    )


if os.environ.get("FEDC_TEST_UPSTREAM") != "1":
    sys.addaudithook(_offline_network_guard)
    # The Python socket guard cannot cover git's subprocess transports.
    os.environ["GIT_ALLOW_PROTOCOL"] = "file"
