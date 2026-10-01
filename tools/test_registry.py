"""The registry's checks, without the network.

    python3 -m unittest discover -s tools
"""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import registry

GOOD = """
id = "acme.cam"
name = "CAM"
description = "Toolpaths for milling"
repository = "acme/printcad-cam"
maintainers = ["acme-dev"]
license = "MIT"
categories = ["printing"]
"""


def entry_at(name: str, text: str) -> tuple[Path, dict]:
    folder = Path(tempfile.mkdtemp())
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path, registry.read_entry(path)


def archive(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


class Entries(unittest.TestCase):
    def test_a_good_entry_has_nothing_wrong(self):
        path, entry = entry_at("acme.cam.toml", GOOD)
        self.assertEqual(registry.entry_problems(path, entry), [])

    def test_the_file_is_named_after_the_id(self):
        path, entry = entry_at("cam.toml", GOOD)
        self.assertIn("the file should be named acme.cam.toml", registry.entry_problems(path, entry))

    def test_an_id_is_lowercase_and_reverse_domain(self):
        for bad in ["Acme.Cam", "cam", "acme..cam", "acme cam"]:
            path, entry = entry_at(f"{bad}.toml", GOOD.replace("acme.cam", bad))
            problems = registry.entry_problems(path, entry)
            self.assertTrue(any("not a package id" in p for p in problems), (bad, problems))

    def test_missing_unknown_and_mistyped_fields_are_named(self):
        path, entry = entry_at(
            "acme.cam.toml",
            GOOD.replace('license = "MIT"\n', "").replace('categories = ["printing"]', 'categories = "printing"\nstars = 5'),
        )
        problems = registry.entry_problems(path, entry)
        self.assertIn("`license` is missing", problems)
        self.assertIn("`categories` should be a list", problems)
        self.assertIn("unknown fields: stars", problems)

    def test_categories_logins_and_repositories_are_checked(self):
        path, entry = entry_at(
            "acme.cam.toml",
            GOOD.replace('["printing"]', '["cooking"]')
            .replace('["acme-dev"]', '["not a login"]')
            .replace("acme/printcad-cam", "https://github.com/acme/printcad-cam"),
        )
        problems = registry.entry_problems(path, entry)
        self.assertTrue(any("not a category" in p for p in problems))
        self.assertTrue(any("not a GitHub login" in p for p in problems))
        self.assertIn("`repository` should be `owner/name` on GitHub", problems)

    def test_a_removed_entry_says_why(self):
        path, entry = entry_at("acme.cam.toml", GOOD + 'removed = " "\n')
        self.assertIn("`removed` should say why", registry.entry_problems(path, entry))

    def test_an_id_or_a_repository_is_listed_once(self):
        first, _ = entry_at("acme.cam.toml", GOOD)
        second = first.parent / "acme.cnc.toml"
        second.write_text(GOOD.replace("acme.cam", "acme.cnc"), encoding="utf-8")
        problems = registry.registry_problems([first, second])
        self.assertTrue(any("acme/printcad-cam is listed by acme.cam.toml too" in p for p in problems))


class Releases(unittest.TestCase):
    def test_a_package_s_manifest_is_read_from_its_archive(self):
        data = archive({"./bench.toml": 'id = "acme.cam"\napi = "printcad:workbench@0.1"\n', "./bench.wasm": "\0asm"})
        self.assertEqual(registry.manifest_of(data)["id"], "acme.cam")

    def test_an_archive_without_its_component_or_manifest_is_refused(self):
        with self.assertRaisesRegex(registry.Problem, "no bench.wasm"):
            registry.manifest_of(archive({"bench.toml": 'id = "a.b"'}))
        with self.assertRaisesRegex(registry.Problem, "no bench.toml"):
            registry.manifest_of(archive({"bench.wasm": "\0asm"}))
        with self.assertRaisesRegex(registry.Problem, "not an archive"):
            registry.manifest_of(b"not a tar")

    def test_the_contract_matches_by_major_version(self):
        self.assertTrue(registry.api_compatible("printcad:workbench@0.1"))
        self.assertTrue(registry.api_compatible("printcad:workbench@0.1.3"))
        self.assertFalse(registry.api_compatible("printcad:workbench@0.2"))
        self.assertFalse(registry.api_compatible("printcad:workbench@1.0"))


class Listed(unittest.TestCase):
    def test_every_listed_entry_checks(self):
        paths = registry.all_entries()
        self.assertTrue(paths)
        for path in paths:
            self.assertEqual(registry.entry_problems(path, registry.read_entry(path)), [], path.name)
        self.assertEqual(registry.registry_problems(paths), [])


if __name__ == "__main__":
    unittest.main()
