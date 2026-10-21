"""Only self-authored synthetic fixtures; no original game text or resources."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from core.runtime_paths import RuntimePaths
from core.scripts import parse_script
from core.translations import body_fields
from core.runtime_data import title_table
from core.script_compat import apply_compatibility


class ProgramTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows bootstrap uses Windows PowerShell")
    def test_windows_bootstrap_preserves_output_arguments_and_exit_status(self):
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        source = Path(__file__).resolve().parents[1] / "app"
        with tempfile.TemporaryDirectory(prefix="fkg bootstrap ") as directory:
            root = Path(directory)
            app = root / "app"
            app.mkdir()
            shutil.copyfile(source / "python-runtime.json", app / "python-runtime.json")
            script = root / "invoke.ps1"
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script.write_text(". " + quote(source / "bootstrap.ps1") + "\n"
                              "function Find-ReaderPython { return " + quote(sys.executable) + " }\n"
                              "exit (Invoke-ReaderBootstrap -Action Check -Root " + quote(root) +
                              " -ReaderArguments @('--include-content'))\n", encoding="utf-8-sig")
            for status in (0, 7):
                (app / "verify-release.py").write_text(
                    "import sys\nprint('Example output', flush=True)\n"
                    "assert sys.argv[1:] == ['--include-content']\nraise SystemExit(" + str(status) + ")\n",
                    encoding="utf-8")
                result = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertIn("Example output", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "Windows bootstrap uses Windows PowerShell")
    def test_windows_bootstrap_offline(self):
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = Path(__file__).with_name("test_bootstrap.ps1")
        result = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Offline bootstrap checks passed:", result.stdout)

    def test_parser_uses_synthetic_dialogue(self):
        payload = parse_script("mess,Example Speaker,An example sentence.\n")
        self.assertEqual(payload["commands"][0]["text"], "An example sentence.")
        self.assertEqual(body_fields({"script": payload})["mess:1:text"], "An example sentence.")

    def test_display_tables_are_optional_until_content_is_installed(self):
        with patch("core.runtime_data.tables", return_value={"chapter": {"synthetic-key": "Example"}}):
            self.assertEqual(title_table("chapter"), {"synthetic-key": "Example"})
        with patch("core.runtime_data.tables", return_value={}):
            self.assertEqual(title_table("chapter"), {})

    def test_program_has_no_developer_drive_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            app.mkdir()
            with self.assertRaises(FileNotFoundError):
                RuntimePaths(app)
            self.assertFalse((app.parent / "userdata").exists())

    def test_paths_use_only_a_synthetic_local_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); app = root / "app"; app.mkdir()
            catalog = root / "content/catalog"; catalog.mkdir(parents=True)
            for name in ("display-titles.json", "script-compat-profiles.json"):
                (catalog / name).write_text("{}", encoding="utf-8")
            manifest = {"kind": "fkg-reader-content", "formatVersion": 1}
            (root / "content/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            p = RuntimePaths(app)
            self.assertEqual(p.story_root, root / "content/stories")
            self.assertEqual(p.user_root, root / "userdata")
            self.assertEqual(p.config["storyRoot"], "content/stories")
            self.assertFalse((root / "userdata").exists())
            manifest["formatVersion"] = 999
            (root / "content/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                RuntimePaths(app)

    def test_empty_compatibility_rules_never_rewrite_a_script(self):
        commands = [{"op": "mess", "line": 1, "name": "Example", "text": "Example text."}]
        with patch("core.script_compat.profiles", return_value={}):
            result, notes = apply_compatibility("synthetic script", commands, lambda text: text)
        self.assertEqual(result, commands)
        self.assertEqual(notes, [])

    def test_server_can_read_a_synthetic_content_tree(self):
        import server
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); app_root = root / "app"; app_root.mkdir()
            catalog = root / "content/catalog"; catalog.mkdir(parents=True)
            for name in ("display-titles.json", "script-compat-profiles.json"):
                (catalog / name).write_text("{}", encoding="utf-8")
            (root / "content/manifest.json").write_text(json.dumps({"kind": "fkg-reader-content", "formatVersion": 1}), encoding="utf-8")
            stories = root / "content/stories"; (stories / "bin").mkdir(parents=True)
            digest = hashlib.md5(b"story_000001").hexdigest()
            (stories / "bin" / (digest + ".bin")).write_bytes(zlib.compress(b"mess,Example Speaker,An example sentence.\n"))
            (stories / "story-manifest.csv").write_text("storyId\n1\n", encoding="utf-8")
            (catalog / "story-metadata.json").write_text(json.dumps({"1": {"categoryKey": "mainstory", "title": "Synthetic Example"}}), encoding="utf-8")
            (root / "content/assets/cache/assets").mkdir(parents=True)
            with patch.object(server, "ROOT", app_root), patch("core.script_compat.profiles", return_value={}):
                app = server.App()
                self.assertEqual(len(app.library.cards), 1)
                self.assertEqual(app.library.read(1)["script"]["commands"][0]["text"], "An example sentence.")
                self.assertEqual(app.personal_translations.path, root / "userdata/personal-translations.json")


if __name__ == "__main__":
    unittest.main()
