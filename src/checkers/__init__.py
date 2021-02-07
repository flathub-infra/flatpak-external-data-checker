from .debianrepochecker import DebianRepoChecker
from .jsonchecker import JSONChecker
from .urlchecker import URLChecker
from .htmlchecker import HTMLChecker
from .jetbrainschecker import JetBrainsChecker
from .snapcraftchecker import SnapcraftChecker
from .anityachecker import AnityaChecker
from .rustchecker import RustChecker
from .gitchecker import GitChecker


# For each ExternalData, checkers are run in the order listed here, stopping once data.state is
# set to something other than UNKNOWN.
ALL_CHECKERS = [
    DebianRepoChecker,
    HTMLChecker,
    JetBrainsChecker,
    SnapcraftChecker,
    AnityaChecker,
    RustChecker,
    JSONChecker,
    GitChecker,  # leave this last but one
    URLChecker,  # leave this last
]
