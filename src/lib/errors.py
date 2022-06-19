import typing as t


class FlatpakExternalDataCheckerError(Exception):
    """Base class for errors in the proram"""

    def __init__(self, message: t.Optional[str] = None):
        super().__init__(message)
        self.message = message or self.__doc__

    def __str__(self):
        if self.__cause__ is not None:
            return f"{self.message}: {self.__cause__}"
        return self.message


class ManifestError(FlatpakExternalDataCheckerError):
    """Error processing flatpak-builder manifest"""


class ManifestLoadError(ManifestError):
    """Error loading flatpak-builder manifest"""


class ManifestFileTooLarge(ManifestLoadError):
    """Manifest file size is too big"""


class ManifestUpdateError(ManifestError):
    """Error updating flatpak-builder manifest"""


class SourceLoadError(ManifestLoadError):
    """Error loading flatpak-builder source item"""


class SourceUnsupported(SourceLoadError):
    """Don't know how to handle flatpak-builder source item"""


class SourceUpdateError(ManifestUpdateError):
    """Error updating flatpak-builder source"""


class AppdataError(ManifestError):
    """Error processing metainfo.xml"""


class AppdataLoadError(AppdataError, ManifestLoadError):
    """Error loading metainfo.xml"""


class AppdataNotFound(AppdataLoadError):
    """Can't find metainfo.xml"""


class AppdataUpdateError(AppdataError, ManifestUpdateError):
    """Error updating metainfo.xml"""


class CheckerError(FlatpakExternalDataCheckerError):
    """Error checking a flatpak-builder source"""


class CheckerMetadataError(CheckerError):
    """Error processing checker metadata"""


class CheckerRemoteError(CheckerError):
    """Error processing remote data"""


class CheckerQueryError(CheckerRemoteError):
    """Error querying for new versions"""


class CheckerFetchError(CheckerRemoteError):
    """Error downloading upstream source"""
