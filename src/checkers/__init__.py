from .debianrepochecker import DebianRepoChecker
from .firefoxchecker import FirefoxChecker
from .flashchecker import FlashChecker
from .urlchecker import URLChecker


ALL_CHECKERS = [
    DebianRepoChecker,
    FirefoxChecker,
    FlashChecker,
    URLChecker,
]
