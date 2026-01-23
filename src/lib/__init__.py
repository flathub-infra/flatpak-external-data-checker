import re
import operator

import aiohttp

TIMEOUT_CONNECT = 10
TIMEOUT_TOTAL = 60 * 10

# With the default urllib User-Agent, dl.discordapp.net returns 403
USER_AGENT = (
    "flatpak-external-data-checker/1.0 "
    "(+https://github.com/flathub-infra/flatpak-external-data-checker)"
)

HTTP_CLIENT_HEADERS = {"User-Agent": USER_AGENT}

HTTP_CHUNK_SIZE = 1024 * 64

NETWORK_ERRORS = (
    aiohttp.ClientError,
    aiohttp.ServerConnectionError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
)

WRONG_CONTENT_TYPES_FILE = [
    re.compile(r"^text/html$"),
    re.compile(r"^application/xhtml(\+.+)?$"),
]
WRONG_CONTENT_TYPES_ARCHIVE = [
    re.compile(r"^text/.*$"),
] + WRONG_CONTENT_TYPES_FILE

FILE_URL_SCHEMES = ["http", "https"]

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
