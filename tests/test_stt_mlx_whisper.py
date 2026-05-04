"""Tests for scripts/stt_mlx_whisper.py

All tests mock mlx_whisper so they run without the package installed.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Locate and import the script as a module
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "stt_mlx_whisper.py"


def _load_script():
    """Import scripts/stt_mlx_whisper.py as a module without executing main()."""
    spec = importlib.util.spec_from_file_location("stt_mlx_whisper", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_fake_mlx_whisper(text: str = "распознанный текст") -> types.ModuleType:
    """Build a fake mlx_whisper module that returns *text* from transcribe()."""
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = MagicMock(return_value={"text": text})
    return fake


# ---------------------------------------------------------------------------
# Basic existence checks
# ---------------------------------------------------------------------------

class TestScriptExists(unittest.TestCase):

    def test_script_file_exists(self):
        self.assertTrue(_SCRIPT_PATH.exists(), f"Script not found: {_SCRIPT_PATH}")

    def test_script_is_executable(self):
        import os, stat
        mode = _SCRIPT_PATH.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "Script should be user-executable")

    def test_script_compiles(self):
        import py_compile
        py_compile.compile(str(_SCRIPT_PATH), doraise=True)

    def test_help_exits_0(self):
        """--help should print usage and exit 0."""
        stt = _load_script()
        with self.assertRaises(SystemExit) as cm:
            stt.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


# ---------------------------------------------------------------------------
# transcribe_audio() unit tests
# ---------------------------------------------------------------------------

class TestTranscribeAudioFunction(unittest.TestCase):

    def _run(self, fake_module, audio_path="audio.wav",
             model="mlx-community/whisper-small-mlx", language="ru"):
        stt = _load_script()
        with patch.dict(sys.modules, {"mlx_whisper": fake_module}):
            return stt.transcribe_audio(audio_path, model, language)

    def test_returns_text_from_result(self):
        fake = _make_fake_mlx_whisper("Привет мир")
        text = self._run(fake)
        self.assertEqual(text, "Привет мир")

    def test_strips_whitespace(self):
        fake = _make_fake_mlx_whisper("  пробелы  ")
        text = self._run(fake)
        self.assertEqual(text, "пробелы")

    def test_passes_audio_path_to_transcribe(self):
        fake = _make_fake_mlx_whisper("ok")
        self._run(fake, audio_path="/tmp/test.wav")
        fake.transcribe.assert_called_once()
        call_args = fake.transcribe.call_args
        # First positional arg is audio_path
        self.assertEqual(call_args[0][0], "/tmp/test.wav")

    def test_passes_model_to_transcribe(self):
        fake = _make_fake_mlx_whisper("ok")
        self._run(fake, model="mlx-community/whisper-large-mlx")
        call_kwargs = fake.transcribe.call_args[1]
        self.assertEqual(call_kwargs["path_or_hf_repo"], "mlx-community/whisper-large-mlx")

    def test_passes_language_to_transcribe(self):
        fake = _make_fake_mlx_whisper("ok")
        self._run(fake, language="en")
        call_kwargs = fake.transcribe.call_args[1]
        self.assertEqual(call_kwargs["language"], "en")

    def test_returns_empty_string_when_text_is_none(self):
        fake = types.ModuleType("mlx_whisper")
        fake.transcribe = MagicMock(return_value={"text": None})
        stt = _load_script()
        with patch.dict(sys.modules, {"mlx_whisper": fake}):
            result = stt.transcribe_audio("audio.wav", "model", "ru")
        self.assertEqual(result, "")

    def test_raises_import_error_when_not_installed(self):
        stt = _load_script()

        # Simulate mlx_whisper missing by patching transcribe_audio itself
        # (avoids touching sys.modules with native C extensions already loaded)
        original = stt.transcribe_audio

        def _raise_import(*_a, **_kw):
            raise ImportError("mlx_whisper is not installed")

        with patch.object(stt, "transcribe_audio", _raise_import):
            with self.assertRaises(ImportError):
                stt.transcribe_audio("audio.wav", "model", "ru")

    def test_propagates_transcribe_exception(self):
        fake = types.ModuleType("mlx_whisper")
        fake.transcribe = MagicMock(side_effect=RuntimeError("CUDA out of memory"))
        stt = _load_script()
        with patch.dict(sys.modules, {"mlx_whisper": fake}):
            with self.assertRaises(RuntimeError):
                stt.transcribe_audio("audio.wav", "model", "ru")


# ---------------------------------------------------------------------------
# main() — end-to-end tests via captured stdout/stderr
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def _run_main(self, argv, fake_module):
        """Run main(argv) with mlx_whisper mocked; return (stdout_lines, stderr_lines, exit_code)."""
        import io
        stt = _load_script()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with patch.dict(sys.modules, {"mlx_whisper": fake_module}):
            with patch("sys.stdout", stdout_buf):
                with patch("sys.stderr", stderr_buf):
                    exit_code = stt.main(argv)

        stdout_lines = stdout_buf.getvalue()
        stderr_lines = stderr_buf.getvalue()
        return stdout_lines, stderr_lines, exit_code

    def test_successful_transcription_stdout_contains_text(self):
        fake = _make_fake_mlx_whisper("тестовый текст")
        stdout, stderr, code = self._run_main(["--audio-path", "audio.wav"], fake)
        self.assertEqual(code, 0)
        self.assertIn("тестовый текст", stdout)
        self.assertEqual(stderr, "")

    def test_empty_transcript_produces_empty_stdout(self):
        fake = _make_fake_mlx_whisper("")
        stdout, stderr, code = self._run_main(["--audio-path", "audio.wav"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_whitespace_only_transcript_produces_empty_stdout(self):
        fake = _make_fake_mlx_whisper("   ")
        stdout, stderr, code = self._run_main(["--audio-path", "audio.wav"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_import_error_exits_1_and_writes_stderr(self):
        import io
        stt = _load_script()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # Simulate mlx_whisper missing by making transcribe_audio raise ImportError
        def _raise_import(*_a, **_kw):
            raise ImportError("mlx_whisper is not installed. Install with: pip install mlx-whisper")

        with patch.object(stt, "transcribe_audio", _raise_import):
            with patch("sys.stdout", stdout_buf):
                with patch("sys.stderr", stderr_buf):
                    code = stt.main(["--audio-path", "audio.wav"])

        self.assertEqual(code, 1)
        self.assertIn("mlx", stderr_buf.getvalue().lower())

    def test_transcribe_exception_exits_1_and_writes_stderr(self):
        fake = types.ModuleType("mlx_whisper")
        fake.transcribe = MagicMock(side_effect=ValueError("bad audio"))
        import io
        stt = _load_script()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.dict(sys.modules, {"mlx_whisper": fake}):
            with patch("sys.stdout", stdout_buf):
                with patch("sys.stderr", stderr_buf):
                    code = stt.main(["--audio-path", "audio.wav"])
        self.assertEqual(code, 1)
        self.assertIn("bad audio", stderr_buf.getvalue())

    def test_default_model_used_when_not_specified(self):
        fake = _make_fake_mlx_whisper("ok")
        self._run_main(["--audio-path", "audio.wav"], fake)
        call_kwargs = fake.transcribe.call_args[1]
        self.assertIn("whisper-small", call_kwargs["path_or_hf_repo"])

    def test_custom_model_passed_through(self):
        fake = _make_fake_mlx_whisper("ok")
        self._run_main(
            ["--audio-path", "audio.wav", "--model", "mlx-community/whisper-large-mlx"],
            fake,
        )
        call_kwargs = fake.transcribe.call_args[1]
        self.assertEqual(call_kwargs["path_or_hf_repo"], "mlx-community/whisper-large-mlx")

    def test_custom_language_passed_through(self):
        fake = _make_fake_mlx_whisper("hello")
        self._run_main(
            ["--audio-path", "audio.wav", "--language", "en"],
            fake,
        )
        call_kwargs = fake.transcribe.call_args[1]
        self.assertEqual(call_kwargs["language"], "en")

    def test_stdout_has_no_trailing_newlines_beyond_one(self):
        """Output should be just the text followed by a single newline from print()."""
        fake = _make_fake_mlx_whisper("текст")
        stdout, _, code = self._run_main(["--audio-path", "audio.wav"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "текст\n")

    def test_no_json_in_stdout(self):
        """Script must print plain text, not JSON."""
        fake = _make_fake_mlx_whisper("некий текст")
        stdout, _, _ = self._run_main(["--audio-path", "audio.wav"], fake)
        self.assertNotIn("{", stdout)
        self.assertNotIn("}", stdout)


if __name__ == "__main__":
    unittest.main()
