from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class SpeechToTextError(Exception):
    pass


FORBIDDEN_TOKENS = ["&&", "||", ";", "|", ">", "<", "`", "$("]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_stt_provider() -> str:
    provider = (os.getenv("STT_PROVIDER") or "disabled").strip().lower()
    allowed = {"disabled", "whisper_cli", "custom_cli"}
    if provider not in allowed:
        raise SpeechToTextError(f"Invalid STT_PROVIDER: {provider}. Allowed: disabled, whisper_cli, custom_cli")
    return provider


def is_voice_enabled() -> bool:
    return get_stt_provider() in {"whisper_cli", "custom_cli"}


def get_voice_work_dir() -> str:
    return (os.getenv("VOICE_WORK_DIR") or ".tmp/voice").strip() or ".tmp/voice"


def ensure_voice_work_dir() -> Path:
    path = Path(get_voice_work_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stderr_preview(text: str, limit: int = 400) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def convert_voice_to_wav(input_path: str, output_path: str) -> str:
    in_path = Path(input_path)
    out_path = Path(output_path)
    if not in_path.exists() or not in_path.is_file():
        raise SpeechToTextError(f"Input voice file does not exist: {in_path.as_posix()}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_binary = (os.getenv("FFMPEG_BINARY") or "ffmpeg").strip() or "ffmpeg"

    try:
        completed = subprocess.run(
            [
                ffmpeg_binary,
                "-y",
                "-i",
                in_path.as_posix(),
                "-ar",
                "16000",
                "-ac",
                "1",
                out_path.as_posix(),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise SpeechToTextError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeechToTextError("ffmpeg conversion timed out.") from exc

    if completed.returncode != 0:
        err = _stderr_preview(completed.stderr or completed.stdout)
        raise SpeechToTextError(f"ffmpeg conversion failed: {err}")

    if not out_path.exists():
        raise SpeechToTextError("ffmpeg conversion finished but output wav file is missing.")
    return out_path.as_posix()


def transcribe_with_whisper_cli(wav_path: str) -> str:
    in_path = Path(wav_path)
    if not in_path.exists() or not in_path.is_file():
        raise SpeechToTextError(f"Audio file does not exist: {in_path.as_posix()}")

    binary = (os.getenv("WHISPER_CLI_BINARY") or "whisper").strip() or "whisper"
    model = (os.getenv("WHISPER_MODEL") or "small").strip() or "small"
    language = (os.getenv("WHISPER_LANGUAGE") or "ru").strip() or "ru"
    out_dir = ensure_voice_work_dir()

    try:
        completed = subprocess.run(
            [
                binary,
                in_path.as_posix(),
                "--model",
                model,
                "--language",
                language,
                "--output_format",
                "txt",
                "--output_dir",
                out_dir.as_posix(),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise SpeechToTextError(f"whisper binary not found: {binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeechToTextError("whisper transcription timed out.") from exc

    if completed.returncode != 0:
        err = _stderr_preview(completed.stderr or completed.stdout)
        raise SpeechToTextError(f"whisper transcription failed: {err}")

    txt_path = out_dir / f"{in_path.stem}.txt"
    transcript = ""
    if txt_path.exists() and txt_path.is_file():
        transcript = txt_path.read_text(encoding="utf-8", errors="replace").strip()
    if not transcript:
        transcript = (completed.stdout or "").strip()
    if not transcript:
        raise SpeechToTextError("Whisper returned empty transcript.")
    return transcript


def transcribe_with_custom_cli(wav_path: str) -> str:
    command = (os.getenv("STT_CUSTOM_COMMAND") or "").strip()
    if not command:
        raise SpeechToTextError("STT_CUSTOM_COMMAND is required for custom_cli provider.")
    if "{audio_path}" not in command:
        raise SpeechToTextError("STT_CUSTOM_COMMAND must include '{audio_path}' placeholder.")
    if any(token in command for token in FORBIDDEN_TOKENS):
        raise SpeechToTextError("STT_CUSTOM_COMMAND contains forbidden shell operators.")

    args = shlex.split(command)
    if not args:
        raise SpeechToTextError("STT_CUSTOM_COMMAND is empty after parsing.")
    args = [wav_path if part == "{audio_path}" else part.replace("{audio_path}", wav_path) for part in args]

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise SpeechToTextError(f"Custom STT binary not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeechToTextError("Custom STT transcription timed out.") from exc

    if completed.returncode != 0:
        err = _stderr_preview(completed.stderr or completed.stdout)
        raise SpeechToTextError(f"Custom STT command failed: {err}")
    transcript = (completed.stdout or "").strip()
    if not transcript:
        raise SpeechToTextError("Custom STT command returned empty transcript.")
    return transcript


def transcribe_audio(wav_path: str) -> str:
    provider = get_stt_provider()
    if provider == "disabled":
        raise SpeechToTextError("Voice transcription is disabled.")
    if provider == "whisper_cli":
        return transcribe_with_whisper_cli(wav_path)
    if provider == "custom_cli":
        return transcribe_with_custom_cli(wav_path)
    raise SpeechToTextError(f"Unsupported STT provider: {provider}")


def cleanup_voice_files(paths: list[str]) -> None:
    for item in paths:
        try:
            path = Path(item)
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            continue


def should_keep_voice_files() -> bool:
    return _bool_env("VOICE_KEEP_FILES", default=False)
