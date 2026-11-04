import unittest

from src.lib.releaselinks import (
    api_release_url,
    download_comparison_url,
    download_release_url,
    github_release_api_url,
)


class TestReleaseLinks(unittest.TestCase):
    def test_download_comparison_with_release_route_in_namespace(self):
        for repo, download, suffix, compare in (
            (
                "https://gitlab.com/group/releases/tag/app",
                "-/releases",
                "/downloads",
                "-/compare",
            ),
            ("https://github.com/releases/tag", "releases/download", "", "compare"),
            ("https://codeberg.org/releases/tag", "releases/download", "", "compare"),
        ):
            with self.subTest(repo=repo):
                self.assertEqual(
                    download_comparison_url(
                        f"{repo}/{download}/release%2F1{suffix}/app.zip",
                        f"{repo}/{download}/release%2F2{suffix}/app.zip",
                    ),
                    f"{repo}/{compare}/release%2F1...release%2F2",
                )

    def test_github_release_api_url(self):
        for repo in (
            "https://github.com/Owner/App",
            "https://GitHub.COM/Owner/App.git",
            "https://github.com/Owner/App.git/",
        ):
            self.assertEqual(
                github_release_api_url(repo, "release/2%beta"),
                "https://api.github.com/repos/Owner/App/releases/tags/release%2F2%25beta",
            )
        for repo in (
            "https://example.org/Owner/App",
            "https://github.com.evil.test/Owner/App",
            "https://github.com@evil.test/Owner/App",
            "https://github.com/Owner/App/tree/main",
            "git@github.com:Owner/App.git",
            "https://[invalid",
        ):
            self.assertIsNone(github_release_api_url(repo, "v2"))

    def test_download_provider_hostnames_are_case_insensitive(self):
        for host, path, expected_path in (
            (
                "GitHub.COM",
                "/Owner/App/releases/download/v2/app.zip",
                "/Owner/App/releases/tag/v2",
            ),
            (
                "Codeberg.ORG",
                "/Owner/App/releases/download/v2/app.zip",
                "/Owner/App/releases/tag/v2",
            ),
            (
                "GitLab.COM",
                "/Owner/App/-/releases/v2/downloads/app.zip",
                "/Owner/App/-/releases/v2",
            ),
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    download_release_url(f"https://{host}{path}"),
                    f"https://{host.lower()}{expected_path}",
                )

    def test_github_api_hostname_is_case_insensitive(self):
        link = "https://github.com/Owner/App/releases/tag/v2"
        self.assertEqual(
            api_release_url(
                "https://API.GitHub.COM/repos/Owner/App/releases",
                {"tag_name": "v2", "html_url": link},
                tag="v2",
                asset_url=None,
                repository_url="https://github.com/owner/app.git",
            ),
            link,
        )

    def test_gitlab_api_matches_tags_archives_and_assets(self):
        release = {
            "tag_name": "v2",
            "_links": {"self": "https://git.example.org/team/app/-/releases/v2"},
            "assets": {
                "sources": [
                    {"url": "https://git.example.org/team/app/-/archive/v2/app.zip"}
                ],
                "links": [
                    {
                        "url": "https://cdn.example.org/app-2.zip",
                        "direct_asset_url": "https://git.example.org/team/app/-/releases/v2/downloads/app.zip",
                    }
                ],
            },
        }
        for project in ("123", "team%2Fapp"):
            api = f"https://git.example.org/gitlab/api/v4/projects/{project}/releases"
            for suffix, payload in (
                ("", [{"tag_name": "v3"}, release]),
                ("/v2", release),
                ("/permalink/latest", release),
            ):
                with self.subTest(project=project, suffix=suffix):
                    self.assertEqual(
                        api_release_url(
                            api + suffix, payload, tag="v2", asset_url=None
                        ),
                        release["_links"]["self"],
                    )
                    for asset in (
                        release["assets"]["sources"][0]["url"],
                        release["assets"]["links"][0]["url"],
                        release["assets"]["links"][0]["direct_asset_url"],
                    ):
                        self.assertEqual(
                            api_release_url(
                                api + suffix, payload, tag=None, asset_url=asset
                            ),
                            release["_links"]["self"],
                        )
                    self.assertIsNone(
                        api_release_url(api + suffix, payload, tag="v1", asset_url=None)
                    )

    def test_gitlab_api_missing_or_malformed_links(self):
        api = "https://gitlab.com/api/v4/projects/123/releases"
        for links in (None, [], {"self": "javascript:alert(1)"}, {}):
            self.assertIsNone(
                api_release_url(
                    api, {"tag_name": "v2", "_links": links}, tag="v2", asset_url=None
                )
            )
        self.assertIsNone(
            api_release_url(
                api,
                {"tag_name": "v2", "assets": {"sources": None, "links": [None]}},
                tag=None,
                asset_url="https://example.org/app.zip",
            )
        )

    def test_codeberg_download(self):
        self.assertEqual(
            download_release_url(
                "https://codeberg.org/forgejo/forgejo/releases/download/v17.0.1/forgejo.zip"
            ),
            "https://codeberg.org/forgejo/forgejo/releases/tag/v17.0.1",
        )
        self.assertIsNone(
            download_release_url(
                "https://codeberg.org/forgejo/forgejo/archive/v17.0.1.tar.gz"
            )
        )

    def test_forgejo_api_including_self_hosted(self):
        for base in ("https://codeberg.org", "https://git.example.org/forgejo"):
            api = f"{base}/api/v1/repos/team/app/releases"
            link = f"{base}/team/app/releases/tag/v2"
            archive = f"{base}/team/app/archive/v2.tar.gz"
            release = {"tag_name": "v2", "html_url": link, "tarball_url": archive}
            for endpoint, payload in (
                (api, [{"tag_name": "v3"}, release]),
                (api + "/latest", release),
                (api + "/tags/v2", release),
                (api + "/123", release),
            ):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(
                        api_release_url(endpoint, payload, tag="v2", asset_url=None),
                        link,
                    )
                    self.assertEqual(
                        api_release_url(endpoint, payload, tag=None, asset_url=archive),
                        link,
                    )
                    self.assertIsNone(
                        api_release_url(endpoint, payload, tag="v1", asset_url=None)
                    )

    def test_gitlab_download(self):
        self.assertEqual(
            download_release_url(
                "https://gitlab.com/group/subgroup/app/-/releases/v2%2Fstable/"
                "downloads/bin/app.zip?private_token=secret"
            ),
            "https://gitlab.com/group/subgroup/app/-/releases/v2%2Fstable",
        )
        for url in (
            "https://gitlab.com/group/app/-/releases/permalink/latest/downloads/app",
            "https://gitlab.com/group/app/-/archive/v2/app.tar.gz",
            "https://example.org/group/app/-/releases/v2/downloads/app",
        ):
            self.assertIsNone(download_release_url(url))

    def test_api_matches_selected_asset_not_latest_version(self):
        releases = [
            {"tag_name": "v3", "html_url": "https://github.com/o/r/releases/tag/v3"},
            {
                "tag_name": "project-v2",
                "html_url": "https://github.com/o/r/releases/tag/project-v2",
                "assets": [{"browser_download_url": "https://cdn.example.org/app.zip"}],
                "tarball_url": "https://api.github.com/repos/o/r/tarball/project-v2",
            },
        ]
        api = "https://api.github.com/repos/o/r/releases"
        for asset in (
            "https://cdn.example.org/app.zip",
            "https://api.github.com/repos/o/r/tarball/project-v2",
        ):
            self.assertEqual(
                api_release_url(api, releases, tag=None, asset_url=asset),
                releases[1]["html_url"],
            )
        self.assertEqual(
            api_release_url(api, releases, tag="project-v2", asset_url=None),
            releases[1]["html_url"],
        )
        self.assertIsNone(api_release_url(api, releases, tag="2", asset_url=None))
        self.assertIsNone(
            api_release_url(
                "https://example.org/releases",
                releases,
                tag="project-v2",
                asset_url=None,
            )
        )

    def test_api_ambiguous_and_malformed_records(self):
        api = "https://api.github.com/repos/o/r/releases"
        releases = [
            {"tag_name": "v2", "html_url": "https://example.org/one"},
            {"tag_name": "v2", "html_url": "https://example.org/two"},
        ]
        self.assertIsNone(api_release_url(api, releases, tag="v2", asset_url=None))
        for payload in (
            None,
            1,
            [],
            [None],
            {"tag_name": "v2", "html_url": "javascript:x"},
        ):
            self.assertIsNone(api_release_url(api, payload, tag="v2", asset_url=None))

    def test_tag_match_requires_source_repository(self):
        for base, api, path in (
            (
                "https://github.com",
                "https://api.github.com/repos/o/r/releases",
                "/o/r/releases/tag/v2",
            ),
            (
                "https://codeberg.org",
                "https://codeberg.org/api/v1/repos/o/r/releases",
                "/o/r/releases/tag/v2",
            ),
            (
                "https://git.example.org",
                "https://git.example.org/api/v4/projects/12/releases",
                "/o/r/-/releases/v2",
            ),
        ):
            link = base + path
            release = {"tag_name": "v2", "html_url": link, "_links": {"self": link}}
            for repo, expected in (
                (base + "/o/r.git", link),
                (base + "/o/r/", link),
                (base.upper() + "/o/r.git", link),
                (base + "/O/R.git", link if "github.com" in base else None),
                (base + "/o/other.git", None),
                ("https://other.example.org/o/r.git", None),
                ("git@codeberg.org:o/r.git", None),
            ):
                with self.subTest(repo=repo, api=api):
                    self.assertEqual(
                        api_release_url(
                            api, release, tag="v2", asset_url=None, repository_url=repo
                        ),
                        expected,
                    )

    def test_github_release_repository_casing(self):
        link = "https://GitHub.COM/Owner/App/releases/tag/v2"
        self.assertEqual(
            api_release_url(
                "https://api.github.com/repos/owner/app/releases",
                {"tag_name": "v2", "html_url": link},
                tag="v2",
                asset_url=None,
                repository_url="https://github.com/owner/app.git",
            ),
            link,
        )
