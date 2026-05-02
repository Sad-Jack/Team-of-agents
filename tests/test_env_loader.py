import os
import tempfile
import unittest
from pathlib import Path

from env_loader import load_dotenv_if_exists


class EnvLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_path = str(Path(self.tmp.name) / ".env")
        # Track keys we set so we can clean up after each test
        self._injected_keys: list[str] = []

    def tearDown(self):
        for key in self._injected_keys:
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def _write_env(self, content: str) -> str:
        Path(self.env_path).write_text(content, encoding="utf-8")
        return self.env_path

    def _mark(self, key: str) -> None:
        self._injected_keys.append(key)

    # ── missing file ────────────────────────────────────────────────────────

    def test_missing_file_returns_empty_dict(self):
        result = load_dotenv_if_exists("/nonexistent/path/.env")
        self.assertEqual(result, {})

    def test_missing_file_does_not_raise(self):
        load_dotenv_if_exists("/tmp/does_not_exist_xyz.env")

    # ── basic parsing ────────────────────────────────────────────────────────

    def test_simple_key_value(self):
        self._write_env("MY_VAR=hello\n")
        self._mark("MY_VAR")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["MY_VAR"], "hello")
        self.assertEqual(os.environ["MY_VAR"], "hello")

    def test_multiple_pairs(self):
        self._write_env("FOO=bar\nBAZ=qux\n")
        self._mark("FOO")
        self._mark("BAZ")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["FOO"], "bar")
        self.assertEqual(result["BAZ"], "qux")

    def test_empty_file_returns_empty_dict(self):
        self._write_env("")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result, {})

    # ── comments and blank lines ─────────────────────────────────────────────

    def test_comment_lines_ignored(self):
        self._write_env("# этот файл конфигурации\nKEY1=val1\n")
        self._mark("KEY1")
        result = load_dotenv_if_exists(self.env_path)
        self.assertIn("KEY1", result)
        self.assertNotIn("# этот файл конфигурации", result)

    def test_blank_lines_ignored(self):
        self._write_env("\n\nKEY2=val2\n\n")
        self._mark("KEY2")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["KEY2"], "val2")

    def test_inline_comment_stripped(self):
        self._write_env("MYKEY=myvalue # this is a comment\n")
        self._mark("MYKEY")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["MYKEY"], "myvalue")

    # ── quoted values ────────────────────────────────────────────────────────

    def test_double_quoted_value(self):
        self._write_env('QUOTED="hello world"\n')
        self._mark("QUOTED")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["QUOTED"], "hello world")

    def test_single_quoted_value(self):
        self._write_env("SQUOTED='hello world'\n")
        self._mark("SQUOTED")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["SQUOTED"], "hello world")

    def test_unquoted_value_stays_as_is(self):
        self._write_env("PLAIN=123\n")
        self._mark("PLAIN")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result["PLAIN"], "123")

    # ── override logic ────────────────────────────────────────────────────────

    def test_no_override_does_not_overwrite_existing(self):
        os.environ["EXISTING_KEY"] = "original"
        self._mark("EXISTING_KEY")
        self._write_env("EXISTING_KEY=new_value\n")
        result = load_dotenv_if_exists(self.env_path, override=False)
        self.assertEqual(os.environ["EXISTING_KEY"], "original")
        self.assertNotIn("EXISTING_KEY", result)

    def test_override_true_overwrites_existing(self):
        os.environ["OVR_KEY"] = "original"
        self._mark("OVR_KEY")
        self._write_env("OVR_KEY=replaced\n")
        result = load_dotenv_if_exists(self.env_path, override=True)
        self.assertEqual(os.environ["OVR_KEY"], "replaced")
        self.assertEqual(result["OVR_KEY"], "replaced")

    def test_new_key_always_loaded(self):
        key = "BRAND_NEW_KEY_XYZ"
        os.environ.pop(key, None)
        self._mark(key)
        self._write_env(f"{key}=fresh\n")
        result = load_dotenv_if_exists(self.env_path)
        self.assertEqual(result[key], "fresh")
        self.assertEqual(os.environ[key], "fresh")

    # ── return value ─────────────────────────────────────────────────────────

    def test_returns_dict_of_loaded_keys(self):
        self._write_env("R1=a\nR2=b\n")
        self._mark("R1")
        self._mark("R2")
        result = load_dotenv_if_exists(self.env_path)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result.keys()), {"R1", "R2"})

    def test_skipped_existing_keys_not_in_returned_dict(self):
        os.environ["SKIP_ME"] = "kept"
        self._mark("SKIP_ME")
        self._write_env("SKIP_ME=ignored\nNEW_KEY=ok\n")
        self._mark("NEW_KEY")
        result = load_dotenv_if_exists(self.env_path, override=False)
        self.assertNotIn("SKIP_ME", result)
        self.assertIn("NEW_KEY", result)

    # ── secrets are not printed ───────────────────────────────────────────────

    def test_secret_value_not_in_stdout(self):
        import io
        from contextlib import redirect_stdout
        secret = "SUPER_SECRET_VALUE_12345"
        self._write_env(f"SECRET_KEY={secret}\n")
        self._mark("SECRET_KEY")
        buf = io.StringIO()
        with redirect_stdout(buf):
            load_dotenv_if_exists(self.env_path)
        self.assertNotIn(secret, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
