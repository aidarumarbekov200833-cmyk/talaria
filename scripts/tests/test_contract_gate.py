"""Focused contracts for the baseline checkpoint tools."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import contract_gate


class DomContractTests(unittest.TestCase):
    def test_capture_reads_original_html_from_zip_and_preserves_contract_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "baseline.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(
                    "Zephyr_latest_2026-08-21/frontend/index.html",
                    '<button id="save" class="primary wide" name="offer" '
                    'data-act="save" data-args=\'[1]\' data-target="modal" '
                    'data-telegram-login-trigger></button>',
                )

            records = contract_gate.capture_dom_from_zip(archive)

        self.assertEqual(
            records,
            [
                {
                    "tag": "button",
                    "id": "save",
                    "classes": ["primary", "wide"],
                    "name": "offer",
                    "data-act": "save",
                    "data-args": "[1]",
                    "data-target": "modal",
                    "data-telegram-login-trigger": {"present": True, "value": None},
                }
            ],
        )

    def test_check_allows_reordering_wrappers_and_extra_classes_but_rejects_missing_contract(self) -> None:
        baseline = [
            {"tag": "button", "id": "save", "classes": ["primary"], "data-act": "save"},
            {"tag": "input", "name": "amount", "classes": []},
        ]
        compatible = (
            '<section><input name="amount" class="new"></section>'
            '<button id="save" class="primary new" data-act="save"></button>'
        )
        incompatible = '<button id="save" class="primary"></button><input name="amount">'

        self.assertEqual(contract_gate.check_dom_contract(baseline, compatible), [])
        self.assertIn("data-act", "\n".join(contract_gate.check_dom_contract(baseline, incompatible)))


class OpenApiContractTests(unittest.TestCase):
    def test_normalization_preserves_incompatible_schema_and_operation_mutations(self) -> None:
        baseline = {
            "paths": {
                "/x": {
                    "parameters": [{"name": "a", "in": "query"}],
                    "post": {
                        "security": [{"token": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "default": 1,
                                        "deprecated": True,
                                        "prefixItems": [
                                            {"type": "string"},
                                            {"type": "integer"},
                                        ],
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"headers": {"X": {"schema": {"type": "string"}}}}},
                    },
                }
            },
            "components": {"schemas": {"X": {"type": "string"}}},
        }
        changed = json.loads(json.dumps(baseline))
        changed["paths"]["/x"]["post"]["requestBody"]["required"] = False
        self.assertNotEqual(contract_gate.normalize_openapi(baseline), contract_gate.normalize_openapi(changed))
        self.assertEqual(contract_gate.normalize_openapi(baseline)["paths"]["/x"]["post"]["requestBody"]["required"], True)
        for mutate in (
            lambda value: value["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"]["schema"].update(default=2),
            lambda value: value["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"]["schema"].update(deprecated=False),
            lambda value: value["paths"]["/x"]["parameters"].append({"name": "b", "in": "query"}),
            lambda value: value["paths"]["/x"]["post"]["responses"]["200"].update(headers={}),
            lambda value: value["paths"]["/x"]["post"].update(security=[]),
            lambda value: value["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"]["schema"].update(prefixItems=[{"type": "integer"}, {"type": "string"}]),
        ):
            mutation = json.loads(json.dumps(baseline))
            mutate(mutation)
            self.assertNotEqual(contract_gate.normalize_openapi(baseline), contract_gate.normalize_openapi(mutation))

    def test_normalization_ignores_only_documentation_and_volatile_metadata(self) -> None:
        document = {
            "openapi": "3.1.0",
            "info": {"title": "Zephyr", "version": "2026.8", "description": "docs"},
            "servers": [{"url": "http://localhost"}],
            "components": {
                "schemas": {
                    "Item": {"title": "Item", "type": "object", "properties": {"id": {"type": "integer"}}}
                }
            },
            "paths": {
                "/items": {
                    "get": {
                        "summary": "list items",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "title": "Items",
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }

        normalized = contract_gate.normalize_openapi(document)

        self.assertEqual(
            normalized["paths"]["/items"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"],
            {"items": {"type": "string"}, "title": "Items", "type": "array"},
        )
        self.assertEqual(normalized["servers"], [{"url": "http://localhost"}])
        self.assertEqual(normalized["info"]["description"], "docs")
        self.assertEqual(normalized["components"]["schemas"]["Item"]["properties"]["id"], {"type": "integer"})


class ActionAndAlembicContractTests(unittest.TestCase):
    def test_action_capture_is_deterministically_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text(
                '<button data-act="beta"></button><button data-act="alpha"></button>',
                encoding="utf-8",
                newline="\n",
            )
            source.write_text(
                "Object.assign(window, { beta, alpha });",
                encoding="utf-8",
                newline="\n",
            )

            first = contract_gate.capture_actions(html, [source])
            second = contract_gate.capture_actions(html, [source])

        self.assertEqual(first, second)
        self.assertEqual(first["references"], ["alpha", "beta"])
        self.assertEqual(first["published"], ["alpha", "beta"])

    def test_action_check_reports_unresolved_html_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text('<button data-act="save"></button>', encoding="utf-8", newline="\n")
            source.write_text("window.open = () => {};", encoding="utf-8", newline="\n")

            errors = contract_gate.check_actions(html, [source])

        self.assertEqual(errors, ["unresolved action: save"])

    def test_action_check_detects_js_template_typo_and_unbound_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text("<div></div>", encoding="utf-8", newline="\n")
            source.write_text(
                'const template = \'<button data-act="typo"></button>\';\n'
                "Object.assign(window, { missing });",
                encoding="utf-8",
                newline="\n",
            )
            errors = contract_gate.check_actions(html, [source])
        self.assertIn("unresolved action: typo", errors)
        self.assertIn("unbound published action: missing (missing)", errors)

    def test_action_binding_ignores_comment_and_rejects_alias_to_missing_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text("", encoding="utf-8", newline="\n")
            source.write_text(
                "// missing\nconst text = 'missing';\nfunction save() {}\n"
                "Object.assign(window, { save: missing });",
                encoding="utf-8",
                newline="\n",
            )
            errors = contract_gate.check_actions(html, [source])
        self.assertEqual(errors, ["unbound published action: save (missing)"])

    def test_action_binding_rejects_non_identifier_property_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text("", encoding="utf-8", newline="\n")
            source.write_text("Object.assign(window, { save: handlers.missing });", encoding="utf-8", newline="\n")
            errors = contract_gate.check_actions(html, [source])
        self.assertEqual(errors, ["unbound published action: save (handlers.missing)"])

    def test_added_resolved_action_is_compatible_with_baseline_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "index.html"
            source = root / "main.js"
            html.write_text('<button data-act="base"></button>', encoding="utf-8", newline="\n")
            source.write_text(
                'function base() {}\nfunction added() {}\n'
                "Object.assign(window, { base, added });",
                encoding="utf-8",
                newline="\n",
            )
            current = contract_gate.capture_actions(html, [source])
        baseline = {"references": ["base"], "published": ["base"]}
        self.assertFalse(set(baseline["references"]) - set(current["references"]))
        self.assertFalse(set(baseline["published"]) - set(current["published"]))

    def test_alembic_capture_finds_single_head_and_index_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            versions = Path(temp_dir)
            (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8", newline="\n")
            (versions / "b.py").write_text(
                'revision = "b"\ndown_revision = "a"\nop.create_index("ix_b", "t", ["x"])\n',
                encoding="utf-8",
                newline="\n",
            )

            manifest = contract_gate.capture_alembic(versions)

        self.assertEqual(manifest["heads"], ["b"])
        self.assertEqual(manifest["indexes"], ["ix_b"])
        self.assertEqual(len(manifest["schema_index_sha256"]), 64)
        self.assertEqual(len(manifest["schema_operations_sha256"]), 64)

    def test_alembic_fingerprint_changes_for_same_index_name_with_different_columns(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            for directory, column in ((first_dir, "a"), (second_dir, "b")):
                Path(directory, "a.py").write_text(
                    f'revision = "a"\ndown_revision = None\nop.create_index("ix_same", "t", ["{column}"], unique=True)\n',
                    encoding="utf-8",
                    newline="\n",
                )
            first = contract_gate.capture_alembic(Path(first_dir))
            second = contract_gate.capture_alembic(Path(second_dir))
        self.assertNotEqual(first["schema_index_sha256"], second["schema_index_sha256"])

    def test_alembic_operations_fingerprint_changes_for_non_index_schema_operation(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            for directory, operation in ((first_dir, 'op.add_column("t", sa.Column("a", sa.Integer()))'), (second_dir, 'op.add_column("t", sa.Column("b", sa.Integer()))')):
                Path(directory, "a.py").write_text(
                    f'revision = "a"\ndown_revision = None\ndef upgrade():\n    {operation}\n',
                    encoding="utf-8",
                    newline="\n",
                )
            first = contract_gate.capture_alembic(Path(first_dir))
            second = contract_gate.capture_alembic(Path(second_dir))
        self.assertNotEqual(first["schema_operations_sha256"], second["schema_operations_sha256"])

    def test_alembic_operations_fingerprint_includes_helper_calls(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            for directory, column in ((first_dir, "a"), (second_dir, "b")):
                Path(directory, "a.py").write_text(
                    f'revision = "a"\ndown_revision = None\ndef helper():\n    op.add_column("t", sa.Column("{column}", sa.Integer()))\ndef upgrade():\n    helper()\n',
                    encoding="utf-8",
                    newline="\n",
                )
            first = contract_gate.capture_alembic(Path(first_dir))
            second = contract_gate.capture_alembic(Path(second_dir))
        self.assertNotEqual(first["schema_operations_sha256"], second["schema_operations_sha256"])

    def test_alembic_operations_fingerprint_includes_helper_call_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            for directory, argument in ((first_dir, "x"), (second_dir, "y")):
                Path(directory, "a.py").write_text(
                    f'revision = "a"\ndown_revision = None\ndef helper(table, column):\n    op.add_column(table, sa.Column(column, sa.Integer()))\ndef upgrade():\n    helper("t", "{argument}")\n',
                    encoding="utf-8",
                    newline="\n",
                )
            first = contract_gate.capture_alembic(Path(first_dir))
            second = contract_gate.capture_alembic(Path(second_dir))
        self.assertNotEqual(first["schema_operations_sha256"], second["schema_operations_sha256"])


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_uses_lf_utf8_and_excludes_sensitive_and_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "keep.txt").write_text("ok", encoding="utf-8", newline="\n")
            (root / ".env").write_text("secret", encoding="utf-8", newline="\n")
            (root / ".env.local").write_text("secret", encoding="utf-8", newline="\n")
            (root / ".env.example").write_text("safe", encoding="utf-8", newline="\n")
            (root / "deploy").mkdir()
            (root / "deploy" / ".env.production.example").write_text("safe", encoding="utf-8", newline="\n")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("x", encoding="utf-8", newline="\n")
            (root / ".pnpm-store").mkdir()
            (root / ".pnpm-store" / "ignored.json").write_text("x", encoding="utf-8", newline="\n")
            (root / "playwright-report").mkdir()
            (root / "playwright-report" / "index.html").write_text("x", encoding="utf-8", newline="\n")
            (root / "nested").mkdir()
            (root / "nested" / ".env.production").write_text("secret", encoding="utf-8", newline="\n")
            (root / "test-venv").mkdir()
            (root / "test-venv" / "pyvenv.cfg").write_text("home=x", encoding="utf-8", newline="\n")
            (root / "test-venv" / "ignored.py").write_text("x", encoding="utf-8", newline="\n")
            output = root / "checkpoint.sha256"

            count = contract_gate.write_checkpoint(root, output)

            self.assertEqual(count, 3, output.read_text(encoding="utf-8"))
            self.assertEqual(output.read_bytes().count(b"\r\n"), 0)
            self.assertIn(b"  keep.txt\n", output.read_bytes())
            self.assertIn(b"  .env.example\n", output.read_bytes())
            self.assertIn(b"  deploy/.env.production.example\n", output.read_bytes())

    def test_checkpoint_does_not_inherit_exclusions_above_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="env-parent-") as temp_dir:
            parent = Path(temp_dir) / "env"
            root = parent / "project"
            root.mkdir(parents=True)
            (parent / "pyvenv.cfg").write_text("outside", encoding="utf-8", newline="\n")
            (root / "keep.txt").write_text("ok", encoding="utf-8", newline="\n")
            output = root / "checkpoint.sha256"
            contract_gate.write_checkpoint(root, output)
            self.assertIn(b"  keep.txt\n", output.read_bytes())


class CommandLineTests(unittest.TestCase):
    def test_openapi_capture_imports_backend_from_repository_root(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "openapi.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/contract_gate.py",
                    "openapi",
                    "capture",
                    "--app",
                    "backend.app.main:app",
                    "--output",
                    str(output),
                ],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("paths", json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
