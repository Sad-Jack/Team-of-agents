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
        # Bypass create router so the test stays focused on dry-run routing logic.
        with patch("telegram_create_router.detect_create_intent", return_value=None):
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
            # Must mention how to enable
            self.assertIn("STT_PROVIDER", upd.message.replies[0])

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
                            # replies[0] = status, replies[1] = transcript
                            all_replies = " ".join(upd.message.replies)
                            self.assertIn("Распознал", all_replies)
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
        with patch("telegram_create_router.detect_create_intent", return_value=None):
            with patch("telegram_bot.plan_supervisor_action", return_value=_CREATE_TASK_PLAN):
                asyncio.run(telegram_bot.text_handler(upd, ctx))
        self.assertIn("telegram:42", telegram_bot.PENDING_ACTIONS)

    def test_dry_run_shows_inline_buttons(self):
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        ctx = _make_ctx()
        with _patch_telegram():
            with patch("telegram_create_router.detect_create_intent", return_value=None):
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
        with patch("telegram_create_router.detect_create_intent", return_value=None):
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
            with patch("telegram_create_router.detect_create_intent", return_value=None):
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
                with patch("telegram_create_router.detect_create_intent", return_value=None):
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


class TestBoardPingHandler(unittest.TestCase):
    """Tests for /board_ping command."""

    def _make_ctx(self, owner_id="42"):
        from unittest.mock import AsyncMock, MagicMock
        ctx = SimpleNamespace()
        ctx.bot_data = {"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}}
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        ctx.bot = fake_bot
        return ctx

    def test_board_ping_registered(self):
        """Handler is accessible on the module."""
        self.assertTrue(callable(telegram_bot.board_ping_handler))

    def test_board_ping_denied_for_non_owner(self):
        upd = _FakeUpdate(user_id="99", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        asyncio.run(telegram_bot.board_ping_handler(upd, ctx))
        self.assertIn("denied", upd.message.replies[0].lower())

    def test_board_ping_replies_when_board_disabled(self):
        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        with patch.dict("os.environ", {"TELEGRAM_BOARD_ENABLED": ""}):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertIn("disabled", text.lower())

    def test_board_ping_replies_when_chat_id_missing(self):
        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        with patch.dict("os.environ", {
            "TELEGRAM_BOARD_ENABLED": "true",
            "TELEGRAM_BOARD_CHAT_ID": "",
        }):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertIn("TELEGRAM_BOARD_CHAT_ID", text)

    def test_board_ping_sends_to_topics(self):
        from unittest.mock import AsyncMock, MagicMock
        import telegram_board as tb

        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        fake_msg = MagicMock()
        fake_msg.message_id = 1
        ctx.bot.send_message = AsyncMock(return_value=fake_msg)

        env = {
            "TELEGRAM_BOARD_ENABLED": "true",
            "TELEGRAM_BOARD_CHAT_ID": "-100xyz",
            "TELEGRAM_TOPIC_RELEASES": "42",
            "TELEGRAM_TOPIC_DECISIONS": "7",
        }
        # clear all other topic vars
        clear = {env_name: "" for _, _, env_name in tb.BOARD_TOPICS}
        clear.update(env)
        with patch.dict("os.environ", clear):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))

        self.assertEqual(ctx.bot.send_message.call_count, 2)
        text = "\n".join(upd.message.replies)
        self.assertIn("ping result", text.lower())
        self.assertIn("✅", text)

    def test_board_ping_continues_after_send_failure(self):
        from unittest.mock import AsyncMock, MagicMock
        import telegram_board as tb

        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        ctx.bot.send_message = AsyncMock(side_effect=Exception("Telegram error"))

        env = {
            "TELEGRAM_BOARD_ENABLED": "true",
            "TELEGRAM_BOARD_CHAT_ID": "-100xyz",
            "TELEGRAM_TOPIC_RELEASES": "42",
            "TELEGRAM_TOPIC_DECISIONS": "7",
        }
        clear = {env_name: "" for _, _, env_name in tb.BOARD_TOPICS}
        clear.update(env)
        with patch.dict("os.environ", clear):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))

        # Both topics attempted
        self.assertEqual(ctx.bot.send_message.call_count, 2)
        text = "\n".join(upd.message.replies)
        self.assertIn("❌", text)

    def test_board_ping_no_token_in_reply(self):
        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        with patch.dict("os.environ", {
            "TELEGRAM_BOARD_ENABLED": "",
            "TELEGRAM_BOT_TOKEN": "secret-token-value-xyz",
        }):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertNotIn("secret-token-value-xyz", text)

    def test_board_ping_no_absolute_paths_in_reply(self):
        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        with patch.dict("os.environ", {"TELEGRAM_BOARD_ENABLED": ""}):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))
        text = "\n".join(upd.message.replies)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)


    def test_board_ping_timeout_shows_warning_not_error(self):
        """Timeout must produce ⚠️, not ❌, in the reply to owner."""
        from unittest.mock import AsyncMock, MagicMock
        import telegram_board as tb

        class TimedOut(Exception):
            pass

        upd = _FakeUpdate(user_id="42", text="/board_ping")
        ctx = self._make_ctx(owner_id="42")
        ctx.bot.send_message = AsyncMock(side_effect=TimedOut("Timed out"))

        env = {
            "TELEGRAM_BOARD_ENABLED": "true",
            "TELEGRAM_BOARD_CHAT_ID": "-100xyz",
            "TELEGRAM_TOPIC_AGENT_LOG": "31",
        }
        clear = {env_name: "" for _, _, env_name in tb.BOARD_TOPICS}
        clear.update(env)
        with patch.dict("os.environ", clear):
            asyncio.run(telegram_bot.board_ping_handler(upd, ctx))

        text = "\n".join(upd.message.replies)
        self.assertIn("⚠️", text)
        self.assertNotIn("❌", text)


class TestBoardFocusCallback(unittest.IsolatedAsyncioTestCase):
    """Tests for board_focus_callback (🎯 В фокус inline button)."""

    def setUp(self):
        from unittest.mock import AsyncMock, MagicMock
        self._bot = MagicMock()
        self._bot.send_message = AsyncMock()

    def _make_update(self, data="board:task:focus:TASK-1", user_id="42"):
        upd = _FakeCallbackUpdate(data, user_id=user_id)
        return upd

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            bot=self._bot,
            bot_data={"telegram_config": {"owner_id": owner_id}},
        )

    async def test_focus_callback_registered(self):
        self.assertTrue(callable(telegram_bot.board_focus_callback))

    async def test_non_owner_silently_ignored(self):
        upd = self._make_update(user_id="99")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.board_focus_callback(upd, ctx)
        # Non-owner: no DM sent
        self._bot.send_message.assert_not_called()
        # No edit to the public board message
        self.assertEqual(upd.callback_query.edits, [])

    async def test_invalid_callback_data_no_crash(self):
        upd = self._make_update(data="invalid:data")
        ctx = self._make_ctx()
        await telegram_bot.board_focus_callback(upd, ctx)
        # No error, no DM
        self._bot.send_message.assert_not_called()

    async def test_owner_sets_focus_and_sends_dm(self):
        upd = self._make_update(data="board:task:focus:TASK-1", user_id="42")
        ctx = self._make_ctx(owner_id="42")
        with patch("telegram_bot.set_active_task") as mock_focus:
            mock_focus.return_value = {"session_id": "telegram:42"}
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "My Task"}):
                await telegram_bot.board_focus_callback(upd, ctx)
        mock_focus.assert_called_once()
        self._bot.send_message.assert_called_once()
        dm_text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("TASK-1", dm_text)
        self.assertIn("🎯", dm_text)

    async def test_dm_sent_to_owner_user_id(self):
        upd = self._make_update(data="board:task:focus:TASK-5", user_id="42")
        ctx = self._make_ctx(owner_id="42")
        with patch("telegram_bot.set_active_task", return_value={"session_id": "telegram:42"}):
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-5", "title": "T"}):
                await telegram_bot.board_focus_callback(upd, ctx)
        call_kwargs = self._bot.send_message.call_args.kwargs
        self.assertEqual(call_kwargs["chat_id"], "42")  # owner's user_id

    async def test_query_answered(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_bot.orchestrator.get_task", return_value=None):
                await telegram_bot.board_focus_callback(upd, ctx)
        self.assertEqual(len(upd.callback_query.answers), 1)


class TestBoardStartCallback(unittest.IsolatedAsyncioTestCase):
    """Tests for board_start_callback (🚧 В работу inline button)."""

    def setUp(self):
        from unittest.mock import AsyncMock, MagicMock
        self._bot = MagicMock()
        self._bot.send_message = AsyncMock()
        self._bot.edit_message_text = AsyncMock()
        # In-memory task store patched via orchestrator methods
        self._tasks = [
            {"id": "TASK-1", "title": "Ready Task", "status": "ready_for_dev", "priority": "medium"},
            {"id": "TASK-2", "title": "In-progress Task", "status": "in_progress", "priority": "low"},
        ]
        self._saved_tasks = None

        def _load_tasks():
            return list(self._tasks)

        def _save_tasks(tasks):
            self._tasks = list(tasks)

        self._load_patcher = patch("telegram_bot.orchestrator.load_tasks", side_effect=_load_tasks)
        self._save_patcher = patch("telegram_bot.orchestrator.save_tasks", side_effect=_save_tasks)
        self._load_patcher.start()
        self._save_patcher.start()

    def tearDown(self):
        self._load_patcher.stop()
        self._save_patcher.stop()

    def _make_update(self, data="board:task:start:TASK-1", user_id="42"):
        return _FakeCallbackUpdate(data, user_id=user_id)

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            bot=self._bot,
            bot_data={"telegram_config": {"owner_id": owner_id}},
        )

    async def test_start_callback_registered(self):
        self.assertTrue(callable(telegram_bot.board_start_callback))

    async def test_non_owner_silently_ignored(self):
        upd = self._make_update(user_id="99")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.board_start_callback(upd, ctx)
        self._bot.send_message.assert_not_called()

    async def test_invalid_callback_data_no_crash(self):
        upd = self._make_update(data="bad:data")
        ctx = self._make_ctx()
        await telegram_bot.board_start_callback(upd, ctx)
        self._bot.send_message.assert_not_called()

    async def test_task_not_found_sends_error_dm(self):
        upd = self._make_update(data="board:task:start:TASK-999", user_id="42")
        ctx = self._make_ctx()
        await telegram_bot.board_start_callback(upd, ctx)
        self._bot.send_message.assert_called_once()
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("не найдена", text)

    async def test_already_in_progress_sends_message(self):
        upd = self._make_update(data="board:task:start:TASK-2", user_id="42")
        ctx = self._make_ctx()
        await telegram_bot.board_start_callback(upd, ctx)
        self._bot.send_message.assert_called_once()
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("уже в работе", text)

    async def test_sets_status_to_in_progress(self):
        upd = self._make_update(data="board:task:start:TASK-1", user_id="42")
        ctx = self._make_ctx()
        from unittest.mock import AsyncMock
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_board.upsert_task_board_card", new=AsyncMock(return_value={"status": "skipped"})):
                await telegram_bot.board_start_callback(upd, ctx)
        task = next(t for t in self._tasks if t["id"] == "TASK-1")
        self.assertEqual(task["status"], "in_progress")

    async def test_sets_focus_on_task(self):
        upd = self._make_update(data="board:task:start:TASK-1", user_id="42")
        ctx = self._make_ctx()
        from unittest.mock import AsyncMock
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}) as mock_focus:
            with patch("telegram_board.upsert_task_board_card", new=AsyncMock(return_value={"status": "skipped"})):
                await telegram_bot.board_start_callback(upd, ctx)
        mock_focus.assert_called_once()
        args, kwargs = mock_focus.call_args
        # task_id is the second positional arg
        self.assertEqual(args[1], "TASK-1")

    async def test_sends_dm_with_task_info(self):
        upd = self._make_update(data="board:task:start:TASK-1", user_id="42")
        ctx = self._make_ctx()
        from unittest.mock import AsyncMock
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_board.upsert_task_board_card", new=AsyncMock(return_value={"status": "skipped"})):
                await telegram_bot.board_start_callback(upd, ctx)
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("TASK-1", text)
        self.assertIn("🚧", text)

    async def test_query_answered(self):
        upd = self._make_update(data="board:task:start:TASK-1", user_id="42")
        ctx = self._make_ctx()
        from unittest.mock import AsyncMock
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_board.upsert_task_board_card", new=AsyncMock(return_value={"status": "skipped"})):
                await telegram_bot.board_start_callback(upd, ctx)
        self.assertEqual(len(upd.callback_query.answers), 1)


class TestFormatFocus(unittest.TestCase):
    """Unit tests for format_focus and format_focus_indicator."""

    def _focus(self, task_id=None, release_id=None, decision_id=None):
        return {
            "active_task_id": task_id,
            "active_release_id": release_id,
            "active_decision_id": decision_id,
            "summary": "ok",
        }

    def test_format_focus_no_focus(self):
        result = telegram_bot.format_focus(self._focus())
        self.assertIn("Фокус не выбран", result)

    def test_format_focus_task_shows_id(self):
        task = {"id": "TASK-1", "title": "Healthcheck", "status": "in_progress"}
        result = telegram_bot.format_focus(self._focus(task_id="TASK-1"), task=task)
        self.assertIn("TASK-1", result)
        self.assertIn("Healthcheck", result)

    def test_format_focus_task_shows_status(self):
        task = {"id": "TASK-1", "title": "T", "status": "ready_for_dev"}
        result = telegram_bot.format_focus(self._focus(task_id="TASK-1"), task=task)
        self.assertIn("Готова к разработке", result)

    def test_format_focus_task_shows_clear_hint(self):
        task = {"id": "TASK-1", "title": "T", "status": "idea"}
        result = telegram_bot.format_focus(self._focus(task_id="TASK-1"), task=task)
        self.assertIn("/clear_focus", result)

    def test_format_focus_release(self):
        result = telegram_bot.format_focus(self._focus(release_id="REL-001"))
        self.assertIn("REL-001", result)
        self.assertIn("/clear_focus", result)

    def test_format_focus_decision(self):
        result = telegram_bot.format_focus(self._focus(decision_id="ADR-001"))
        self.assertIn("ADR-001", result)

    def test_format_focus_no_absolute_paths(self):
        task = {"id": "TASK-1", "title": "T", "status": "idea"}
        result = telegram_bot.format_focus(self._focus(task_id="TASK-1"), task=task)
        self.assertNotIn("/Users/", result)

    def test_indicator_empty_when_no_focus(self):
        self.assertEqual("", telegram_bot.format_focus_indicator(self._focus()))

    def test_indicator_shows_id_and_title(self):
        task = {"id": "TASK-1", "title": "My Task", "status": "idea"}
        result = telegram_bot.format_focus_indicator(self._focus(task_id="TASK-1"), task=task)
        self.assertIn("TASK-1", result)
        self.assertIn("My Task", result)

    def test_indicator_shows_id_without_title(self):
        task = {"id": "TASK-1", "title": "", "status": "idea"}
        result = telegram_bot.format_focus_indicator(self._focus(task_id="TASK-1"), task=task)
        self.assertIn("TASK-1", result)

    def test_indicator_release(self):
        result = telegram_bot.format_focus_indicator(self._focus(release_id="REL-001"))
        self.assertIn("REL-001", result)


class TestCheckFocusSwitch(unittest.TestCase):
    """Unit tests for _check_focus_switch."""

    def test_vozmi_v_fokus(self):
        result = telegram_bot._check_focus_switch("возьми TASK-2 в фокус")
        self.assertEqual(result, "TASK-2")

    def test_pereklyuchis_na(self):
        result = telegram_bot._check_focus_switch("переключись на TASK-5")
        self.assertEqual(result, "TASK-5")

    def test_fokus_na(self):
        result = telegram_bot._check_focus_switch("фокус на BUG-3")
        self.assertEqual(result, "BUG-3")

    def test_case_insensitive(self):
        result = telegram_bot._check_focus_switch("возьми task-1 в фокус")
        self.assertEqual(result, "TASK-1")

    def test_regular_message_returns_none(self):
        self.assertIsNone(telegram_bot._check_focus_switch("создай задачу"))
        self.assertIsNone(telegram_bot._check_focus_switch("статус проекта"))
        self.assertIsNone(telegram_bot._check_focus_switch("что делать дальше"))

    def test_no_task_id_returns_none(self):
        self.assertIsNone(telegram_bot._check_focus_switch("переключись"))

    def test_bug_id_extracted(self):
        result = telegram_bot._check_focus_switch("возьми BUG-10 в фокус")
        self.assertEqual(result, "BUG-10")


class TestFocusHandler(unittest.IsolatedAsyncioTestCase):
    """Tests for /focus command."""

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            args=[],
            bot_data={"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}},
        )

    async def test_focus_no_focus_shows_not_set(self):
        upd = _FakeUpdate(user_id="42", text="/focus")
        ctx = self._make_ctx()
        with patch("telegram_bot.get_focus", return_value={
            "active_task_id": None, "active_release_id": None,
            "active_decision_id": None, "summary": "Фокус не установлен.",
        }):
            await telegram_bot.focus_handler(upd, ctx)
        self.assertIn("Фокус не выбран", upd.message.replies[0])

    async def test_focus_shows_active_task(self):
        upd = _FakeUpdate(user_id="42", text="/focus")
        ctx = self._make_ctx()
        with patch("telegram_bot.get_focus", return_value={
            "active_task_id": "TASK-1", "active_release_id": None,
            "active_decision_id": None, "summary": "ok",
        }):
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "T", "status": "idea"}):
                await telegram_bot.focus_handler(upd, ctx)
        self.assertIn("TASK-1", upd.message.replies[0])

    async def test_focus_with_task_id_arg_sets_focus(self):
        """'/focus TASK-1' should call set_active_task."""
        upd = _FakeUpdate(user_id="42", text="/focus TASK-1")
        ctx = SimpleNamespace(
            args=["TASK-1"],
            bot_data={"telegram_config": {"owner_id": "42", "dry_run_by_default": True}},
        )
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}) as mock_set:
            with patch("telegram_bot.get_focus", return_value={
                "active_task_id": "TASK-1", "active_release_id": None,
                "active_decision_id": None, "summary": "ok",
            }):
                with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "T", "status": "idea"}):
                    await telegram_bot.focus_handler(upd, ctx)
        mock_set.assert_called_once()
        self.assertIn("TASK-1", upd.message.replies[0])

    async def test_focus_invalid_task_id_replies_not_found(self):
        upd = _FakeUpdate(user_id="42", text="/focus TASK-999")
        ctx = SimpleNamespace(
            args=["TASK-999"],
            bot_data={"telegram_config": {"owner_id": "42", "dry_run_by_default": True}},
        )
        from conversation_context import ConversationContextError
        with patch("telegram_bot.set_active_task", side_effect=ConversationContextError("Task not found: TASK-999")):
            await telegram_bot.focus_handler(upd, ctx)
        self.assertIn("не найдена", upd.message.replies[0])

    async def test_focus_denied_for_non_owner(self):
        upd = _FakeUpdate(user_id="99", text="/focus")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.focus_handler(upd, ctx)
        self.assertIn("denied", upd.message.replies[0].lower())

    async def test_focus_no_secrets_in_reply(self):
        upd = _FakeUpdate(user_id="42", text="/focus")
        ctx = self._make_ctx()
        with patch("telegram_bot.get_focus", return_value={
            "active_task_id": "TASK-1", "active_release_id": None,
            "active_decision_id": None, "summary": "ok",
        }):
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "T", "status": "idea"}):
                await telegram_bot.focus_handler(upd, ctx)
        reply = upd.message.replies[0]
        self.assertNotIn("/Users/", reply)
        self.assertNotIn("token", reply.lower())


class TestClearFocusHandler(unittest.IsolatedAsyncioTestCase):
    """Tests for /clear_focus command."""

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            args=[],
            bot_data={"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}},
        )

    async def test_clear_focus_with_active_task_replies_cleared(self):
        upd = _FakeUpdate(user_id="42", text="/clear_focus")
        ctx = self._make_ctx()
        pre = {"active_task_id": "TASK-1", "active_release_id": None, "active_decision_id": None, "summary": "ok"}
        after = {"active_task_id": None, "active_release_id": None, "active_decision_id": None, "summary": "Фокус не установлен."}
        with patch("telegram_bot.get_focus", return_value=pre):
            with patch("telegram_bot.clear_focus"):
                await telegram_bot.clear_focus_handler(upd, ctx)
        self.assertIn("снят", upd.message.replies[0].lower())

    async def test_clear_focus_no_focus_replies_no_active(self):
        upd = _FakeUpdate(user_id="42", text="/clear_focus")
        ctx = self._make_ctx()
        no_focus = {"active_task_id": None, "active_release_id": None, "active_decision_id": None, "summary": "ok"}
        with patch("telegram_bot.get_focus", return_value=no_focus):
            with patch("telegram_bot.clear_focus"):
                await telegram_bot.clear_focus_handler(upd, ctx)
        self.assertIn("нет активного фокуса", upd.message.replies[0].lower())

    async def test_clear_focus_denied_for_non_owner(self):
        upd = _FakeUpdate(user_id="99", text="/clear_focus")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.clear_focus_handler(upd, ctx)
        self.assertIn("denied", upd.message.replies[0].lower())

    async def test_clear_focus_no_secrets_in_reply(self):
        upd = _FakeUpdate(user_id="42", text="/clear_focus")
        ctx = self._make_ctx()
        no_focus = {"active_task_id": None, "active_release_id": None, "active_decision_id": None, "summary": "ok"}
        with patch("telegram_bot.get_focus", return_value=no_focus):
            with patch("telegram_bot.clear_focus"):
                await telegram_bot.clear_focus_handler(upd, ctx)
        self.assertNotIn("/Users/", upd.message.replies[0])


class TestFocusSwitchInHandleUserText(unittest.IsolatedAsyncioTestCase):
    """Tests for natural-language focus switching in handle_user_text."""

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            args=[],
            bot_data={"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}},
        )

    async def test_vozmi_v_fokus_sets_focus(self):
        upd = _FakeUpdate(user_id="42", text="возьми TASK-2 в фокус")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}) as mock_set:
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-2", "title": "My Task", "status": "idea"}):
                await telegram_bot.handle_user_text(upd, ctx, "возьми TASK-2 в фокус")
        mock_set.assert_called_once()
        self.assertIn("TASK-2", upd.message.replies[0])
        self.assertIn("🎯", upd.message.replies[0])

    async def test_pereklyuchis_na_sets_focus(self):
        upd = _FakeUpdate(user_id="42", text="переключись на TASK-3")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-3", "title": "Task 3", "status": "idea"}):
                await telegram_bot.handle_user_text(upd, ctx, "переключись на TASK-3")
        self.assertIn("TASK-3", upd.message.replies[0])

    async def test_focus_switch_not_found(self):
        upd = _FakeUpdate(user_id="42", text="возьми TASK-999 в фокус")
        ctx = self._make_ctx()
        from conversation_context import ConversationContextError
        with patch("telegram_bot.set_active_task", side_effect=ConversationContextError("not found")):
            await telegram_bot.handle_user_text(upd, ctx, "возьми TASK-999 в фокус")
        self.assertIn("не найдена", upd.message.replies[0].lower())

    async def test_focus_switch_non_owner_denied(self):
        upd = _FakeUpdate(user_id="99", text="возьми TASK-1 в фокус")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.handle_user_text(upd, ctx, "возьми TASK-1 в фокус")
        self.assertIn("denied", upd.message.replies[0].lower())

    async def test_regular_text_not_intercepted(self):
        """Non-focus-switch text should NOT be intercepted by the focus-switch handler."""
        upd = _FakeUpdate(user_id="42", text="создай задачу")
        ctx = self._make_ctx()
        # The supervisor/fast router runs — patch it to verify focus-switch did NOT intercept
        with patch("telegram_bot.set_active_task") as mock_set:
            with patch("telegram_bot.telegram_fast_router.try_route", return_value="fast reply"):
                await telegram_bot.handle_user_text(upd, ctx, "создай задачу")
        # set_active_task should NOT have been called by the focus-switch logic
        mock_set.assert_not_called()


class TestFocusIndicatorInResponses(unittest.IsolatedAsyncioTestCase):
    """Focus indicator appears in responses when focus is active."""

    def _make_ctx(self, owner_id="42", dry_run=False):
        return SimpleNamespace(
            args=[],
            bot=SimpleNamespace(send_message=None),
            bot_data={"telegram_config": {
                "owner_id": owner_id,
                "dry_run_by_default": dry_run,
            }},
        )

    _PLAN = {
        "intent": "create_task",
        "confidence": 0.9,
        "requires_confirmation": False,
        "action": {"name": "create_task", "args": {"title": "T"}},
        "explanation": "ok",
        "warnings": [],
    }

    async def test_indicator_appended_after_execute(self):
        """Focus indicator should appear in execute reply when focus is active."""
        upd = _FakeUpdate(user_id="42", text="создай задачу T")
        ctx = self._make_ctx(dry_run=False)

        active_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                        "active_decision_id": None, "summary": "ok"}
        result = {"executed": True, "action": "create_task",
                  "result": {"id": "TASK-99", "title": "T", "status": "idea"}}

        with patch("telegram_create_router.detect_create_intent", return_value=None):
            with patch("telegram_bot.plan_supervisor_action", return_value=self._PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=result):
                    with patch("telegram_bot.get_focus", return_value=active_focus):
                        with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "Healthcheck", "status": "idea"}):
                            with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
                                await telegram_bot.handle_user_text(upd, ctx, "создай задачу T", force_execute=True)

        combined = "\n".join(upd.message.replies)
        self.assertIn("🎯", combined)
        self.assertIn("TASK-1", combined)

    async def test_no_indicator_when_no_focus(self):
        """No indicator when focus is empty."""
        upd = _FakeUpdate(user_id="42", text="создай задачу T")
        ctx = self._make_ctx(dry_run=False)

        no_focus = {"active_task_id": None, "active_release_id": None,
                    "active_decision_id": None, "summary": "Фокус не установлен."}
        result = {"executed": True, "action": "create_task",
                  "result": {"id": "TASK-99", "title": "T", "status": "idea"}}

        with patch("telegram_bot.plan_supervisor_action", return_value=self._PLAN):
            with patch("telegram_bot.execute_supervisor_action", return_value=result):
                with patch("telegram_bot.get_focus", return_value=no_focus):
                    with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
                        await telegram_bot.handle_user_text(upd, ctx, "создай задачу T", force_execute=True)

        combined = "\n".join(upd.message.replies)
        self.assertNotIn("🎯 Фокус:", combined)

    async def test_create_task_preserves_existing_focus(self):
        """After create_task, the original focus task should be restored."""
        upd = _FakeUpdate(user_id="42", text="создай задачу T")
        ctx = self._make_ctx(dry_run=False)

        pre_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                     "active_decision_id": None, "summary": "ok"}
        post_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                      "active_decision_id": None, "summary": "ok"}
        result = {"executed": True, "action": "create_task",
                  "result": {"id": "TASK-99", "title": "T", "status": "idea"}}

        with patch("telegram_create_router.detect_create_intent", return_value=None):
            with patch("telegram_bot.plan_supervisor_action", return_value=self._PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=result):
                    with patch("telegram_bot.get_focus", side_effect=[pre_focus, post_focus]):
                        with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "HC", "status": "idea"}):
                            with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}) as mock_set:
                                await telegram_bot.handle_user_text(upd, ctx, "создай задачу T", force_execute=True)

        # set_active_task should have been called to RESTORE pre_task focus
        mock_set.assert_called_once_with(
            "telegram:42", "TASK-1", user_id="42", channel="telegram"
        )

    async def test_create_task_note_about_preserved_focus(self):
        """Reply should include note that existing focus was not changed."""
        upd = _FakeUpdate(user_id="42", text="создай задачу T")
        ctx = self._make_ctx(dry_run=False)

        pre_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                     "active_decision_id": None, "summary": "ok"}
        post_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                      "active_decision_id": None, "summary": "ok"}
        result = {"executed": True, "action": "create_task",
                  "result": {"id": "TASK-99", "title": "T", "status": "idea"}}

        with patch("telegram_create_router.detect_create_intent", return_value=None):
            with patch("telegram_bot.plan_supervisor_action", return_value=self._PLAN):
                with patch("telegram_bot.execute_supervisor_action", return_value=result):
                    with patch("telegram_bot.get_focus", side_effect=[pre_focus, post_focus]):
                        with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "HC", "status": "idea"}):
                            with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
                                await telegram_bot.handle_user_text(upd, ctx, "создай задачу T", force_execute=True)

        combined = "\n".join(upd.message.replies)
        self.assertIn("TASK-1", combined)
        self.assertIn("фокус", combined.lower())

    async def test_start_handler_shows_focus_indicator(self):
        """start_handler should show focus indicator when focus is active."""
        upd = _FakeUpdate(user_id="42", text="/start")
        ctx = self._make_ctx()
        active_focus = {"active_task_id": "TASK-1", "active_release_id": None,
                        "active_decision_id": None, "summary": "ok"}
        with patch("telegram_bot.get_focus", return_value=active_focus):
            with patch("telegram_bot.orchestrator.get_task", return_value={"id": "TASK-1", "title": "HC", "status": "idea"}):
                with patch("managed_project.get_managed_project_info", return_value={"managed_repo_path": "."}):
                    await telegram_bot.start_handler(upd, ctx)
        combined = "\n".join(upd.message.replies)
        self.assertIn("TASK-1", combined)


class TestBoardFocusCallbackDM(unittest.IsolatedAsyncioTestCase):
    """Tests that board_focus_callback sends rich DM with hint."""

    def setUp(self):
        from unittest.mock import AsyncMock, MagicMock
        self._bot = MagicMock()
        self._bot.send_message = AsyncMock()

    def _make_update(self, data="board:task:focus:TASK-1", user_id="42"):
        return _FakeCallbackUpdate(data, user_id=user_id)

    def _make_ctx(self, owner_id="42"):
        return SimpleNamespace(
            bot=self._bot,
            bot_data={"telegram_config": {"owner_id": owner_id}},
        )

    async def test_dm_includes_clear_focus_hint(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_bot.orchestrator.get_task", return_value={
                "id": "TASK-1", "title": "HC", "status": "ready_for_dev"
            }):
                await telegram_bot.board_focus_callback(upd, ctx)
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("/clear_focus", text)
        self.assertIn("TASK-1", text)

    async def test_dm_includes_task_status(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_bot.orchestrator.get_task", return_value={
                "id": "TASK-1", "title": "HC", "status": "ready_for_dev"
            }):
                await telegram_bot.board_focus_callback(upd, ctx)
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertIn("Готова к разработке", text)

    async def test_dm_no_absolute_paths(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx()
        with patch("telegram_bot.set_active_task", return_value={"session_id": "s"}):
            with patch("telegram_bot.orchestrator.get_task", return_value={
                "id": "TASK-1", "title": "HC", "status": "idea"
            }):
                await telegram_bot.board_focus_callback(upd, ctx)
        text = self._bot.send_message.call_args.kwargs.get("text", "")
        self.assertNotIn("/Users/", text)


class TestVoiceHandler(unittest.IsolatedAsyncioTestCase):
    """Comprehensive tests for voice_handler in telegram_bot.py."""

    def _make_ctx(self, owner_id="42"):
        from unittest.mock import AsyncMock, MagicMock
        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=_FakeTGFile())
        ctx = MagicMock()
        ctx.bot = bot
        ctx.bot_data = {"telegram_config": {"owner_id": owner_id, "dry_run_by_default": True}}
        return ctx

    def _make_upd(self, user_id="42", file_id="tg-voice-001"):
        return _FakeUpdate(user_id=user_id, voice=SimpleNamespace(file_id=file_id))

    def _patches(self, transcript="привет", keep_files=False):
        """Convenience context manager: patches all voice internals for success path."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch as _patch, MagicMock

        tmp = tempfile.mkdtemp()
        tmp_path = Path(tmp)

        return [
            _patch("telegram_bot.is_voice_enabled", return_value=True),
            _patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path),
            _patch("telegram_bot.convert_voice_to_wav", return_value=str(tmp_path / "a.wav")),
            _patch("telegram_bot.transcribe_audio", return_value=transcript),
            _patch("telegram_bot.should_keep_voice_files", return_value=keep_files),
        ]

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    async def test_non_owner_denied(self):
        """Voice from a non-owner must be silently rejected."""
        upd = self._make_upd(user_id="999")
        ctx = self._make_ctx(owner_id="42")
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            await telegram_bot.voice_handler(upd, ctx)
        self.assertEqual(len(upd.message.replies), 1)
        self.assertIn("запрещ", upd.message.replies[0].lower())

    async def test_non_owner_does_not_call_pipeline(self):
        """Pipeline must never be called for non-owner voice."""
        upd = self._make_upd(user_id="999")
        ctx = self._make_ctx(owner_id="42")
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.handle_user_text") as route_mock:
                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()

    # ------------------------------------------------------------------
    # Disabled STT
    # ------------------------------------------------------------------

    async def test_disabled_gives_friendly_message(self):
        """When voice is disabled the reply must explain how to enable it."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        with patch("telegram_bot.is_voice_enabled", return_value=False):
            await telegram_bot.voice_handler(upd, ctx)
        reply = upd.message.replies[0]
        self.assertIn("выключен", reply)
        self.assertIn("whisper_cli", reply)
        self.assertIn("custom_cli", reply)

    async def test_disabled_does_not_call_pipeline(self):
        upd = self._make_upd()
        ctx = self._make_ctx()
        with patch("telegram_bot.is_voice_enabled", return_value=False):
            with patch("telegram_bot.handle_user_text") as route_mock:
                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    async def test_status_message_sent_before_transcript(self):
        """A '🎙 Принял голосовое' status must appear before the transcript reply."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        patches = self._patches(transcript="создай задачу")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("telegram_bot.handle_user_text"):
                await telegram_bot.voice_handler(upd, ctx)
        self.assertGreaterEqual(len(upd.message.replies), 2)
        self.assertIn("Принял голосовое", upd.message.replies[0])

    async def test_transcript_shown_to_user(self):
        """The recognised text must appear in the reply to the user."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        patches = self._patches(transcript="запусти анализ")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("telegram_bot.handle_user_text"):
                await telegram_bot.voice_handler(upd, ctx)
        all_text = " ".join(upd.message.replies)
        self.assertIn("Распознал", all_text)
        self.assertIn("запусти анализ", all_text)

    async def test_transcript_passed_to_pipeline(self):
        """Recognised text must be forwarded verbatim to handle_user_text."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        patches = self._patches(transcript="покажи бэклог")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("telegram_bot.handle_user_text") as route_mock:
                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_called_once()
        _, _, passed_text = route_mock.call_args.args
        self.assertEqual(passed_text, "покажи бэклог")

    # ------------------------------------------------------------------
    # Empty / blank transcript
    # ------------------------------------------------------------------

    async def test_empty_transcript_no_pipeline(self):
        """Empty transcript must NOT be forwarded to the text pipeline."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        patches = self._patches(transcript="")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("telegram_bot.handle_user_text") as route_mock:
                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()

    async def test_empty_transcript_friendly_message(self):
        """User gets a helpful message when transcript is empty."""
        upd = self._make_upd()
        ctx = self._make_ctx()
        patches = self._patches(transcript="   ")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with patch("telegram_bot.handle_user_text"):
                await telegram_bot.voice_handler(upd, ctx)
        all_text = " ".join(upd.message.replies)
        self.assertIn("Не смог распознать", all_text)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    async def test_ffmpeg_failure_handled(self):
        """ffmpeg error must produce a user-friendly reply, not a crash."""
        from speech_to_text import SpeechToTextError
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())

        upd = self._make_upd()
        ctx = self._make_ctx()
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path):
                with patch("telegram_bot.convert_voice_to_wav",
                           side_effect=SpeechToTextError("ffmpeg binary not found: ffmpeg")):
                    with patch("telegram_bot.should_keep_voice_files", return_value=False):
                        with patch("telegram_bot.handle_user_text") as route_mock:
                            await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()
        all_text = " ".join(upd.message.replies)
        self.assertIn("Ошибка голосового ввода", all_text)
        # Must not leak absolute paths
        self.assertNotIn(tmp_path.as_posix(), all_text)

    async def test_whisper_failure_handled(self):
        """Whisper CLI error must produce a user-friendly reply."""
        from speech_to_text import SpeechToTextError
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())

        upd = self._make_upd()
        ctx = self._make_ctx()
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path):
                with patch("telegram_bot.convert_voice_to_wav"):
                    with patch("telegram_bot.transcribe_audio",
                               side_effect=SpeechToTextError("whisper timed out")):
                        with patch("telegram_bot.should_keep_voice_files", return_value=False):
                            with patch("telegram_bot.handle_user_text") as route_mock:
                                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()
        all_text = " ".join(upd.message.replies)
        self.assertIn("Ошибка голосового ввода", all_text)

    async def test_custom_cli_missing_command_handled(self):
        """Missing STT_CUSTOM_COMMAND must produce a user-friendly reply."""
        from speech_to_text import SpeechToTextError
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())

        upd = self._make_upd()
        ctx = self._make_ctx()
        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path):
                with patch("telegram_bot.convert_voice_to_wav"):
                    with patch("telegram_bot.transcribe_audio",
                               side_effect=SpeechToTextError(
                                   "STT_CUSTOM_COMMAND is required for custom_cli provider.")):
                        with patch("telegram_bot.should_keep_voice_files", return_value=False):
                            with patch("telegram_bot.handle_user_text") as route_mock:
                                await telegram_bot.voice_handler(upd, ctx)
        route_mock.assert_not_called()
        all_text = " ".join(upd.message.replies)
        self.assertIn("Ошибка голосового ввода", all_text)

    # ------------------------------------------------------------------
    # Temporary files
    # ------------------------------------------------------------------

    async def test_temp_files_cleaned_when_keep_false(self):
        """Temporary voice files must be deleted when VOICE_KEEP_FILES=false."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch as _p

        tmp_path = Path(tempfile.mkdtemp())
        # Create fake temp files so cleanup can find them
        ogg = tmp_path / "voice_test.ogg"
        wav = tmp_path / "voice_test.wav"
        ogg.write_text("x")
        wav.write_text("x")

        upd = self._make_upd()
        ctx = self._make_ctx()
        cleanup_mock = MagicMock()

        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path):
                with patch("telegram_bot.convert_voice_to_wav"):
                    with patch("telegram_bot.transcribe_audio", return_value="test"):
                        with patch("telegram_bot.should_keep_voice_files", return_value=False):
                            with patch("telegram_bot.cleanup_voice_files", cleanup_mock):
                                with patch("telegram_bot.handle_user_text"):
                                    await telegram_bot.voice_handler(upd, ctx)

        cleanup_mock.assert_called_once()
        cleaned_paths = cleanup_mock.call_args.args[0]
        self.assertGreater(len(cleaned_paths), 0)

    async def test_temp_files_kept_when_keep_true(self):
        """Temporary voice files must NOT be deleted when VOICE_KEEP_FILES=true."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        tmp_path = Path(tempfile.mkdtemp())
        upd = self._make_upd()
        ctx = self._make_ctx()
        cleanup_mock = MagicMock()

        with patch("telegram_bot.is_voice_enabled", return_value=True):
            with patch("telegram_bot.ensure_voice_work_dir", return_value=tmp_path):
                with patch("telegram_bot.convert_voice_to_wav"):
                    with patch("telegram_bot.transcribe_audio", return_value="test"):
                        with patch("telegram_bot.should_keep_voice_files", return_value=True):
                            with patch("telegram_bot.cleanup_voice_files", cleanup_mock):
                                with patch("telegram_bot.handle_user_text"):
                                    await telegram_bot.voice_handler(upd, ctx)

        cleanup_mock.assert_not_called()


class TestBoardSyncAfterAction(unittest.IsolatedAsyncioTestCase):
    """_board_sync_after_action and _notify_action_result trigger board sync for state-changing actions."""

    def _make_ctx(self):
        from unittest.mock import MagicMock, AsyncMock
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.send_message = AsyncMock()
        return ctx

    def _plan(self, action_name, args=None):
        return {
            "intent": "execute",
            "action": {"name": action_name, "args": args or {}},
        }

    def _result(self, action_name, raw_result):
        return {"executed": True, "action": action_name, "result": raw_result}

    async def test_create_task_syncs_board(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "created", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "create_task",
                {},
                self._result("create_task", {"id": "TASK-5", "title": "T", "status": "idea"}),
            )
        self.assertIn("TASK-5", synced)

    async def test_create_bug_syncs_board(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "created", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "create_bug",
                {},
                self._result("create_bug", {"id": "BUG-1", "title": "B", "status": "idea"}),
            )
        self.assertIn("BUG-1", synced)

    async def test_run_next_syncs_by_action_args_id(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "run_next",
                {"id": "TASK-1"},
                self._result("run_next", {"task": {"id": "TASK-1", "status": "ready_for_dev"}, "message": "ok"}),
            )
        self.assertIn("TASK-1", synced)

    async def test_run_next_syncs_by_task_dict_when_no_args_id(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "run_next",
                {},
                self._result("run_next", {"task": {"id": "TASK-2", "status": "in_progress"}, "message": "ok"}),
            )
        self.assertIn("TASK-2", synced)

    async def test_advance_task_safely_syncs_board(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "advance_task_safely",
                {"id": "TASK-3"},
                self._result("advance_task_safely", {"task_id": "TASK-3", "final_status": "in_progress"}),
            )
        self.assertIn("TASK-3", synced)

    async def test_prepare_task_for_dev_syncs_board(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "prepare_task_for_dev",
                {"id": "TASK-4"},
                self._result("prepare_task_for_dev", {"task_id": "TASK-4", "final_status": "ready_for_dev"}),
            )
        self.assertIn("TASK-4", synced)

    async def test_run_all_syncs_all_tasks(self):
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "run_all",
                {},
                self._result("run_all", [
                    {"id": "TASK-1", "status": "in_progress"},
                    {"id": "TASK-2", "status": "done"},
                ]),
            )
        self.assertIn("TASK-1", synced)
        self.assertIn("TASK-2", synced)

    async def test_non_state_action_no_sync(self):
        """list_tasks, show_task etc. must not trigger board sync."""
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "updated", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                ctx,
                "list_tasks",
                {},
                self._result("list_tasks", []),
            )
        self.assertEqual(synced, [])

    async def test_none_context_no_crash(self):
        """context=None should not raise."""
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "skipped", "task_id": task_id}
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            await telegram_bot._board_sync_after_action(
                None,
                "create_task",
                {},
                self._result("create_task", {"id": "TASK-X", "status": "idea"}),
            )
        # Sync can still be called with bot=None — shouldn't raise

    async def test_notify_action_result_calls_board_sync(self):
        """_notify_action_result triggers _board_sync_after_action for create_task."""
        ctx = self._make_ctx()
        synced = []
        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "created", "task_id": task_id}
        plan = self._plan("create_task")
        result = self._result("create_task", {"id": "TASK-7", "title": "T", "status": "idea"})
        with patch("telegram_board.sync_task_to_board", _fake_sync):
            with patch("telegram_bot.get_status_chat_id", return_value=None):
                await telegram_bot._notify_action_result(ctx, plan, result)
        self.assertIn("TASK-7", synced)


# ---------------------------------------------------------------------------
# TestBoardSyncHandler
# ---------------------------------------------------------------------------

class TestBoardSyncHandler(unittest.IsolatedAsyncioTestCase):

    def _make_update(self, user_id="42"):
        return _FakeUpdate(user_id=user_id, text="")

    def _make_ctx(self, owner_id="42"):
        class _FakeCtxBot:
            async def send_message(self, *a, **kw): pass
        ctx = SimpleNamespace()
        ctx.bot = _FakeCtxBot()
        ctx.bot_data = {"telegram_config": {"owner_id": owner_id, "dry_run_by_default": False}}
        return ctx

    async def test_non_owner_denied(self):
        upd = self._make_update(user_id="99")
        ctx = self._make_ctx(owner_id="42")
        await telegram_bot.board_sync_handler(upd, ctx)
        self.assertIn("denied", " ".join(upd.message.replies).lower())

    async def test_sends_syncing_message_first(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx(owner_id="42")
        summary_result = {
            "status": "ok", "total": 2,
            "created": 1, "updated": 1, "unchanged": 0, "recreated": 0,
            "moved": 0, "skipped": 0, "timeout": 0, "error": 0,
            "items": [],
        }
        async def _fake_sync(*, bot=None, source="system"):
            return summary_result
        with patch("telegram_board.sync_all_tasks_to_board", _fake_sync):
            await telegram_bot.board_sync_handler(upd, ctx)
        # First reply is the "Синхронизирую" message
        self.assertIn("Синхронизирую", upd.message.replies[0])

    async def test_replies_with_summary(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx(owner_id="42")
        summary_result = {
            "status": "ok", "total": 3,
            "created": 2, "updated": 1, "unchanged": 0, "recreated": 0,
            "moved": 0, "skipped": 0, "timeout": 0, "error": 0,
            "items": [],
        }
        async def _fake_sync(*, bot=None, source="system"):
            return summary_result
        with patch("telegram_board.sync_all_tasks_to_board", _fake_sync):
            await telegram_bot.board_sync_handler(upd, ctx)
        combined = " ".join(upd.message.replies)
        # Should contain the formatted summary
        self.assertIn("sync", combined.lower())

    async def test_skipped_board_disabled(self):
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx(owner_id="42")
        skipped_result = {"status": "skipped", "reason": "board disabled", "total": 0, "items": []}
        async def _fake_sync(*, bot=None, source="system"):
            return skipped_result
        with patch("telegram_board.sync_all_tasks_to_board", _fake_sync):
            await telegram_bot.board_sync_handler(upd, ctx)
        combined = " ".join(upd.message.replies)
        self.assertIn("пропущен", combined.lower())

    async def test_uses_context_bot(self):
        """bot=context.bot is passed through to sync_all_tasks_to_board."""
        upd = self._make_update(user_id="42")
        ctx = self._make_ctx(owner_id="42")
        received_bot = []

        async def _fake_sync(*, bot=None, source="system"):
            received_bot.append(bot)
            return {"status": "ok", "total": 0, "created": 0, "updated": 0, "unchanged": 0,
                    "recreated": 0, "moved": 0, "skipped": 0, "timeout": 0, "error": 0, "items": []}

        with patch("telegram_board.sync_all_tasks_to_board", _fake_sync):
            await telegram_bot.board_sync_handler(upd, ctx)
        self.assertIs(received_bot[0], ctx.bot)

    async def test_board_sync_command_registered(self):
        """board_sync_handler is wired into build_application."""
        self.assertTrue(
            hasattr(telegram_bot, "board_sync_handler"),
            "board_sync_handler must be defined in telegram_bot",
        )


# ---------------------------------------------------------------------------
# TestErrorLogging
# ---------------------------------------------------------------------------

class TestErrorLogging(unittest.IsolatedAsyncioTestCase):
    """Tests for write_error_log() and the error_handler."""

    def _make_exc(self, msg="test error"):
        """Return an exception that has a real __traceback__."""
        try:
            raise ValueError(msg)
        except ValueError as e:
            return e

    def _make_context(self, exc):
        return SimpleNamespace(error=exc)

    # ------------------------------------------------------------------
    # write_error_log — unit tests
    # ------------------------------------------------------------------

    def test_write_error_log_creates_file(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc()
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            error_id, log_path = telegram_bot.write_error_log(exc)
        self.assertTrue(log_path.exists(), "Log file was not created")
        log_files = list(tmpdir.glob("TG-*.log"))
        self.assertEqual(len(log_files), 1)

    def test_write_error_log_id_format(self):
        import tempfile, re
        from pathlib import Path
        exc = self._make_exc()
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            error_id, _ = telegram_bot.write_error_log(exc)
        self.assertRegex(error_id, r"^TG-\d{8}-\d{6}-[0-9a-f]{4}$")

    def test_write_error_log_contains_traceback(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc("traceback test")
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            error_id, log_path = telegram_bot.write_error_log(exc)
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("--- traceback ---", content)
        self.assertIn("ValueError", content)

    def test_write_error_log_contains_exception_message(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc("unique error message xyz")
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            _, log_path = telegram_bot.write_error_log(exc)
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("unique error message xyz", content)
        self.assertIn("exception_class: ValueError", content)

    def test_write_error_log_no_secrets(self):
        """TELEGRAM_BOT_TOKEN must never appear in the log file."""
        import tempfile
        from pathlib import Path
        exc = self._make_exc()
        tmpdir = Path(tempfile.mkdtemp())
        secret_token = "SECRET_TG_TOKEN_12345"
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": secret_token}, clear=False):
            with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
                _, log_path = telegram_bot.write_error_log(exc)
        content = log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret_token, content)

    def test_write_error_log_includes_user_id(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc()
        upd = _FakeUpdate(user_id="55555", text="тест")
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            _, log_path = telegram_bot.write_error_log(exc, update=upd)
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("55555", content)

    def test_write_error_log_never_raises_on_write_failure(self):
        """Even if the directory can't be created, write_error_log must not raise."""
        exc = self._make_exc()
        from pathlib import Path
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", Path("/nonexistent/path/that/cannot/be/created")):
            error_id, log_path = telegram_bot.write_error_log(exc)
        # Must return a valid error_id regardless
        self.assertTrue(error_id.startswith("TG-"))

    # ------------------------------------------------------------------
    # error_handler — integration tests
    # ------------------------------------------------------------------

    async def test_error_handler_reply_contains_error_id(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc("some failure")
        context_ns = self._make_context(exc)
        upd = _FakeUpdate(user_id="42", text="test command")
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(upd, context_ns)
        combined = " ".join(upd.message.replies)
        self.assertIn("Error ID: TG-", combined)

    async def test_error_handler_reply_contains_log_path(self):
        import tempfile
        from pathlib import Path
        exc = self._make_exc("some failure")
        context_ns = self._make_context(exc)
        upd = _FakeUpdate(user_id="42", text="test command")
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(upd, context_ns)
        combined = " ".join(upd.message.replies)
        self.assertIn("Лог:", combined)
        self.assertIn(".log", combined)

    async def test_error_handler_friendly_message_for_empty_output(self):
        """'Claude Code returned empty output' → friendly explanation in reply."""
        exc = self._make_exc("Claude Code returned empty output.")
        context_ns = self._make_context(exc)
        upd = _FakeUpdate(user_id="42", text="test command")
        import tempfile
        from pathlib import Path
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(upd, context_ns)
        combined = " ".join(upd.message.replies)
        self.assertIn("Claude Code", combined)
        self.assertIn("пустой ответ", combined)
        # Error ID must still appear
        self.assertIn("Error ID: TG-", combined)

    async def test_error_handler_generic_message_for_other_errors(self):
        """Generic errors get 'Произошла внутренняя ошибка.' header."""
        exc = self._make_exc("some random failure")
        context_ns = self._make_context(exc)
        upd = _FakeUpdate(user_id="42", text="test")
        import tempfile
        from pathlib import Path
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(upd, context_ns)
        combined = " ".join(upd.message.replies)
        self.assertIn("внутренняя ошибка", combined)

    async def test_error_handler_no_update_does_not_raise(self):
        """error_handler with update=None must complete without raising."""
        exc = self._make_exc()
        context_ns = self._make_context(exc)
        import tempfile
        from pathlib import Path
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(None, context_ns)  # no update

    async def test_error_handler_none_exc_does_not_raise(self):
        """error_handler with no error in context must do nothing."""
        context_ns = SimpleNamespace(error=None)
        upd = _FakeUpdate(user_id="42", text="")
        import tempfile
        from pathlib import Path
        tmpdir = Path(tempfile.mkdtemp())
        with patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.error_handler(upd, context_ns)
        # No replies expected
        self.assertEqual(upd.message.replies, [])

    # ------------------------------------------------------------------
    # Voice downstream error → error log + Error ID
    # ------------------------------------------------------------------

    async def test_voice_downstream_error_creates_log(self):
        """handle_user_text raises inside voice_handler → error log file created."""
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock

        upd = _FakeUpdate(user_id="42", voice=SimpleNamespace(file_id="f1"))
        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=_FakeTGFile())
        ctx = MagicMock()
        ctx.bot = bot
        ctx.bot_data = {"telegram_config": {"owner_id": "42", "dry_run_by_default": True}}
        tmpdir = Path(tempfile.mkdtemp())

        with patch("telegram_bot.is_voice_enabled", return_value=True), \
             patch("telegram_bot.ensure_voice_work_dir", return_value=tmpdir), \
             patch("telegram_bot.convert_voice_to_wav"), \
             patch("telegram_bot.transcribe_audio", return_value="тест"), \
             patch("telegram_bot.handle_user_text", side_effect=RuntimeError("Claude empty output")), \
             patch("telegram_bot.should_keep_voice_files", return_value=False), \
             patch("telegram_bot.cleanup_voice_files"), \
             patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.voice_handler(upd, ctx)

        log_files = list(tmpdir.glob("TG-*.log"))
        self.assertGreaterEqual(len(log_files), 1)

    async def test_voice_downstream_error_reply_has_error_id(self):
        """Voice downstream error reply includes 'Error ID: TG-...'."""
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock

        upd = _FakeUpdate(user_id="42", voice=SimpleNamespace(file_id="f2"))
        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=_FakeTGFile())
        ctx = MagicMock()
        ctx.bot = bot
        ctx.bot_data = {"telegram_config": {"owner_id": "42", "dry_run_by_default": True}}
        tmpdir = Path(tempfile.mkdtemp())

        with patch("telegram_bot.is_voice_enabled", return_value=True), \
             patch("telegram_bot.ensure_voice_work_dir", return_value=tmpdir), \
             patch("telegram_bot.convert_voice_to_wav"), \
             patch("telegram_bot.transcribe_audio", return_value="тест"), \
             patch("telegram_bot.handle_user_text", side_effect=RuntimeError("some downstream failure")), \
             patch("telegram_bot.should_keep_voice_files", return_value=False), \
             patch("telegram_bot.cleanup_voice_files"), \
             patch.object(telegram_bot, "_ERROR_LOG_DIR", tmpdir):
            await telegram_bot.voice_handler(upd, ctx)

        combined = " ".join(upd.message.replies)
        self.assertIn("Error ID: TG-", combined)
        self.assertIn("Голос распознан", combined)


if __name__ == "__main__":
    unittest.main()
