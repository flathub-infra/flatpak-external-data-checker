from .debianrepochecker import DebianRepoChecker
from .firefoxchecker import FirefoxChecker
from .urlchecker import URLChecker
from .htmlchecker import HTMLChecker
from .jetbrainschecker import JetBrainsChecker
from .snapcraftchecker import SnapcraftChecker
from .anityachecker import AnityaChecker
from .rustchecker import RustChecker


# For each ExternalData, checkers are run in the order listed here, stopping once data.state is
# set to something other than UNKNOWN.
ALL_CHECKERS = [
    DebianRepoChecker,
    FirefoxChecker,
    HTMLChecker,
    JetBrainsChecker,
    SnapcraftChecker,
    AnityaChecker,
    RustChecker,
    URLChecker,  # leave this last
]
