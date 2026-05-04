#!/usr/bin/env python3
"""MLX-Whisper STT wrapper for Team-of-agents voice pipeline.

Usage:
    python scripts/stt_mlx_whisper.py --audio-path audio.wav
    python scripts/stt_mlx_whisper.py --audio-path audio.wav --model mlx-community/whisper-small-mlx --language ru

Prints only the recognised text to stdout.
Errors go to stderr.
Exit code 0 on success, 1 on failure.

Integration with Team-of-agents:
    STT_PROVIDER=custom_cli
    STT_CUSTOM_COMMAND=python scripts/stt_mlx_whisper.py --audio-path {audio_path} --model mlx-community/whisper-small-mlx --language ru

Note: the first run may be slow as mlx-whisper downloads the model into its cache.
Install: pip install mlx-whisper
"""
from __future__ import annotations

import argparse
import sys


def transcribe_audio(audio_path: str, model: str, language: str) -> str:
    """Transcribe *audio_path* using mlx_whisper and return the text.

    Separated into its own function so it can be unit-tested with a mock.
    Raises ImportError if mlx_whisper is not installed.
    Raises RuntimeError (or any exception mlx_whisper raises) on transcription failure.
    """
    try:
        import mlx_whisper  # noqa: PLC0415 — intentional lazy import
    except ImportError as exc:
        raise ImportError(
            "mlx_whisper is not installed. "
            "Install with: pip install mlx-whisper"
        ) from exc

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model,
        language=language,
    )
    return (result.get("text") or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe an audio file with mlx-whisper and print the text to stdout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--audio-path",
        required=True,
        metavar="PATH",
        help="Path to the WAV audio file to transcribe.",
    )
    parser.add_argument(
        "--model",
        default="mlx-community/whisper-small-mlx",
        metavar="REPO",
        help="HuggingFace repo or local path for the mlx-whisper model. "
             "Default: mlx-community/whisper-small-mlx",
    )
    parser.add_argument(
        "--language",
        default="ru",
        metavar="LANG",
        help="Language code passed to mlx_whisper.transcribe. Default: ru",
    )

    args = parser.parse_args(argv)

    try:
        text = transcribe_audio(args.audio_path, args.model, args.language)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1

    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
