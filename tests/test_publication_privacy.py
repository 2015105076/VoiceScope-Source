"""Exercise publication checks in disposable repos with synthetic records only."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"


class PublicationPrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Synthetic Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("README.md", "Synthetic project\n")
        self.commit()

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def write(self, name: str, value: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def commit(self) -> None:
        self.git("add", "--all")
        self.git("commit", "-m", "Synthetic fixture")

    def scan(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=self.root, capture_output=True, text=True)

    def test_empty_config_examples_and_schema_are_allowed(self) -> None:
        self.write("backend/config/model_api.example.json", '{"api_key": ""}')
        self.write(".env.example", "PASSWORD=replace-with-a-secret\n")
        self.write("backend/mysql_schema.sql", "CREATE TABLE synthetic (id INTEGER);\n")
        self.commit()
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forced_staged_private_files_are_rejected(self) -> None:
        names = ["backend/data/audio/synthetic.wav", "backend/data/test.sqlite3-wal", ".env", "exports/synthetic.json"]
        for name in names:
            self.write(name, "synthetic private record")
        self.git("add", "--all")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        for name in names:
            self.assertIn(name, result.stderr)

    def test_deleted_historical_record_is_still_rejected(self) -> None:
        self.write("backend/data/synthetic.json", '{"patient": "synthetic"}')
        self.commit()
        self.git("rm", "backend/data/synthetic.json")
        self.commit()
        self.assertEqual(self.scan().returncode, 1)

    def test_deleted_credential_in_arbitrary_code_is_rejected_without_disclosure(self) -> None:
        synthetic_key = "sk-" + "A" * 32
        self.write("settings.py", f'API_KEY = "{synthetic_key}"\n')
        self.commit()
        self.write("settings.py", 'API_KEY = ""\n')
        self.commit()
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Possible credential", result.stderr)
        self.assertNotIn(synthetic_key, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
