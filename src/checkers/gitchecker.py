import logging
import subprocess
from src.lib.externaldata import Checker, ExternalGitRepo

log = logging.getLogger(__name__)


class GitChecker(Checker):
    CHECKER_DATA_TYPE = "git"
    SUPPORTED_DATA_CLASSES = [ExternalGitRepo]

    def should_check(self, external_data):
        return isinstance(external_data, ExternalGitRepo)

    def check(self, external_data):
        assert self.should_check(external_data)
        external_data: ExternalGitRepo

        if (
            external_data.current_version.commit is not None
            and external_data.current_version.tag is None
            and external_data.current_version.branch is None
        ):
            log.info(
                "Skipping source %s, commit is specified, but neither tag nor branch",
                external_data.filename,
            )
            return

        if (
            external_data.current_version.commit is None
            and external_data.current_version.tag is None
        ):
            log.info(
                "Skipping source %s, not pinned to tag or commit",
                external_data.filename,
            )
            return

        try:
            remote_version = external_data.current_version.fetch_remote()
        except KeyError as err:
            log.error(
                "Couldn't get remote commit from %s: not found %s",
                external_data.current_version.url,
                err,
            )
            external_data.state = ExternalGitRepo.State.BROKEN
            return

        if external_data.current_version.matches(remote_version):
            log.debug(
                "Remote git repo %s is still valid", external_data.current_version.url
            )
            external_data.state = ExternalGitRepo.State.VALID
        else:
            external_data.state = ExternalGitRepo.State.BROKEN
            external_data.new_version = remote_version
