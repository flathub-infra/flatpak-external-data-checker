import typing as t


class FlatpakExternalDataCheckerError(Exception):
    """Base class for errors in the proram"""


class CheckerError(FlatpakExternalDataCheckerError):
    """Error checking a flatpak-builder source"""

    def __init__(self, message: t.Optional[str] = None):
        super().__init__(message)
        self.message = message or self.__doc__

    def __str__(self):
        if self.__cause__ is not None:
            return f"{self.message}: {self.__cause__}"
        return self.message


class CheckerMetadataError(CheckerError):
    """Error processing checker metadata"""


class CheckerRemoteError(CheckerError):
    """Error processing remote data"""


class CheckerQueryError(CheckerRemoteError):
    """Error querying for new versions"""


class CheckerFetchError(CheckerRemoteError):
    """Error downloading upstream source"""
