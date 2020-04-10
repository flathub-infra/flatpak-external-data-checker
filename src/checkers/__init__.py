from .debianrepochecker import DebianRepoChecker
from .firefoxchecker import FirefoxChecker
from .flashchecker import FlashChecker
from .urlchecker import URLChecker
from .htmlchecker import HTMLChecker
from .jetbrainschecker import JetBrainsChecker
from .snapcraftchecker import SnapcraftChecker


# For each ExternalData, checkers are run in the order listed here, stopping once data.state is
# set to something other than UNKNOWN.
ALL_CHECKERS = [
    DebianRepoChecker,
    FirefoxChecker,
    FlashChecker,
    HTMLChecker,
    JetBrainsChecker,
    SnapcraftChecker,
    URLChecker,  # leave this last
]
