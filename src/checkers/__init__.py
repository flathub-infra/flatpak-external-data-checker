from .debianrepochecker import DebianRepoChecker
from .firefoxchecker import FirefoxChecker
from .flashchecker import FlashChecker
from .urlchecker import URLChecker
from .htmlchecker import HTMLChecker


# For each ExternalData, checkers are run in the order listed here, stopping once data.state is
# set to something other than UNKNOWN.
ALL_CHECKERS = [
    DebianRepoChecker,
    FirefoxChecker,
    FlashChecker,
    HTMLChecker,
    URLChecker,  # leave this last
]
