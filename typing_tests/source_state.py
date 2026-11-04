from typing_extensions import assert_type

from src.checkers import Checker
from src.lib.externaldata import (
    ArchiveSource,
    BuilderSource,
    ExternalBase,
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
    ExtraDataSource,
    FileSource,
)


def source_factories() -> None:
    file_source = {
        "type": "file",
        "url": "https://example.com/file",
        "sha256": "abc",
    }
    git_source = {"type": "git", "url": "https://example.com/repo.git"}
    assert_type(BuilderSource.from_source("source.json", file_source), BuilderSource)
    assert_type(ExternalBase.from_source("source.json", file_source), ExternalBase)
    assert_type(ExternalData.from_source("source.json", file_source), ExternalData)
    assert_type(FileSource.from_source("source.json", file_source), FileSource)
    assert_type(FileSource.from_source_impl("source.json", file_source), FileSource)
    assert_type(
        ArchiveSource.from_source("source.json", file_source | {"type": "archive"}),
        ArchiveSource,
    )
    assert_type(
        ExtraDataSource.from_source(
            "source.json", file_source | {"type": "extra-data"}
        ),
        ExtraDataSource,
    )
    assert_type(ExternalGitRepo.from_source("source.json", git_source), ExternalGitRepo)
    assert_type(
        ExternalGitRepo.from_source_impl("source.json", git_source), ExternalGitRepo
    )
    assert_type(
        ExternalData.data_classes(),
        dict[BuilderSource.Type, type[ExternalData]],
    )


async def checker_states(
    checker: Checker,
    data: ExternalData,
    repo: ExternalGitRepo,
) -> None:
    assert_type(data.current_version, ExternalFile)
    assert_type(data.new_version, ExternalFile | None)
    assert_type(repo.current_version, ExternalGitRef)
    assert_type(repo.new_version, ExternalGitRef | None)
    new_file = data.current_version._replace(version="2", size=123)
    new_ref = repo.current_version._replace(tag="v2", commit="abc")
    assert_type(new_file, ExternalFile)
    assert_type(new_ref, ExternalGitRef)
    assert_type(data.current_version.matches(new_file), bool)
    assert_type(data.current_version.is_same_version(new_file), bool)
    assert_type(repo.current_version.matches(new_ref), bool)
    assert_type(repo.current_version.is_same_version(new_ref), bool)
    assert_type(data.set_new_version(new_file), None)
    assert_type(repo.set_new_version(new_ref), None)
    assert_type(await checker.check(data), None)
    assert_type(await checker.validate_checker_data(data), None)
    assert_type(await checker._set_new_version(data, new_file), None)
    assert_type(await checker._set_new_version(repo, new_ref), None)


class CustomFileSource(FileSource):
    pass


def subclass_factory() -> None:
    assert_type(
        CustomFileSource.from_source_impl(
            "source.json",
            {"type": "file", "url": "https://example.com/file", "sha256": "abc"},
        ),
        CustomFileSource,
    )
