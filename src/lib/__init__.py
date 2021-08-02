import operator

import aiohttp


TIMEOUT_CONNECT = 10
TIMEOUT_TOTAL = 60 * 10

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = (
    "flatpak-external-data-checker/1.0 "
    "(+https://github.com/flathub/flatpak-external-data-checker)"
)

HTTP_CLIENT_HEADERS = {"User-Agent": USER_AGENT}

HTTP_CHUNK_SIZE = 1024 * 64

NETWORK_ERRORS = (
    aiohttp.ClientError,
    aiohttp.ServerConnectionError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
)

OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
OPERATORS_SCHEMA = {
    "type": "object",
    "properties": {o: {"type": "string"} for o in list(OPERATORS)},
    "additionalProperties": False,
    "minProperties": 1,
}
