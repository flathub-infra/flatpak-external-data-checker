from .debianrepochecker import DebianRepoChecker
from .flashchecker import FlashChecker
from .urlchecker import URLChecker


ALL_CHECKERS = [
    DebianRepoChecker,
    FlashChecker,
    URLChecker,
]
