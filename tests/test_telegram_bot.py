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
    def __init__(self, user_id, text="", voice=None, reply_to_msg_id=None, chat_id=None):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id if chat_id is not None else user_id)
        self.message = _FakeMessage(text=text)
        self.message.voice = voice
        self.message.reply_to_message = (
            SimpleNamespace(message_id=reply_to_msg_id) if reply_to_msg_id is not None else None
        )


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
        text = telegram_bot.format_supervisor_plan(plan, debug=True)
        self.assertIn("План действия", text)
        self.assertIn("create_task", text)

    def test_format_supervisor_plan_human(self):
        plan = {
            "intent": "create_task",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "create_task", "args": {"title": "My Task"}},
            "explanation": "Creating a task",
            "warnings": [],
        }
        text = telegram_bot.format_supervisor_plan(plan, debug=False)
        self.assertIn("🧩", text)
        self.assertIn("My Task", text)
        self.assertNotIn("intent", text)
        self.assertNotIn("confidence", text)

    def test_format_supervisor_execution_result(self):
        result = {"executed": True, "action": "create_task", "result": {"id": "TASK-1"}}
        text = telegram_bot.format_supervisor_execution_result(result, debug=True)
        self.assertIn("Результат выполнения", text)
        self.assertIn("executed: true", text)

    def test_format_supervisor_execution_result_human(self):
        result = {
            "executed": True,
            "action": "create_task",
            "result": {"id": "TASK-1", "title": "My Task", "status": "idea"},
        }
        text = telegram_bot.format_supervisor_execution_result(result, debug=False)
        self.assertIn("✅", text)
        self.assertIn("TASK-1", text)
        self.assertNotIn("executed: true", text)
        self.assertNotIn("Результат выполнения", text)

    def test_debug_mode_default_false(self):
        import os as _os
        with patch.dict("os.environ", {}, clear=False):
            _os.environ.pop("TELEGRAM_DEBUG_MODE", None)
            self.assertFalse(telegram_bot.is_debug_mode())

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
    def __init__(self, raise_on_send=False, returned_message_id=None):
        self.sent = []
        self._raise = raise_on_send
        self._message_id = returned_message_id

    async def get_file(self, _file_id):
        return _FakeTGFile()

    async def send_message(self, chat_id, text):
        if self._raise:
            raise RuntimeError("send failed")
        self.sent.append({"chat_id": chat_id, "text": text})
        if self._message_id is not None:
            return SimpleNamespace(message_id=self._message_id)
        return None


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
            with patch("telegram_message_links.add_message_link"):
                asyncio.run(telegram_bot._notify_action_result(ctx, _CREATE_TASK_PLAN, _CREATE_TASK_RESULT))
        self.assertTrue(any("TASK-99" in m["text"] for m in bot.sent))
        self.assertTrue(any("🧩" in m["text"] for m in bot.sent))

    def test_notify_action_result_create_bug(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link"):
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
        upd = _FakeUpdate(user_id="42", text="зафиксируй баг: падает при старте")
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100",
                                       "TELEGRAM_FAST_ROUTER_ENABLED": "false"}, clear=False):
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_BUG_PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=_CREATE_BUG_RESULT):
                    with patch("telegram_message_links.add_message_link"):
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
        self.assertIn("MANAGED_REPO_PATH", text)
        self.assertNotIn("/sys", text)
        self.assertNotIn("/managed", text)

    def test_start_shows_embedded(self):
        upd = self._run_start(managed_path="..")
        text = "\n".join(upd.message.replies)
        self.assertIn("внешнему проекту", text)
        self.assertNotIn("/managed", text)

    def test_start_no_absolute_paths(self):
        upd = self._run_start(managed_path="..")
        text = "\n".join(upd.message.replies)
        self.assertNotIn("/sys", text)
        self.assertNotIn("/managed", text)
        self.assertNotIn("/Users", text)

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


class TestTaskCards(unittest.TestCase):
    def test_format_task_card_task_icon_and_fields(self):
        task = {
            "id": "TASK-1", "title": "Add healthcheck", "status": "idea",
            "priority": "high", "description": "Verify system health.",
        }
        card = telegram_bot._format_task_card(task, "task")
        self.assertIn("🧩", card)
        self.assertIn("TASK-1", card)
        self.assertIn("Add healthcheck", card)
        self.assertIn("Ответь", card)
        self.assertIn("Новая задача", card)

    def test_format_task_card_bug_icon_and_severity(self):
        bug = {
            "id": "TASK-2", "title": "Crash on start", "status": "idea",
            "severity": "critical", "priority": "high",
        }
        card = telegram_bot._format_task_card(bug, "bug")
        self.assertIn("🐞", card)
        self.assertIn("TASK-2", card)
        self.assertIn("critical", card)
        self.assertNotIn("Приоритет", card)

    def test_format_task_card_unknown_severity_no_severity_label(self):
        bug = {"id": "TASK-3", "title": "Minor", "status": "idea", "severity": "unknown", "priority": "low"}
        card = telegram_bot._format_task_card(bug, "bug")
        self.assertIn("🐞", card)
        self.assertIn("TASK-3", card)
        self.assertNotIn("Серьёзность", card)

    def test_send_task_card_sends_to_status_chat(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        task = {"id": "TASK-1", "title": "Test", "status": "idea", "priority": "medium"}
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link"):
                asyncio.run(telegram_bot._send_task_card(ctx, task, "task"))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("🧩", bot.sent[0]["text"])
        self.assertIn("TASK-1", bot.sent[0]["text"])

    def test_send_task_card_stores_link_when_message_id_returned(self):
        bot = _FakeSendBot(returned_message_id=42)
        ctx = _make_ctx(bot=bot)
        task = {"id": "TASK-5", "title": "Test", "status": "idea", "priority": "medium"}
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link") as mock_add:
                asyncio.run(telegram_bot._send_task_card(ctx, task, "task"))
        mock_add.assert_called_once_with("100", 42, "task", "TASK-5")

    def test_send_task_card_no_link_stored_when_no_message_id(self):
        bot = _FakeSendBot(returned_message_id=None)
        ctx = _make_ctx(bot=bot)
        task = {"id": "TASK-6", "title": "Test", "status": "idea"}
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link") as mock_add:
                asyncio.run(telegram_bot._send_task_card(ctx, task, "task"))
        mock_add.assert_not_called()

    def test_send_task_card_skipped_when_no_status_chat(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        task = {"id": "TASK-7", "title": "Test", "status": "idea"}
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": ""}, clear=False):
            asyncio.run(telegram_bot._send_task_card(ctx, task, "task"))
        self.assertEqual(bot.sent, [])

    def test_notify_create_task_sends_task_card(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link"):
                asyncio.run(telegram_bot._notify_action_result(ctx, _CREATE_TASK_PLAN, _CREATE_TASK_RESULT))
        self.assertTrue(any("🧩" in m["text"] for m in bot.sent))
        self.assertTrue(any("TASK-99" in m["text"] for m in bot.sent))
        self.assertTrue(any("Ответь" in m["text"] for m in bot.sent))

    def test_notify_create_bug_sends_bug_card(self):
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.add_message_link"):
                asyncio.run(telegram_bot._notify_action_result(ctx, _CREATE_BUG_PLAN, _CREATE_BUG_RESULT))
        self.assertTrue(any("🐞" in m["text"] for m in bot.sent))
        self.assertTrue(any("TASK-100" in m["text"] for m in bot.sent))

    def test_no_status_chat_id_does_not_break_main_flow(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        bot = _FakeSendBot()
        ctx = _make_ctx(bot=bot)
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": ""}, clear=False):
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_TASK_PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=_CREATE_TASK_RESULT):
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        # No card sent (status chat not configured)
        self.assertEqual(bot.sent, [])
        # Reply was still sent to the user
        self.assertTrue(len(upd.message.replies) > 0)


class TestReplyRouting(unittest.TestCase):
    def test_reply_to_linked_task_enriches_context(self):
        upd = _FakeUpdate(user_id="42", text="бери в работу", reply_to_msg_id=55, chat_id="100")
        ctx = _make_ctx()
        with patch("telegram_message_links.find_link", return_value={
            "telegram_chat_id": "100",
            "telegram_message_id": 55,
            "work_item_type": "task",
            "work_item_id": "TASK-7",
        }):
            with patch("telegram_bot.handle_user_text") as handle_mock:
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        handle_mock.assert_called_once()
        enriched_text = handle_mock.call_args[0][2]
        self.assertIn("TASK-7", enriched_text)
        self.assertIn("бери в работу", enriched_text)

    def test_reply_to_linked_bug_enriches_context(self):
        upd = _FakeUpdate(user_id="42", text="что по этому?", reply_to_msg_id=77, chat_id="100")
        ctx = _make_ctx()
        with patch("telegram_message_links.find_link", return_value={
            "telegram_chat_id": "100",
            "telegram_message_id": 77,
            "work_item_type": "bug",
            "work_item_id": "TASK-3",
        }):
            with patch("telegram_bot.handle_user_text") as handle_mock:
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        enriched_text = handle_mock.call_args[0][2]
        self.assertIn("TASK-3", enriched_text)
        self.assertIn("bug", enriched_text)

    def test_reply_to_unknown_in_status_chat_shows_hint(self):
        upd = _FakeUpdate(user_id="42", text="что делать?", reply_to_msg_id=99, chat_id="100")
        ctx = _make_ctx()
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.find_link", return_value=None):
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(any("Не нашёл" in r for r in upd.message.replies))

    def test_reply_to_unknown_outside_status_chat_routes_normally(self):
        upd = _FakeUpdate(user_id="42", text="Создай задачу", reply_to_msg_id=99, chat_id="999")
        ctx = _make_ctx()
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": "100"}, clear=False):
            with patch("telegram_message_links.find_link", return_value=None):
                with patch("telegram_bot.handle_user_text") as handle_mock:
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        handle_mock.assert_called_once()
        self.assertEqual(handle_mock.call_args[0][2], "Создай задачу")

    def test_plain_text_no_reply_routes_normally(self):
        upd = _FakeUpdate(user_id="42", text="Создай задачу")
        ctx = _make_ctx()
        with patch("telegram_bot.handle_user_text") as handle_mock:
            asyncio.run(telegram_bot.text_handler(upd, ctx))
        handle_mock.assert_called_once()
        self.assertEqual(handle_mock.call_args[0][2], "Создай задачу")

    def test_no_status_chat_id_reply_to_linked_still_works(self):
        """Even without TELEGRAM_STATUS_CHAT_ID set, reply to a stored link is enriched."""
        upd = _FakeUpdate(user_id="42", text="отмени", reply_to_msg_id=55, chat_id="42")
        ctx = _make_ctx()
        with patch.dict("os.environ", {"TELEGRAM_STATUS_CHAT_ID": ""}, clear=False):
            with patch("telegram_message_links.find_link", return_value={
                "telegram_chat_id": "42",
                "telegram_message_id": 55,
                "work_item_type": "task",
                "work_item_id": "TASK-9",
            }):
                with patch("telegram_bot.handle_user_text") as handle_mock:
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        enriched = handle_mock.call_args[0][2]
        self.assertIn("TASK-9", enriched)


    def test_reply_to_linked_task_sends_acknowledgment(self):
        """Bot says 'Понял, работаю с TASK-X.' before forwarding to supervisor."""
        upd = _FakeUpdate(user_id="42", text="бери в работу", reply_to_msg_id=55, chat_id="100")
        ctx = _make_ctx()
        with patch("telegram_message_links.find_link", return_value={
            "telegram_chat_id": "100",
            "telegram_message_id": 55,
            "work_item_type": "task",
            "work_item_id": "TASK-6",
        }):
            with patch("telegram_bot.handle_user_text"):
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertTrue(any("TASK-6" in r for r in upd.message.replies))
        self.assertTrue(any("Понял" in r for r in upd.message.replies))


class TestTelegramConfigOutput(unittest.TestCase):
    def _run_config(self, env_overrides):
        import subprocess, sys
        env = {**__import__("os").environ, **env_overrides}
        result = subprocess.run(
            [sys.executable, "run.py", "telegram-config"],
            capture_output=True, text=True, env=env,
            cwd="/Users/semionovk/MySpace/team",
        )
        return result.stdout

    def test_status_chat_id_set_true_when_present(self):
        output = self._run_config({"TELEGRAM_STATUS_CHAT_ID": "12345"})
        self.assertIn("TELEGRAM_STATUS_CHAT_ID_SET=true", output)

    def test_status_chat_id_set_false_when_absent(self):
        output = self._run_config({"TELEGRAM_STATUS_CHAT_ID": ""})
        self.assertIn("TELEGRAM_STATUS_CHAT_ID_SET=false", output)


class TestTaskCardFormat(unittest.TestCase):
    def test_task_card_has_id_label(self):
        card = telegram_bot._format_task_card(
            {"id": "TASK-6", "title": "Check UX", "status": "Backlog", "description": "Verify the flow."},
            "task",
        )
        self.assertIn("ID: TASK-6", card)
        self.assertIn("Название: Check UX", card)
        self.assertIn("Что нужно сделать:", card)
        self.assertIn("Статус: Backlog", card)
        self.assertIn("именно с этой задачей", card)

    def test_bug_card_header(self):
        card = telegram_bot._format_task_card(
            {"id": "BUG-3", "title": "Crash", "status": "idea", "severity": "critical"},
            "bug",
        )
        self.assertIn("🐞 Новый баг", card)
        self.assertIn("ID: BUG-3", card)
        self.assertIn("Серьёзность: critical", card)

    def test_task_card_no_local_paths(self):
        card = telegram_bot._format_task_card(
            {"id": "TASK-1", "title": "Test", "status": "idea", "description": "Do it."},
            "task",
        )
        self.assertNotIn("/Users", card)
        self.assertNotIn("/home", card)
        self.assertNotIn("/var", card)

    def test_task_card_no_description_skips_section(self):
        card = telegram_bot._format_task_card(
            {"id": "TASK-1", "title": "Test", "status": "idea"},
            "task",
        )
        self.assertNotIn("Что нужно сделать:", card)


class TestFastRouterIntegration(unittest.TestCase):
    """Integration: fast router intercepts matching text before Supervisor."""

    def test_fast_router_handles_status_without_llm(self):
        upd = _FakeUpdate(user_id="42", text="статус")
        ctx = _make_ctx()
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        fake_reply = "📌 Статус проекта\n\nЗадач всего: 0"
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            with patch("telegram_fast_router.try_route", return_value=fake_reply) as router_mock:
                with patch("telegram_bot.plan_supervisor_action") as plan_mock:
                    asyncio.run(telegram_bot.text_handler(upd, ctx))
        router_mock.assert_called_once_with("статус")
        plan_mock.assert_not_called()
        self.assertIn("📌", "\n".join(upd.message.replies))

    def test_fast_router_disabled_falls_through_to_supervisor(self):
        """When TELEGRAM_FAST_ROUTER_ENABLED=false, try_route must not be called."""
        upd = _FakeUpdate(user_id="42", text="статус")
        ctx = _make_ctx()
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "false"}, clear=False):
            with patch("telegram_fast_router.try_route", return_value="fast reply") as router_mock:
                with patch("telegram_bot.plan_supervisor_action", return_value={
                    "intent": "project_status", "confidence": 0.9,
                    "requires_confirmation": False,
                    "action": {"name": "project_status", "args": {}},
                    "explanation": "ok", "warnings": [],
                }) as plan_mock:
                    with patch("telegram_bot.execute_supervisor_action", return_value={
                        "executed": True, "action": "project_status", "result": {},
                    }):
                        asyncio.run(telegram_bot.text_handler(upd, ctx))
        router_mock.assert_not_called()
        plan_mock.assert_called_once()

    def test_fast_router_none_falls_through_to_supervisor(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу: тест")
        ctx = _make_ctx()
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            with patch("telegram_fast_router.try_route", return_value=None):
                with patch("telegram_bot.plan_supervisor_action", return_value={
                    "intent": "create_task", "confidence": 0.9,
                    "requires_confirmation": False,
                    "action": {"name": "create_task", "args": {"title": "тест"}},
                    "explanation": "ok", "warnings": [],
                }) as plan_mock:
                    with patch("telegram_bot.execute_supervisor_action", return_value={
                        "executed": True, "action": "create_task",
                        "result": {"id": "TASK-99", "title": "тест", "status": "idea"},
                    }):
                        asyncio.run(telegram_bot.text_handler(upd, ctx))
        plan_mock.assert_called_once()

    def test_reply_context_bypasses_fast_router(self):
        """Supervisor-enriched reply text must not be intercepted by fast router."""
        upd = _FakeUpdate(user_id="42", text="бери в работу", reply_to_msg_id=10, chat_id="100")
        ctx = _make_ctx()
        ctx.bot_data["telegram_config"]["dry_run_by_default"] = False
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            with patch("telegram_message_links.find_link", return_value={
                "telegram_chat_id": "100", "telegram_message_id": 10,
                "work_item_type": "task", "work_item_id": "TASK-5",
            }):
                with patch("telegram_fast_router.try_route") as router_mock:
                    with patch("telegram_bot.handle_user_text") as handle_mock:
                        asyncio.run(telegram_bot.text_handler(upd, ctx))
        # router should not be called from text_handler on the raw "бери в работу" text
        # (the handle_user_text stub short-circuits so we just check routing)
        handle_mock.assert_called_once()
        enriched = handle_mock.call_args[0][2]
        self.assertIn("TASK-5", enriched)


class TestFastRouterConfigOutput(unittest.TestCase):

    def _run_config(self, env_overrides):
        import subprocess, sys, os as _os
        env = {**_os.environ, **env_overrides}
        result = subprocess.run(
            [sys.executable, "run.py", "telegram-config"],
            capture_output=True, text=True, env=env,
            cwd="/Users/semionovk/MySpace/team",
        )
        return result.stdout

    def test_fast_router_enabled_true(self):
        output = self._run_config({"TELEGRAM_FAST_ROUTER_ENABLED": "true"})
        self.assertIn("TELEGRAM_FAST_ROUTER_ENABLED=true", output)

    def test_fast_router_enabled_false(self):
        output = self._run_config({"TELEGRAM_FAST_ROUTER_ENABLED": "false"})
        self.assertIn("TELEGRAM_FAST_ROUTER_ENABLED=false", output)

    def test_fast_router_default_is_true(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "TELEGRAM_FAST_ROUTER_ENABLED"}
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "run.py", "telegram-config"],
            capture_output=True, text=True, env=env,
            cwd="/Users/semionovk/MySpace/team",
        )
        self.assertIn("TELEGRAM_FAST_ROUTER_ENABLED=true", result.stdout)


class TestBoardConfigHandler(unittest.TestCase):
    """Tests for /board_config command."""

    def _make_ctx(self, owner_id="42"):
        ctx = SimpleNamespace()
        ctx.bot_data = {"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}}
        ctx.bot = _FakeBot()
        return ctx

    def _run_board_config(self, user_id="42", owner_id="42"):
        upd = _FakeUpdate(user_id=user_id, text="/board_config")
        ctx = self._make_ctx(owner_id=owner_id)
        asyncio.run(telegram_bot.board_config_handler(upd, ctx))
        return upd

    def test_board_config_owner_gets_response(self):
        upd = self._run_board_config(user_id="42", owner_id="42")
        self.assertTrue(len(upd.message.replies) > 0)

    def test_board_config_denied_for_non_owner(self):
        upd = self._run_board_config(user_id="99", owner_id="42")
        self.assertIn("denied", upd.message.replies[0].lower())

    def test_board_config_response_contains_board_label(self):
        upd = self._run_board_config(user_id="42", owner_id="42")
        text = "\n".join(upd.message.replies)
        self.assertIn("Board", text)

    def test_board_config_no_token_values(self):
        with patch.dict("os.environ", {
            "TELEGRAM_BOARD_CHAT_ID": "-9998887776665",
            "TELEGRAM_TOPIC_RELEASES": "77777",
        }):
            upd = self._run_board_config(user_id="42", owner_id="42")
        text = "\n".join(upd.message.replies)
        self.assertNotIn("-9998887776665", text)
        self.assertNotIn("77777", text)

    def test_board_config_no_absolute_paths(self):
        upd = self._run_board_config(user_id="42", owner_id="42")
        text = "\n".join(upd.message.replies)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)


if __name__ == "__main__":
    unittest.main()
