from .debianrepochecker import DebianRepoChecker
from .rotatingurlchecker import RotatingURLChecker
from .urlchecker import URLChecker


ALL_CHECKERS = [
    DebianRepoChecker,
    RotatingURLChecker,
    URLChecker,
]
