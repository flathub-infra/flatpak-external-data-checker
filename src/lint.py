import re
import subprocess
from pathlib import Path
from string import Template

import jsonschema
from gi.repository import GLib
from ruamel.yaml.error import YAMLError

from .checkers import ALL_CHECKERS
from .checkers.jsonchecker import JSONChecker
from .lib.errors import ManifestLoadError, SourceLoadError
from .lib.externaldata import CHECKER_DATA_SCHEMA_COMMON, ExternalBase, ExternalGitRepo
from .lib.utils import read_manifest
from .manifest import CheckerOptions, ManifestChecker


class ManifestLinter:
    def __init__(self, max_manifest_size: int):
        self.max_manifest_size = max_manifest_size
        self.errors: list[str] = []
        self._active: set[Path] = set()
        self._active_nodes: set[int] = set()
        self._external_sources: set[Path] = set()

    def _error(self, path: Path, location: str, message: str):
        self.errors.append(f"{path}:{location}: {message}")

    def lint(self, path: Path) -> list[str]:
        path = path.absolute()
        self._file(path, "root")
        if not self.errors:
            # The constructor validates parent relationships without network access.
            try:
                checker = ManifestChecker(
                    str(path), CheckerOptions(max_manifest_size=self.max_manifest_size)
                )
                for error in checker.get_errors():
                    self._error(path, "$", str(error))
            except (ManifestLoadError, TypeError, ValueError, KeyError) as err:
                self._error(path, "$", str(err))
        return self.errors

    def _file(self, path: Path, kind: str):
        try:
            resolved = path.resolve()
            if resolved in self._active:
                self._error(path, "$", "Circular manifest reference")
                return
            if path.stat().st_size > self.max_manifest_size:
                self._error(path, "$", "Manifest exceeds maximum file size")
                return
            contents = read_manifest(path)
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
            YAMLError,
            GLib.Error,
        ) as err:
            self._error(path, "$", str(err))
            return
        if isinstance(contents, tuple):
            contents = contents[0]
        if kind == "module" and not isinstance(contents, dict):
            self._error(path, "$", "Expected a module object")
            return
        self._active.add(resolved)
        if kind == "source":
            self._external_sources.add(path)
        try:
            if kind == "root":
                if isinstance(contents, list):
                    kind = "sources"
                elif isinstance(contents, dict):
                    if "app-id" in contents or "id" in contents:
                        kind = "app"
                    elif "name" in contents and (
                        "sources" in contents or "modules" in contents
                    ):
                        kind = "module"
                    elif "type" in contents:
                        kind = "source"
            self._walk(path, "$", contents, kind)
        finally:
            self._active.remove(resolved)
            self._external_sources.discard(path)

    def _walk(self, path: Path, location: str, value, kind: str):
        if isinstance(value, (dict, list)):
            ident = id(value)
            if ident in self._active_nodes:
                self._error(path, location, "Circular YAML alias")
                return
            self._active_nodes.add(ident)
            try:
                self._node(path, location, value, kind)
            finally:
                self._active_nodes.remove(ident)
        else:
            self._node(path, location, value, kind)

    def _node(self, path: Path, location: str, value, kind: str):
        if isinstance(value, str) and kind in ("module", "source"):
            if kind == "source" and path in self._external_sources:
                self._error(
                    path, location, "Nested external source manifests not allowed"
                )
                return
            self._file(path.parent / value, kind)
            return
        if kind in ("modules", "sources"):
            if kind == "sources" and isinstance(value, dict):
                self._node(path, location, value, "source")
                return
            if not isinstance(value, list):
                self._error(path, location, "Expected a list")
                return
            for index, item in enumerate(value):
                self._walk(path, f"{location}[{index}]", item, kind[:-1])
            return
        if kind == "source" and isinstance(value, list) and location == "$":
            self._node(path, location, value, "sources")
            return
        if not isinstance(value, dict):
            self._error(path, location, "Expected a manifest object")
            return
        if kind in ("module", "app"):
            if kind == "module" and not isinstance(value.get("name"), str):
                self._error(path, location + ".name", "Expected a module name")
            for field in ("modules", "sources"):
                if field in value:
                    self._walk(path, location + "." + field, value[field], field)
        elif kind == "source":
            self._source(path, location, value)
        else:
            self._error(path, location, "Can't determine manifest kind")

    def _schema(self, path: Path, location: str, value, schema: dict) -> bool:
        validator = jsonschema.validators.validator_for(schema)(schema)
        errors = list(validator.iter_errors(value))
        for error in errors:
            suffix = "".join(f"[{part!r}]" for part in error.absolute_path)
            self._error(path, location + suffix, error.message)
        return not errors

    def _source(self, path: Path, location: str, source: dict):
        if "x-checker-data" not in source:
            return
        location += ".x-checker-data"
        metadata = source["x-checker-data"]
        if not self._schema(path, location, metadata, CHECKER_DATA_SCHEMA_COMMON):
            return
        checker = next(
            (c for c in ALL_CHECKERS if c.CHECKER_DATA_TYPE == metadata["type"]),
            None,
        )
        if checker is None:
            self._error(
                path, location + ".type", f"Unknown checker {metadata['type']!r}"
            )
            return
        try:
            data = ExternalBase.from_source(str(path), source)
        except (SourceLoadError, ValueError, KeyError, TypeError) as err:
            self._error(path, location, str(err))
            return
        if not isinstance(data, tuple(checker.SUPPORTED_DATA_CLASSES)):
            self._error(
                path,
                location,
                f"Checker {metadata['type']!r} does not support {data.type}",
            )
            return
        if not self._schema(
            path, location, metadata, checker.get_json_schema(type(data))
        ):
            return
        for field, groups in (
            ("pattern", 2 if metadata["type"] == "html" else 1),
            ("version-pattern", 1),
            ("tag-pattern", 1),
        ):
            if field not in metadata:
                continue
            try:
                pattern = re.compile(metadata[field])
            except (re.error, TypeError) as err:
                self._error(path, location + "." + field, str(err))
            else:
                if pattern.groups != groups:
                    self._error(
                        path,
                        location + "." + field,
                        f"Expected {groups} capture group(s), got {pattern.groups}",
                    )
        if metadata["type"] == "json":
            self._queries(path, location, data)
        elif metadata["type"] == "anitya":
            field = (
                "tag-template" if isinstance(data, ExternalGitRepo) else "url-template"
            )
            self._version_template(path, location, metadata, field)
        elif metadata["type"] == "html" and "pattern" not in metadata:
            self._version_template(path, location, metadata, "url-template")

    def _version_template(self, path: Path, location: str, metadata: dict, field: str):
        template = Template(metadata[field])
        location += "." + field
        matches = list(template.pattern.finditer(template.template))
        if any(match.group("invalid") is not None for match in matches):
            self._error(
                path, location, "Invalid template placeholder; use $$ for a literal $"
            )
            return
        names = dict.fromkeys(
            name
            for match in matches
            if (name := match.group("named") or match.group("braced")) is not None
        )
        for name in names:
            if name in {"version", "major", "minor", "patch"} or re.fullmatch(
                r"version(?:0|[1-9][0-9]*)", name
            ):
                continue
            self._error(
                path,
                location,
                f"Unknown template variable {name!r}; use ${{version}} or "
                "${major}, ${minor}, ${patch}, ${versionN}. "
                "Use braces to separate variables from adjacent text",
            )

    def _queries(self, path: Path, location: str, data: ExternalBase):
        metadata = data.checker_data
        variables = ["parent"] if "parent-id" in metadata else []
        for query in JSONChecker.get_queries(data):
            expressions = [(f"{query.name}-query", query.value_expr)]
            if query.url_expr:
                expressions.insert(0, (f"{query.name}-data-url", query.url_expr))
            for field, expression in expressions:
                args = ["jq"]
                for variable in variables:
                    args.extend(["--argjson", variable, "null"])
                # Empty input compiles without executing the query.
                # The leading space stops jq treating the query as an option.
                args.append(" " + expression)
                try:
                    result = subprocess.run(
                        args,
                        input="",
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired) as err:
                    self._error(path, location + "." + field, str(err))
                else:
                    if result.returncode:
                        self._error(path, location + "." + field, result.stderr.strip())
            variables.append(query.name)
