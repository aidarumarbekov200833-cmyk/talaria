#!/usr/bin/env python3
"""Capture and enforce the compatibility baselines used by Zephyr refactors.

The commands deliberately use only Python's standard library so they can run in
CI without adding a production dependency.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

CONTRACT_ATTRIBUTES = ("id", "name", "data-act", "data-args", "data-target")
ACTION_ATTRIBUTES = ("data-act", "data-change-act", "data-input-act", "data-enter-act")
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".env",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".pnpm-store",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "ms-playwright",
        "playwright-report",
        "test-results",
    }
)
OPENAPI_SET_LIST_KEYS = frozenset({"enum", "required"})


class StartTagCollector(HTMLParser):
    """Collect relevant attributes from every HTML start tag."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        record: dict[str, Any] = {"tag": tag, "classes": values.get("class", "").split()}
        for attribute in CONTRACT_ATTRIBUTES:
            if attribute in values:
                record[attribute] = values[attribute]
        if "data-telegram-login-trigger" in values:
            record["data-telegram-login-trigger"] = {
                "present": True,
                "value": values["data-telegram-login-trigger"],
            }
        self.records.append(record)


def parse_html_records(html: str) -> list[dict[str, Any]]:
    collector = StartTagCollector()
    collector.feed(html)
    collector.close()
    return collector.records


def capture_dom_from_zip(archive: Path, member: str = "frontend/index.html") -> list[dict[str, Any]]:
    """Read the original DOM contract from the external source archive."""
    with zipfile.ZipFile(archive) as bundle:
        matches = [name for name in bundle.namelist() if name == member or name.endswith(f"/{member}")]
        if len(matches) != 1:
            raise ValueError(f"archive must contain exactly one {member}; found {len(matches)}")
        try:
            html = bundle.read(matches[0]).decode("utf-8")
        except KeyError as exc:
            raise ValueError(f"archive does not contain {member}") from exc
    return parse_html_records(html)


def _record_matches(baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if baseline["tag"] != candidate["tag"]:
        return False
    if not set(baseline.get("classes", [])).issubset(candidate.get("classes", [])):
        return False
    for attribute in CONTRACT_ATTRIBUTES:
        if attribute in baseline and baseline[attribute] != candidate.get(attribute):
            return False
    if "data-telegram-login-trigger" in baseline:
        if candidate.get("data-telegram-login-trigger") != baseline["data-telegram-login-trigger"]:
            return False
    return True


def check_dom_contract(baseline: list[dict[str, Any]], html: str) -> list[str]:
    """Return violations after injectively matching baseline records to current DOM."""
    current = parse_html_records(html)
    candidates = [
        [index for index, record in enumerate(current) if _record_matches(expected, record)]
        for expected in baseline
    ]
    assigned: dict[int, int] = {}

    def assign(expected_index: int, seen: set[int]) -> bool:
        for current_index in candidates[expected_index]:
            if current_index in seen:
                continue
            seen.add(current_index)
            prior = assigned.get(current_index)
            if prior is None or assign(prior, seen):
                assigned[current_index] = expected_index
                return True
        return False

    errors = []
    for index, expected in enumerate(baseline):
        if not assign(index, set()):
            fields = [key for key in expected if key not in {"tag", "classes"}]
            detail = ", ".join(fields or ["class tokens"])
            errors.append(f"missing DOM contract #{index}: <{expected['tag']}> ({detail})")
    return errors


def _normalize_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key in sorted(value):
        normalized = _normalize_schema(value[key])
        if key in OPENAPI_SET_LIST_KEYS and isinstance(normalized, list):
            normalized = sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        result[key] = normalized
    return result


def normalize_openapi(document: dict[str, Any]) -> dict[str, Any]:
    """Keep API path, method, response and schema shape while dropping docs noise."""
    return _normalize_schema(document)


def _normalize_operation(operation: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "parameters" in operation:
        result["parameters"] = _normalize_schema(operation["parameters"])
    if "requestBody" in operation:
        result["requestBody"] = _normalize_content(operation["requestBody"])
    responses: dict[str, Any] = {}
    for status, response in sorted(operation.get("responses", {}).items()):
        normalized_response: dict[str, Any] = {}
        if "content" in response:
            normalized_response["content"] = _normalize_content(response["content"])
        responses[str(status)] = normalized_response
    result["responses"] = responses
    return result


def _normalize_content(value: dict[str, Any]) -> dict[str, Any]:
    content = value.get("content", value)
    result: dict[str, Any] = {}
    for media_type, media in sorted(content.items()):
        if isinstance(media, dict) and "schema" in media:
            result[media_type] = {"schema": _normalize_schema(media["schema"])}
        else:
            result[media_type] = _normalize_schema(media)
    return result


def _load_app(target: str) -> Any:
    module_name, separator, attribute = target.partition(":")
    if not separator:
        raise ValueError("application target must be MODULE:ATTRIBUTE")
    return getattr(importlib.import_module(module_name), attribute)


def capture_openapi(target: str) -> dict[str, Any]:
    app = _load_app(target)
    return normalize_openapi(app.openapi())


def _action_references(html: str) -> list[str]:
    references: list[str] = []
    for attribute in ACTION_ATTRIBUTES:
        references.extend(re.findall(rf"\b{re.escape(attribute)}\s*=\s*['\"]([^'\"]+)['\"]", html))
    return sorted(set(references))


def _published_actions(sources: list[Path]) -> list[str]:
    names: set[str] = set()
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for block in re.findall(r"\bObject\.assign\(\s*window\s*,\s*\{(.*?)\}\s*\)", text, re.DOTALL):
            block = re.sub(r"//[^\n]*", "", block)
            names.update(re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*(?=[:,]|$)", block))
    return sorted(names)


def _binding_names(text: str) -> set[str]:
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.DOTALL)
    names = set(re.findall(r"\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)\b", text))
    for block in re.findall(r"\bimport\s*\{(.*?)\}\s*from\b", text, re.DOTALL):
        for item in block.split(","):
            binding = item.strip().split()
            if binding:
                names.add(binding[-1] if len(binding) >= 3 and binding[-2] == "as" else binding[0])
    return names


def _window_assignments(text: str) -> list[tuple[str, str]]:
    assignments = []
    for block in re.findall(r"\bObject\.assign\(\s*window\s*,\s*\{(.*?)\}\s*\)", text, re.DOTALL):
        block = re.sub(r"//[^\n]*|/\*.*?\*/", "", block, flags=re.DOTALL)
        for item in block.split(","):
            parts = item.strip().split(":", 1)
            key = parts[0].strip()
            value = parts[-1].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", key):
                assignments.append((key, value))
    return assignments


def _unbound_published_actions(sources: list[Path]) -> list[str]:
    errors: set[str] = set()
    for source in sources:
        text = source.read_text(encoding="utf-8")
        bindings = _binding_names(text)
        for key, value in _window_assignments(text):
            if not re.fullmatch(r"[A-Za-z_$][\w$]*", value) or value not in bindings:
                errors.add(f"unbound published action: {key} ({value})")
    return sorted(errors)


def capture_actions(html_path: Path, sources: list[Path]) -> dict[str, list[str]]:
    references = _action_references(html_path.read_text(encoding="utf-8"))
    for source in sources:
        references.extend(_action_references(source.read_text(encoding="utf-8")))
    return {
        "references": sorted(set(references)),
        "published": _published_actions(sources),
    }


def check_actions(html_path: Path, sources: list[Path]) -> list[str]:
    manifest = capture_actions(html_path, sources)
    published = set(manifest["published"])
    errors = [f"unresolved action: {name}" for name in manifest["references"] if name not in published]
    return errors + _unbound_published_actions(sources)


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(statement.value)
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            if statement.target.id == name and statement.value is not None:
                return ast.literal_eval(statement.value)
    return None


def capture_alembic(versions_dir: Path) -> dict[str, Any]:
    revisions: dict[str, str | tuple[str, ...] | None] = {}
    indexes: list[str] = []
    for migration in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(migration.read_text(encoding="utf-8"), filename=str(migration))
        revision = _literal_assignment(tree, "revision")
        if not isinstance(revision, str):
            continue
        revisions[revision] = _literal_assignment(tree, "down_revision")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "create_index" or not node.args:
                continue
            try:
                index = ast.literal_eval(node.args[0])
            except ValueError:
                continue
            if isinstance(index, str):
                indexes.append(index)
    referenced = {
        item for down_revision in revisions.values() for item in ((down_revision,) if isinstance(down_revision, str) else (down_revision or ()))
    }
    heads = sorted(set(revisions) - referenced)
    signatures = []
    operations = []
    for migration in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(migration.read_text(encoding="utf-8"), filename=str(migration))
        revision = _literal_assignment(tree, "revision")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_index":
                signatures.append({"revision": revision, "call": ast.dump(node, include_attributes=False)})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "op":
                    operations.append({"revision": revision, "call": ast.dump(node, include_attributes=False)})
        operations.append({"revision": revision, "migration_ast": ast.dump(tree, include_attributes=False)})
    fingerprint = hashlib.sha256(json.dumps(sorted(signatures, key=str), sort_keys=True).encode("utf-8")).hexdigest()
    operations_fingerprint = hashlib.sha256(json.dumps(sorted(operations, key=str), sort_keys=True).encode("utf-8")).hexdigest()
    return {"heads": heads, "indexes": sorted(indexes), "schema_index_sha256": fingerprint, "schema_operations_sha256": operations_fingerprint}


def _is_excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_PARTS or part.startswith(".venv") for part in relative.parts):
        return True
    if relative.name.startswith(".env") and not relative.name.endswith(".example"):
        return True
    parents = [root / parent for parent in (relative.parent, *relative.parents) if parent != Path(".")]
    return any((parent / "pyvenv.cfg").is_file() for parent in parents)


def write_checkpoint(root: Path, output: Path) -> int:
    root = root.resolve()
    output = output.resolve()
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.resolve() == output or _is_excluded(path, root):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return len(lines)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _fail(errors: list[str]) -> int:
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    groups = parser.add_subparsers(dest="contract", required=True)
    for name in ("dom", "openapi", "actions", "alembic"):
        group = groups.add_parser(name).add_subparsers(dest="command", required=True)
        capture = group.add_parser("capture")
        capture.add_argument("--output", type=Path, required=True)
        check = group.add_parser("check")
        check.add_argument("--manifest", type=Path, required=True)
        if name == "dom":
            capture.add_argument("--zip", dest="archive", type=Path, required=True)
            capture.add_argument("--member", default="frontend/index.html")
            check.add_argument("--html", type=Path, required=True)
        elif name == "openapi":
            capture.add_argument("--app", required=True)
            check.add_argument("--app", required=True)
        elif name == "actions":
            capture.add_argument("--html", type=Path, required=True)
            capture.add_argument("--source", type=Path, action="append", required=True)
            check.add_argument("--html", type=Path, required=True)
            check.add_argument("--source", type=Path, action="append", required=True)
        else:
            capture.add_argument("--versions", type=Path, required=True)
            check.add_argument("--versions", type=Path, required=True)
    checkpoint = groups.add_parser("checkpoint")
    checkpoint.add_argument("--root", type=Path, required=True)
    checkpoint.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.contract == "checkpoint":
        print(f"checkpoint files: {write_checkpoint(args.root, args.output)}")
        return 0
    if args.contract == "dom":
        if args.command == "capture":
            _write_json(args.output, capture_dom_from_zip(args.archive, args.member))
            return 0
        return _fail(check_dom_contract(_read_json(args.manifest), args.html.read_text(encoding="utf-8")))
    if args.contract == "openapi":
        current = capture_openapi(args.app)
    elif args.contract == "actions":
        current = capture_actions(args.html, args.source)
        if args.command == "check":
            errors = check_actions(args.html, args.source)
            baseline = _read_json(args.manifest)
            for key in ("references", "published"):
                missing = sorted(set(baseline[key]) - set(current[key]))
                if missing:
                    errors.append(f"missing baseline actions {key}: {', '.join(missing)}")
            return _fail(errors)
    else:
        current = capture_alembic(args.versions)
    if args.command == "capture":
        _write_json(args.output, current)
        return 0
    return _fail([] if _read_json(args.manifest) == current else [f"{args.contract} contract changed"])


if __name__ == "__main__":
    raise SystemExit(main())
