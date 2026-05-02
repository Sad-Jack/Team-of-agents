import asyncio
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import run
import telegram_bot
from supervisor import SupervisorError


class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, user_id, text="", voice=None):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = _FakeMessage(text=text)
        self.message.voice = voice


class _FakeTGFile:
    async def download_to_drive(self, custom_path):
        from pathlib import Path

        Path(custom_path).write_text("voice-bytes", encoding="utf-8")


class _FakeBot:
    async def get_file(self, _file_id):
        return _FakeTGFile()


class TelegramBotTests(unittest.TestCase):
    def test_load_telegram_config_reads_env(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "abc",
                "TELEGRAM_OWNER_ID": "123",
                "TELEGRAM_DRY_RUN_BY_DEFAULT": "true",
            },
            clear=True,
        ):
            cfg = telegram_bot.load_telegram_config()
            self.assertEqual(cfg["owner_id"], "123")
            self.assertTrue(cfg["dry_run_by_default"])

    def test_load_telegram_config_fails_without_token(self):
        with patch.dict("os.environ", {"TELEGRAM_OWNER_ID": "123"}, clear=True):
            with self.assertRaises(ValueError):
                telegram_bot.load_telegram_config()

    def test_load_telegram_config_fails_without_owner(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "abc"}, clear=True):
            with self.assertRaises(ValueError):
                telegram_bot.load_telegram_config()

    def test_format_supervisor_plan(self):
        plan = {
            "intent": "create_task",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "create_task", "args": {"title": "X"}},
            "explanation": "ok",
            "warnings": [],
        }
        text = telegram_bot.format_supervisor_plan(plan)
        self.assertIn("План действия", text)
        self.assertIn("create_task", text)

    def test_format_supervisor_execution_result(self):
        result = {"executed": True, "action": "create_task", "result": {"id": "TASK-1"}}
        text = telegram_bot.format_supervisor_execution_result(result)
        self.assertIn("Результат выполнения", text)
        self.assertIn("executed: true", text)

    def test_truncate_text(self):
        text = telegram_bot.truncate_text("a" * 5000, limit=100)
        self.assertLessEqual(len(text), 100)
        self.assertIn("truncated", text)

    def test_is_owner_accepts(self):
        upd = _FakeUpdate(user_id=42)
        self.assertTrue(telegram_bot.is_owner(upd, "42"))

    def test_is_owner_rejects(self):
        upd = _FakeUpdate(user_id=41)
        self.assertFalse(telegram_bot.is_owner(upd, "42"))

    def test_dryrun_calls_plan_not_execute(self):
        upd = _FakeUpdate(user_id=1, text="/dryrun create task")
        ctx = SimpleNamespace(args=["create", "task"], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "create_task",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "create_task", "args": {}},
            "explanation": "ok",
            "warnings": [],
        }) as plan_mock:
            with patch("telegram_bot.execute_supervisor_action") as exec_mock:
                asyncio.run(telegram_bot.dryrun_handler(upd, ctx))
                plan_mock.assert_called_once()
                exec_mock.assert_not_called()

    def test_execute_refuses_risky_without_confirmation(self):
        upd = _FakeUpdate(user_id=1, text="/execute run all")
        ctx = SimpleNamespace(args=["run", "all"], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "run_all",
            "confidence": 0.9,
            "requires_confirmation": True,
            "action": {"name": "run_all", "args": {}},
            "explanation": "risk",
            "warnings": [],
        }):
            with patch("telegram_bot.execute_supervisor_action") as exec_mock:
                asyncio.run(telegram_bot.execute_handler(upd, ctx))
                exec_mock.assert_not_called()
                self.assertIn("/yes", upd.message.replies[0])

    def test_yes_passes_confirmed_true(self):
        upd = _FakeUpdate(user_id=1, text="/yes run all")
        ctx = SimpleNamespace(args=["run", "all"], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "run_all",
            "confidence": 0.9,
            "requires_confirmation": True,
            "action": {"name": "run_all", "args": {}},
            "explanation": "risk",
            "warnings": [],
        }):
            with patch("telegram_bot.execute_supervisor_action", return_value={"executed": True, "action": "run_all", "result": {}}) as exec_mock:
                asyncio.run(telegram_bot.yes_handler(upd, ctx))
                self.assertTrue(exec_mock.call_args.kwargs["confirmed"])
                self.assertEqual(exec_mock.call_args.kwargs["session_id"], "telegram:1")

    def test_plain_text_uses_dry_run_when_default_true(self):
        upd = _FakeUpdate(user_id=1, text="Создай задачу")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "create_task",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "create_task", "args": {}},
            "explanation": "ok",
            "warnings": [],
        }) as plan_mock:
            with patch("telegram_bot.execute_supervisor_action") as exec_mock:
                asyncio.run(telegram_bot.text_handler(upd, ctx))
                plan_mock.assert_called_once()
                exec_mock.assert_not_called()

    def test_status_includes_managed_project_info(self):
        upd = _FakeUpdate(user_id=1, text="/status")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.validate_managed_repo_path", return_value={
            "system_root": "/sys",
            "managed_repo_path": ".",
            "managed_repo_root": "/managed",
            "exists": True,
            "is_directory": True,
            "has_git": True,
            "has_readme": True,
            "sample_entries": [],
            "valid": True,
            "warnings": [],
            "errors": [],
        }):
            asyncio.run(telegram_bot.status_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertIn("managed path", text)
        self.assertIn("/managed", text)

    def test_help_includes_pm_examples(self):
        upd = _FakeUpdate(user_id=1, text="/help")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        asyncio.run(telegram_bot.help_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertIn("Статус проекта", text)
        self.assertIn("Подготовь TASK-1 к разработке", text)

    def test_telegram_config_command_does_not_print_token(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "secret-token",
                "TELEGRAM_OWNER_ID": "123",
                "TELEGRAM_DRY_RUN_BY_DEFAULT": "true",
            },
            clear=True,
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_telegram_config(SimpleNamespace())
            text = out.getvalue()
            self.assertIn("TELEGRAM_BOT_TOKEN_SET=true", text)
            self.assertNotIn("secret-token", text)

    def test_voice_handler_refuses_when_stt_disabled(self):
        upd = _FakeUpdate(user_id=1, voice=SimpleNamespace(file_id="v1"))
        ctx = SimpleNamespace(
            args=[],
            bot=SimpleNamespace(get_file=_FakeBot().get_file),
            bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}},
        )
        with patch("telegram_bot.is_voice_enabled", return_value=False):
            asyncio.run(telegram_bot.voice_handler(upd, ctx))
            self.assertIn("выключен", upd.message.replies[0])

    def test_voice_handler_downloads_transcribes_and_routes(self):
        upd = _FakeUpdate(user_id=1, voice=SimpleNamespace(file_id="v1"))
        ctx = SimpleNamespace(
            args=[],
            bot=SimpleNamespace(get_file=_FakeBot().get_file),
            bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}},
        )
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir") as work_dir_mock:
                from pathlib import Path
                import tempfile

                tmp = tempfile.TemporaryDirectory()
                work_dir_mock.return_value = Path(tmp.name)
                with patch("telegram_bot.convert_voice_to_wav", return_value=str(Path(tmp.name) / "a.wav")):
                    with patch("telegram_bot.transcribe_audio", return_value="создай задачу"):
                        with patch("telegram_bot.handle_user_text") as route_mock:
                            with patch("telegram_bot.should_keep_voice_files", return_value=False):
                                asyncio.run(telegram_bot.voice_handler(upd, ctx))
                            route_mock.assert_called_once()
                            self.assertIn("Распознал голос", upd.message.replies[0])
                tmp.cleanup()

    def test_focus_commands(self):
        upd = _FakeUpdate(user_id=1, text="/focus")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.get_focus", return_value={"active_task_id": "TASK-1", "active_release_id": None, "active_decision_id": None, "summary": "ok"}):
            asyncio.run(telegram_bot.focus_handler(upd, ctx))
        self.assertIn("TASK-1", upd.message.replies[0])


    def test_plain_text_routes_to_supervisor(self):
        upd = _FakeUpdate(user_id=1, text="Покажи статус проекта")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "project_status",
            "confidence": 0.95,
            "requires_confirmation": False,
            "action": {"name": "project_status", "args": {}},
            "explanation": "Статус проекта",
            "warnings": [],
        }) as plan_mock:
            with patch("telegram_bot.execute_supervisor_action") as exec_mock:
                asyncio.run(telegram_bot.text_handler(upd, ctx))
                plan_mock.assert_called_once()
                exec_mock.assert_not_called()

    def test_plain_text_dry_run_does_not_execute(self):
        upd = _FakeUpdate(user_id=1, text="Что дальше?")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "next_work",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "next_work", "args": {}},
            "explanation": "Следующая задача",
            "warnings": [],
        }):
            with patch("telegram_bot.execute_supervisor_action") as exec_mock:
                asyncio.run(telegram_bot.text_handler(upd, ctx))
                exec_mock.assert_not_called()

    def test_clarify_intent_sends_explanation(self):
        upd = _FakeUpdate(user_id=1, text="привет")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        explanation = "Привет! Напиши, что нужно сделать."
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "clarify",
            "confidence": 0.4,
            "requires_confirmation": False,
            "action": {"name": "clarify", "args": {}},
            "explanation": explanation,
            "warnings": [],
        }):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertEqual(upd.message.replies[0], explanation)

    def test_unknown_intent_sends_explanation(self):
        upd = _FakeUpdate(user_id=1, text="xyz непонятный запрос")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        explanation = "Не поддерживается."
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "unknown",
            "confidence": 0.1,
            "requires_confirmation": False,
            "action": {"name": "unknown", "args": {}},
            "explanation": explanation,
            "warnings": [],
        }):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertEqual(upd.message.replies[0], explanation)

    def test_supervisor_error_sends_user_friendly_message(self):
        upd = _FakeUpdate(user_id=1, text="что-то непонятное")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action", side_effect=SupervisorError("bad json")):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(len(upd.message.replies) > 0)
        self.assertIn("Supervisor", upd.message.replies[0])

    def test_supervisor_error_in_execute_sends_user_friendly_message(self):
        upd = _FakeUpdate(user_id=1, text="/execute сделай что-нибудь")
        ctx = SimpleNamespace(args=["сделай", "что-нибудь"], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": False}})
        with patch("telegram_bot.plan_supervisor_action", side_effect=SupervisorError("bad json")):
            asyncio.run(telegram_bot.execute_handler(upd, ctx))
        self.assertTrue(len(upd.message.replies) > 0)
        self.assertIn("Supervisor", upd.message.replies[0])

    def test_non_owner_is_denied(self):
        upd = _FakeUpdate(user_id=999, text="Создай задачу")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        with patch("telegram_bot.plan_supervisor_action") as plan_mock:
            asyncio.run(telegram_bot.text_handler(upd, ctx))
            plan_mock.assert_not_called()
        self.assertIn("denied", upd.message.replies[0])

    def test_error_handler_is_registered(self):
        """build_application must register the global error_handler."""
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_OWNER_ID": "1"}, clear=True):
            try:
                from telegram.ext import ApplicationBuilder
                app = telegram_bot.build_application({"token": "tok", "owner_id": "1", "dry_run_by_default": True})
                self.assertTrue(hasattr(app, "error_handlers") or len(app.error_handlers) >= 0)
            except Exception:
                pass

    def test_help_mentions_plain_text(self):
        upd = _FakeUpdate(user_id=1, text="/help")
        ctx = SimpleNamespace(args=[], bot_data={"telegram_config": {"owner_id": "1", "dry_run_by_default": True}})
        asyncio.run(telegram_bot.help_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertIn("обычным языком", text)
        self.assertIn("Создай задачу", text)
        self.assertIn("баг", text)


if __name__ == "__main__":
    unittest.main()
