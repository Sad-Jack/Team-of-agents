import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import speech_to_text
import run


class SpeechToTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.in_audio = self.root / "input.ogg"
        self.in_audio.write_text("dummy", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_stt_provider_default_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(speech_to_text.get_stt_provider(), "disabled")

    def test_invalid_stt_provider(self):
        with patch.dict(os.environ, {"STT_PROVIDER": "bad"}, clear=True):
            with self.assertRaises(speech_to_text.SpeechToTextError):
                speech_to_text.get_stt_provider()

    def test_is_voice_enabled(self):
        with patch.dict(os.environ, {"STT_PROVIDER": "disabled"}, clear=True):
            self.assertFalse(speech_to_text.is_voice_enabled())
        with patch.dict(os.environ, {"STT_PROVIDER": "whisper_cli"}, clear=True):
            self.assertTrue(speech_to_text.is_voice_enabled())

    def test_convert_voice_to_wav_subprocess_no_shell(self):
        out = self.root / "out.wav"
        with patch("speech_to_text.subprocess.run") as mocked:
            mocked.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            out.write_text("wav", encoding="utf-8")
            speech_to_text.convert_voice_to_wav(self.in_audio.as_posix(), out.as_posix())
            self.assertFalse(mocked.call_args.kwargs["shell"])

    def test_convert_voice_to_wav_failure(self):
        out = self.root / "out.wav"
        with patch("speech_to_text.subprocess.run") as mocked:
            mocked.return_value = SimpleNamespace(returncode=1, stdout="", stderr="ffmpeg err")
            with self.assertRaises(speech_to_text.SpeechToTextError):
                speech_to_text.convert_voice_to_wav(self.in_audio.as_posix(), out.as_posix())

    def test_transcribe_audio_disabled(self):
        with patch.dict(os.environ, {"STT_PROVIDER": "disabled"}, clear=True):
            with self.assertRaises(speech_to_text.SpeechToTextError):
                speech_to_text.transcribe_audio("x.wav")

    def test_transcribe_with_whisper_cli_reads_txt(self):
        wav = self.root / "voice.wav"
        wav.write_text("wav", encoding="utf-8")
        out_dir = self.root / "tmpvoice"
        out_dir.mkdir(parents=True, exist_ok=True)
        txt = out_dir / "voice.txt"
        txt.write_text("привет мир", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "STT_PROVIDER": "whisper_cli",
                "VOICE_WORK_DIR": out_dir.as_posix(),
                "WHISPER_CLI_BINARY": "whisper",
            },
            clear=True,
        ):
            with patch("speech_to_text.subprocess.run") as mocked:
                mocked.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                text = speech_to_text.transcribe_with_whisper_cli(wav.as_posix())
                self.assertEqual(text, "привет мир")
                self.assertFalse(mocked.call_args.kwargs["shell"])

    def test_custom_cli_rejects_without_placeholder(self):
        with patch.dict(os.environ, {"STT_PROVIDER": "custom_cli", "STT_CUSTOM_COMMAND": "echo hi"}, clear=True):
            with self.assertRaises(speech_to_text.SpeechToTextError):
                speech_to_text.transcribe_with_custom_cli("a.wav")

    def test_custom_cli_rejects_shell_operators(self):
        with patch.dict(
            os.environ,
            {"STT_PROVIDER": "custom_cli", "STT_CUSTOM_COMMAND": "mycmd {audio_path} | cat"},
            clear=True,
        ):
            with self.assertRaises(speech_to_text.SpeechToTextError):
                speech_to_text.transcribe_with_custom_cli("a.wav")

    def test_cleanup_voice_files(self):
        f = self.root / "x.wav"
        f.write_text("x", encoding="utf-8")
        speech_to_text.cleanup_voice_files([f.as_posix(), (self.root / "missing.wav").as_posix()])
        self.assertFalse(f.exists())

    def test_voice_config_cli(self):
        with patch.dict(os.environ, {"STT_PROVIDER": "disabled"}, clear=True):
            with patch("run.shutil.which", return_value="/usr/bin/ffmpeg"):
                import io
                from contextlib import redirect_stdout
                from types import SimpleNamespace

                out = io.StringIO()
                with redirect_stdout(out):
                    run.cmd_voice_config(SimpleNamespace())
                self.assertIn("STT_PROVIDER=disabled", out.getvalue())

    def test_transcribe_file_cli(self):
        wav = self.root / "sample.ogg"
        wav.write_text("x", encoding="utf-8")
        with patch("run.speech_to_text.convert_voice_to_wav", return_value=str(self.root / "a.wav")):
            with patch("run.speech_to_text.transcribe_audio", return_value="тест"):
                with patch("run.speech_to_text.should_keep_voice_files", return_value=False):
                    with patch("run.speech_to_text.cleanup_voice_files"):
                        import io
                        from contextlib import redirect_stdout
                        from types import SimpleNamespace

                        out = io.StringIO()
                        with redirect_stdout(out):
                            run.cmd_transcribe_file(SimpleNamespace(path=wav.as_posix()))
                        self.assertIn("тест", out.getvalue())


if __name__ == "__main__":
    unittest.main()
