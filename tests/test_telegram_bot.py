import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import run
import telegram_bot


class _FakeIKButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data


class _FakeIKMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class _FakeTelegramModule:
    InlineKeyboardButton = _FakeIKButton
    InlineKeyboardMarkup = _FakeIKMarkup


def _patch_telegram():
    return patch.dict(sys.modules, {"telegram": _FakeTelegramModule()})


class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


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


class _FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self):
        self.answers.append(True)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


class _FakeCallbackUpdate:
    def __init__(self, data, user_id="42", chat_id="42"):
        self.callback_query = _FakeCallbackQuery(data)
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.message = None


class _FakeSendBot:
    def __init__(self, raise_on_send=False):
        self.sent = []
        self._raise = raise_on_send

    async def get_file(self, _file_id):
        return _FakeTGFile()

    async def send_message(self, chat_id, text):
        if self._raise:
            raise RuntimeError("send failed")
        self.sent.append({"chat_id": chat_id, "text": text})


def _make_ctx(owner_id="42", bot=None):
    b = bot or _FakeSendBot()
    return SimpleNamespace(
        args=[],
        bot=b,
        bot_data={"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}},
    )


_CREATE_TASK_PLAN = {
    "intent": "create_task",
    "confidence": 0.9,
    "requires_confirmation": False,
    "action": {"name": "create_task", "args": {"title": "Test Task"}},
    "explanation": "ok",
    "warnings": [],
}

_CREATE_TASK_RESULT = {
    "executed": True,
    "action": "create_task",
    "result": {"id": "TASK-99", "title": "Test Task", "status": "idea"},
}

_CREATE_BUG_PLAN = {
    "intent": "create_bug",
    "confidence": 0.9,
    "requires_confirmation": False,
    "action": {"name": "create_bug", "args": {"title": "Bug Title"}},
    "explanation": "ok",
    "warnings": [],
}

_CREATE_BUG_RESULT = {
    "executed": True,
    "action": "create_bug",
    "result": {"id": "TASK-100", "title": "Bug Title", "status": "idea"},
}

_RISKY_PLAN = {
    "intent": "run_all",
    "confidence": 0.9,
    "requires_confirmation": True,
    "action": {"name": "run_all", "args": {}},
    "explanation": "risky",
    "warnings": [],
}


class TestPendingActions(unittest.TestCase):
    def setUp(self):
        telegram_bot.PENDING_ACTIONS.clear()

    def test_dry_run_stores_pending_action(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        ctx = _make_ctx()
        with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_TASK_PLAN):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertIn("telegram:42", telegram_bot.PENDING_ACTIONS)

    def test_dry_run_shows_inline_buttons(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        ctx = _make_ctx()
        with _patch_telegram():
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_TASK_PLAN):
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(
            any(m is not None for m in upd.message.reply_markups),
            "Expected inline keyboard markup in reply",
        )
        markup = next(m for m in upd.message.reply_markups if m is not None)
        cb_datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("confirm_pending_action", cb_datas)
        self.assertIn("cancel_pending_action", cb_datas)

    def test_dry_run_clarify_no_pending(self):
        upd = _FakeUpdate(user_id="42", text="что-нибудь")
        ctx = _make_ctx()
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "clarify", "explanation": "Уточни запрос", "action": None,
            "warnings": [], "confidence": 0.5, "requires_confirmation": False,
        }):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertNotIn("telegram:42", telegram_bot.PENDING_ACTIONS)

    def test_dry_run_unknown_no_pending(self):
        upd = _FakeUpdate(user_id="42", text="что-нибудь")
        ctx = _make_ctx()
        with patch("telegram_bot.plan_supervisor_action", return_value={
            "intent": "unknown", "explanation": "Не понял", "action": None,
            "warnings": [], "confidence": 0.1, "requires_confirmation": False,
        }):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertNotIn("telegram:42", telegram_bot.PENDING_ACTIONS)

    def test_cancel_clears_pending(self):
        telegram_bot.PENDING_ACTIONS["telegram:42"] = {
            "plan": _CREATE_TASK_PLAN, "text": "test", "created_at": "now",
            "requires_confirmation": False,
        }
        upd = _FakeCallbackUpdate("cancel_pending_action", user_id="42")
        ctx = _make_ctx()
        asyncio.run(telegram_bot.cancel_callback(upd, ctx))
        self.assertNotIn("telegram:42", telegram_bot.PENDING_ACTIONS)
        self.assertIn("отменено", upd.callback_query.edits[0].lower())

    def test_confirm_no_pending_replies_error(self):
        upd = _FakeCallbackUpdate("confirm_pending_action", user_id="42")
        ctx = _make_ctx()
        asyncio.run(telegram_bot.confirm_callback(upd, ctx))
        self.assertIn("Нет действия", upd.callback_query.edits[0])

    def test_confirm_nonrisky_calls_execute(self):
        telegram_bot.PENDING_ACTIONS["telegram:42"] = {
            "plan": _CREATE_TASK_PLAN, "text": "test", "created_at": "now",
            "requires_confirmation": False,
        }
        upd = _FakeCallbackUpdate("confirm_pending_action", user_id="42")
        ctx = _make_ctx()
        with patch("telegram_bot.execute_supervisor_action", return_value=_CREATE_TASK_RESULT) as exec_mock:
            asyncio.run(telegram_bot.confirm_callback(upd, ctx))
        exec_mock.assert_called_once()
        self.assertNotIn("telegram:42", telegram_bot.PENDING_ACTIONS)

    def test_confirm_risky_refuses_and_does_not_execute(self):
        telegram_bot.PENDING_ACTIONS["telegram:42"] = {
            "plan": _RISKY_PLAN, "text": "test", "created_at": "now",
            "requires_confirmation": True,
        }
        upd = _FakeCallbackUpdate("confirm_pending_action", user_id="42")
        ctx = _make_ctx()
        with patch("telegram_bot.execute_supervisor_action") as exec_mock:
            asyncio.run(telegram_bot.confirm_callback(upd, ctx))
        exec_mock.assert_not_called()
        self.assertIn("/yes", upd.callback_query.edits[0])

    def test_confirm_nonowner_denied(self):
        telegram_bot.PENDING_ACTIONS["telegram:99"] = {
            "plan": _CREATE_TASK_PLAN, "text": "test", "created_at": "now",
            "requires_confirmation": False,
        }
        upd = _FakeCallbackUpdate("confirm_pending_action", user_id="99")
        ctx = _make_ctx(owner_id="42")
        asyncio.run(telegram_bot.confirm_callback(upd, ctx))
        self.assertIn("Доступ запрещён", upd.callback_query.edits[0])

    def test_new_dryrun_replaces_old_pending(self):
        telegram_bot.PENDING_ACTIONS["telegram:42"] = {
            "plan": _CREATE_TASK_PLAN, "text": "old", "created_at": "now",
            "requires_confirmation": False,
        }
        new_plan = {**_CREATE_TASK_PLAN, "action": {"name": "create_task", "args": {"title": "New"}}}
        upd = _FakeUpdate(user_id="42", text="создай задачу New")
        ctx = _make_ctx()
        with patch("telegram_bot.plan_supervisor_action", return_value=new_plan):
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertEqual(
            telegram_bot.PENDING_ACTIONS["telegram:42"]["text"], "создай задачу New"
        )


class TestStatusNotifications(unittest.TestCase):
    def test_no_notification_when_no_chat_id(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": ""}, clear=False):
            asyncio.run(telegram_bot.send_status_notification(ctx, "test"))
        self.assertEqual(bot.sent, [])

    def test_notification_sent_when_chat_id_set(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            asyncio.run(telegram_bot.send_status_notification(ctx, "hello"))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["chat_id"], "100")
        self.assertIn("hello", bot.sent[0]["text"])

    def test_notification_failure_does_not_raise(self):
        bot = _FakeSendBot(raise_on_send=True)
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            asyncio.run(telegram_bot.send_status_notification(ctx, "fail"))
        # should complete without raising

    def test_notify_action_result_create_task(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            asyncio.run(telegram_bot._notify_action_result(ctx, _CREATE_TASK_PLAN, _CREATE_TASK_RESULT))
        self.assertTrue(any("TASK-99" in m["text"] for m in bot.sent))
        self.assertTrue(any("🆕" in m["text"] for m in bot.sent))

    def test_notify_action_result_create_bug(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            asyncio.run(telegram_bot._notify_action_result(ctx, _CREATE_BUG_PLAN, _CREATE_BUG_RESULT))
        self.assertTrue(any("🐞" in m["text"] for m in bot.sent))

    def test_execute_create_task_sends_notification(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_TASK_PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=_CREATE_TASK_RESULT):
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(any("TASK-99" in m["text"] for m in bot.sent))

    def test_execute_create_bug_sends_notification(self):
        upd = _FakeUpdate(user_id="42", text="баг")
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_BUG_PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=_CREATE_BUG_RESULT):
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(any("🐞" in m["text"] for m in bot.sent))


class TestStartHandler(unittest.TestCase):
    def _run_start(self, managed_path="."):
        upd = _FakeUpdate(user_id="42", text="/start")
        ctx = _make_ctx()
        with patch("managed_project.get_managed_project_info", return_value={
            "system_root": "/sys",
            "managed_repo_path": managed_path,
            "managed_repo_root": "/managed" if managed_path != "." else "/sys",
        }):
            asyncio.run(telegram_bot.start_handler(upd, ctx))
        return upd

    def test_start_shows_self_managed(self):
        upd = self._run_start(managed_path=".")
        text = "\n".join(upd.message.replies)
        self.assertIn("self-managed", text)
        self.assertIn("MANAGED_REPO_PATH=..", text)

    def test_start_shows_embedded(self):
        upd = self._run_start(managed_path="..")
        text = "\n".join(upd.message.replies)
        self.assertIn("embedded", text)

    def test_start_has_study_button(self):
        upd = _FakeUpdate(user_id="42", text="/start")
        ctx = _make_ctx()
        with _patch_telegram():
            with patch("managed_project.get_managed_project_info", return_value={
                "system_root": "/sys",
                "managed_repo_path": ".",
                "managed_repo_root": "/sys",
            }):
                asyncio.run(telegram_bot.start_handler(upd, ctx))
        self.assertTrue(
            any(m is not None for m in upd.message.reply_markups),
            "Expected inline keyboard with study button",
        )
        markup = next(m for m in upd.message.reply_markups if m is not None)
        cb_datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("study_project", cb_datas)


class TestStudyProjectCallback(unittest.TestCase):
    def _make_study_upd(self):
        return _FakeCallbackUpdate("study_project", user_id="42", chat_id="42")

    def test_study_self_mode(self):
        upd = self._make_study_upd()
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch("managed_project.get_managed_project_info", return_value={
            "managed_repo_path": ".",
            "managed_repo_root": "/sys",
        }):
            asyncio.run(telegram_bot.study_project_callback(upd, ctx))
        all_text = " ".join(m["text"] for m in bot.sent)
        self.assertIn("MANAGED_REPO_PATH=.", all_text)

    def test_study_invalid_path(self):
        upd = self._make_study_upd()
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch("managed_project.get_managed_project_info", return_value={
            "managed_repo_path": "..",
            "managed_repo_root": "/nonexistent",
        }):
            with patch("managed_project.validate_managed_repo_path", return_value={
                "errors": ["Path does not exist"],
                "warnings": [],
                "valid": False,
            }):
                asyncio.run(telegram_bot.study_project_callback(upd, ctx))
        all_text = " ".join(m["text"] for m in bot.sent)
        self.assertIn("неверный", all_text)

    def test_study_valid_path(self):
        upd = self._make_study_upd()
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch("managed_project.get_managed_project_info", return_value={
            "managed_repo_path": "..",
            "managed_repo_root": "/some/project",
        }):
            with patch("managed_project.validate_managed_repo_path", return_value={
                "errors": [], "warnings": [], "valid": True,
            }):
                with patch("repo_inspector.scan_repository", return_value={
                    "total_files_indexed": 42,
                }):
                    asyncio.run(telegram_bot.study_project_callback(upd, ctx))
        all_text = " ".join(m["text"] for m in bot.sent)
        self.assertIn("42", all_text)
        self.assertIn("✅", all_text)

    def test_study_nonowner_denied(self):
        upd = self._make_study_upd()
        ctx = _make_ctx(owner_id="99")
        asyncio.run(telegram_bot.study_project_callback(upd, ctx))
        self.assertIn("Доступ запрещён", upd.callback_query.edits[0])


if __name__ == "__main__":
    unittest.main()
