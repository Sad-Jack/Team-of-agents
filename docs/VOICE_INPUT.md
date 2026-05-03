# Голосовой ввод (Telegram)

## Что это

Голосовой ввод — это дополнительный входной канал для Telegram-бота.
Бот не выполняет бизнес-логику в voice-хендлере: после распознавания текст передаётся в `Supervisor` так же, как обычное текстовое сообщение.

## Архитектура

Поток обработки:
1. Telegram присылает voice-сообщение.
2. Файл скачивается во временную директорию `VOICE_WORK_DIR`.
3. `ffmpeg` конвертирует аудио в WAV (`16kHz`, mono).
4. STT-провайдер распознаёт речь в текст.
5. Распознанный текст передаётся в `handle_user_text(...)` и дальше в Supervisor.

Ключевые модули:
- `telegram_bot.py` — интерфейсный слой Telegram.
- `speech_to_text.py` — конвертация и STT-абстракция.
- `supervisor.py` — планирование/выполнение безопасных действий.

## Настройка

По умолчанию голосовой ввод выключен:

```env
STT_PROVIDER=disabled
```

Варианты `STT_PROVIDER`:
- `disabled`
- `whisper_cli`
- `custom_cli`

Пример для `whisper_cli`:

```env
STT_PROVIDER=whisper_cli
FFMPEG_BINARY=ffmpeg
VOICE_WORK_DIR=.tmp/voice
WHISPER_CLI_BINARY=whisper
WHISPER_MODEL=small
WHISPER_LANGUAGE=ru
VOICE_KEEP_FILES=false
```

Проверка конфигурации:

```bash
python3 run.py voice-config
```

Локальная проверка распознавания:

```bash
python3 run.py transcribe-file --path sample.ogg
```

## Провайдеры

### whisper_cli

Использует локальный CLI `whisper`.
Ожидается команда формата:

```bash
whisper <audio.wav> --model small --language ru --output_format txt --output_dir .tmp/voice
```

### custom_cli

Использует `STT_CUSTOM_COMMAND` с обязательным плейсхолдером `{audio_path}`.
Пример:

```env
STT_PROVIDER=custom_cli
STT_CUSTOM_COMMAND=my_stt_tool --input {audio_path}
```

Ограничения безопасности:
- запрещены shell-операторы (`&&`, `||`, `;`, `|`, `>`, `<`, `` ` ``, `$(`)
- `shell=True` не используется

## Приватность и временные файлы

- временные файлы сохраняются в `.tmp/voice`
- по умолчанию удаляются после обработки
- при `VOICE_KEEP_FILES=true` файлы сохраняются для диагностики

## Troubleshooting

### ffmpeg not found

Симптом: ошибка запуска конвертации.

Что делать:
- проверьте `FFMPEG_BINARY`
- установите ffmpeg (`brew install ffmpeg` или `apt install ffmpeg`)

### STT disabled

Симптом: бот отвечает, что голосовой ввод выключен.

Что делать:
- установите `STT_PROVIDER=whisper_cli` или `custom_cli`

### whisper binary not found

Симптом: ошибка запуска whisper CLI.

Что делать:
- проверьте `WHISPER_CLI_BINARY`
- установите и проверьте CLI в `PATH`

### empty transcript

Симптом: распознавание завершилось, но текст пустой.

Что делать:
- проверьте качество аудио
- попробуйте другой `WHISPER_MODEL`
- проверьте язык `WHISPER_LANGUAGE`

### Telegram file download failed

Симптом: ошибка на этапе скачивания voice-файла.

Что делать:
- проверьте подключение и права бота
- проверьте `TELEGRAM_BOT_TOKEN`
- запустите `python3 run.py telegram-config`
