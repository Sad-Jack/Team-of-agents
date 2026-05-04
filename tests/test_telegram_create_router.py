"""Tests for telegram_create_router.py and its integration in telegram_bot.py."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import telegram_create_router
import telegram_bot


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []
        self.reply_markups: list = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class _FakeUpdate:
    def __init__(self, user_id="42", text=""):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=user_id)
        self.message = _FakeMessage(text=text)
        self.callback_query = None


class _FakeBot:
    async def send_message(self, *a, **kw):
        return SimpleNamespace(message_id=1)


def _make_ctx(owner_id="42"):
    ctx = SimpleNamespace()
    ctx.bot = _FakeBot()
    ctx.bot_data = {
        "telegram_config": {
            "owner_id": owner_id,
            "dry_run_by_default": False,
        }
    }
    return ctx


def _fake_task(task_id="TASK-99", title="Тестовая задача"):
    return {"id": task_id, "title": title, "status": "idea", "type": "feature"}


def _fake_bug(bug_id="BUG-99", title="Тестовый баг"):
    return {"id": bug_id, "title": title, "status": "idea", "type": "bug"}


# ---------------------------------------------------------------------------
# 1. normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle(unittest.TestCase):

    def test_strips_trailing_dot(self):
        self.assertEqual(telegram_create_router.normalize_title("проверить голос."), "Проверить голос")

    def test_capitalizes_first_letter(self):
        self.assertEqual(telegram_create_router.normalize_title("добавить кнопку"), "Добавить кнопку")

    def test_preserves_english_words(self):
        self.assertEqual(
            telegram_create_router.normalize_title("добавить Board sync кнопку"),
            "Добавить Board sync кнопку",
        )

    def test_preserves_task_ids(self):
        self.assertEqual(
            telegram_create_router.normalize_title("проверить TASK-1"),
            "Проверить TASK-1",
        )

    def test_strips_multiple_trailing_punct(self):
        self.assertEqual(telegram_create_router.normalize_title("текст!!!"), "Текст")

    def test_collapses_spaces(self):
        self.assertEqual(
            telegram_create_router.normalize_title("  много   пробелов  "),
            "Много пробелов",
        )

    def test_empty_string_stays_empty(self):
        self.assertEqual(telegram_create_router.normalize_title(""), "")

    def test_strips_trailing_comma(self):
        self.assertEqual(telegram_create_router.normalize_title("текст,"), "Текст")


# ---------------------------------------------------------------------------
# 2. detect_create_intent — task patterns
# ---------------------------------------------------------------------------

class TestDetectCreateIntentTask(unittest.TestCase):

    def _t(self, text):
        return telegram_create_router.detect_create_intent(text)

    def test_sozdat_zadachu(self):
        result = self._t("Создать задачу проверить голосовое управление проектом")
        self.assertIsNotNone(result)
        kind, title = result
        self.assertEqual(kind, "task")
        self.assertIn("Проверить", title)

    def test_sozdaj_zadachu(self):
        kind, title = self._t("Создай задачу добавить кнопку Board")
        self.assertEqual(kind, "task")
        self.assertIn("Добавить", title)

    def test_dobav_zadachu(self):
        kind, title = self._t("Добавь задачу настроить мониторинг")
        self.assertEqual(kind, "task")
        self.assertIn("Настроить", title)

    def test_dobavit_zadachu(self):
        kind, title = self._t("Добавить задачу сделать healthcheck")
        self.assertEqual(kind, "task")
        self.assertIn("Сделать", title)

    def test_postav_zadachu(self):
        kind, title = self._t("Поставь задачу добавить кнопку синхронизации Board")
        self.assertEqual(kind, "task")
        self.assertIn("Добавить", title)

    def test_novaya_zadacha(self):
        kind, title = self._t("Новая задача проверить интеграцию")
        self.assertEqual(kind, "task")
        self.assertIn("Проверить", title)

    def test_nado_sdelat(self):
        kind, title = self._t("Надо сделать рефакторинг модуля")
        self.assertEqual(kind, "task")
        self.assertIn("Рефакторинг", title)

    def test_nuzhno_sdelat(self):
        kind, title = self._t("Нужно сделать документацию API")
        self.assertEqual(kind, "task")
        self.assertIn("Документацию", title)

    def test_zadachu_na(self):
        """Accusative + 'на' — common in STT output."""
        kind, title = self._t("Задачу на проверку аудио сообщений в проектах.")
        self.assertEqual(kind, "task")
        self.assertNotEqual(title, "")
        # trailing dot removed
        self.assertFalse(title.endswith("."))

    def test_zadachu_bare(self):
        kind, title = self._t("Задачу проверить OAuth интеграцию")
        self.assertEqual(kind, "task")
        self.assertIn("Проверить", title)

    def test_zadacha_na(self):
        kind, title = self._t("задача на проверку Board sync")
        self.assertEqual(kind, "task")
        self.assertIn("Проверку", title)

    def test_zadacha_bare(self):
        kind, title = self._t("Задача написать юнит-тесты")
        self.assertEqual(kind, "task")
        self.assertIn("Написать", title)

    def test_case_insensitive(self):
        kind, title = self._t("СОЗДАТЬ ЗАДАЧУ тест в верхнем регистре")
        self.assertEqual(kind, "task")

    def test_empty_title_after_prefix(self):
        kind, title = self._t("Создать задачу")
        self.assertEqual(kind, "task")
        self.assertEqual(title, "")

    def test_empty_title_zadachu(self):
        result = self._t("задачу")
        # "задачу" alone (no space, no trailing content) correctly returns None
        # because the pattern requires at least whitespace+content after.
        # If a match IS returned for some reason, verify it is a task with empty title.
        if result is not None:
            kind, title = result
            self.assertEqual(kind, "task")
            self.assertEqual(title, "")

    def test_title_uppercase_preserved(self):
        kind, title = self._t("Создай задачу обновить TASK-5 статус")
        self.assertEqual(kind, "task")
        self.assertIn("TASK-5", title)

    def test_regression_chto_delat(self):
        """'что делать дальше?' must NOT match create-task."""
        result = self._t("что делать дальше?")
        self.assertIsNone(result)

    def test_regression_status(self):
        """'статус проекта' must NOT match."""
        result = self._t("статус проекта")
        self.assertIsNone(result)

    def test_regression_zadachi_plural(self):
        """'задачи' (backlog query) must NOT match create-task."""
        result = self._t("задачи")
        self.assertIsNone(result)

    def test_regression_pokozhi_zadachi(self):
        result = self._t("покажи задачи")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 3. detect_create_intent — bug patterns
# ---------------------------------------------------------------------------

class TestDetectCreateIntentBug(unittest.TestCase):

    def _t(self, text):
        return telegram_create_router.detect_create_intent(text)

    def test_sozdat_bag(self):
        kind, title = self._t("Создать баг карточка не обновляется")
        self.assertEqual(kind, "bug")
        self.assertIn("Карточка", title)

    def test_sozdaj_bag(self):
        kind, title = self._t("Создай баг форма падает при отправке")
        self.assertEqual(kind, "bug")

    def test_dobavit_bag(self):
        kind, title = self._t("Добавить баг неверный redirect")
        self.assertEqual(kind, "bug")

    def test_dobav_bag(self):
        kind, title = self._t("Добавь баг ошибка авторизации")
        self.assertEqual(kind, "bug")

    def test_novyj_bag(self):
        kind, title = self._t("Новый баг кнопка не работает")
        self.assertEqual(kind, "bug")

    def test_nashol_bag(self):
        kind, title = self._t("Нашёл баг карточка задачи не обновляется.")
        self.assertEqual(kind, "bug")
        self.assertEqual(title, "Карточка задачи не обновляется")

    def test_nashel_bag(self):
        kind, title = self._t("Нашел баг форма регистрации падает")
        self.assertEqual(kind, "bug")

    def test_est_bag(self):
        kind, title = self._t("есть баг при загрузке файла")
        self.assertEqual(kind, "bug")

    def test_bag_bare(self):
        kind, title = self._t("Баг авторизация не работает через OAuth")
        self.assertEqual(kind, "bug")
        self.assertIn("Авторизация", title)

    def test_trailing_dot_removed(self):
        kind, title = self._t("Нашёл баг карточка не обновляется.")
        self.assertEqual(title, "Карточка не обновляется")

    def test_empty_title_after_prefix(self):
        kind, title = self._t("Создать баг")
        self.assertEqual(kind, "bug")
        self.assertEqual(title, "")

    def test_case_insensitive(self):
        kind, title = self._t("НАШЁЛ БАГ тест")
        self.assertEqual(kind, "bug")

    def test_regression_bagi_plural(self):
        """'баги' must NOT match (it's a list-bugs query)."""
        result = self._t("баги")
        self.assertIsNone(result)

    def test_bug_before_task(self):
        """Bug patterns are checked before task patterns."""
        # "есть баг" should be bug, not task (no overlap, but ordering verified)
        kind, _ = self._t("есть баг авторизация")
        self.assertEqual(kind, "bug")


# ---------------------------------------------------------------------------
# 4. create_task_fast / create_bug_fast (unit tests with mocked orchestrator)
# ---------------------------------------------------------------------------

class TestCreateFastFunctions(unittest.TestCase):

    def _fake_task_dict(self, task_id, title):
        return {"id": task_id, "title": title, "status": "idea", "type": "feature",
                "description": "", "priority": "medium"}

    def _fake_bug_dict(self, bug_id, title):
        return {"id": bug_id, "title": title, "status": "idea", "type": "bug",
                "description": "", "priority": "medium", "severity": "unknown"}

    def test_create_task_fast_calls_orchestrator(self):
        with patch("orchestrator.create_task", return_value=self._fake_task_dict("TASK-1", "Тест")) as mock_ct:
            result = telegram_create_router.create_task_fast("Тест")
        mock_ct.assert_called_once_with("Тест", telegram_create_router._DEFAULT_DESCRIPTION)
        self.assertEqual(result["id"], "TASK-1")

    def test_create_task_fast_custom_description(self):
        with patch("orchestrator.create_task", return_value=self._fake_task_dict("TASK-2", "T")) as mock_ct:
            telegram_create_router.create_task_fast("T", "Моё описание")
        mock_ct.assert_called_once_with("T", "Моё описание")

    def test_create_bug_fast_does_not_call_run_agent(self):
        """create_bug_fast must NOT call agent_runner.run_agent."""
        fake_tasks = []

        def _fake_load():
            return list(fake_tasks)

        def _fake_normalize(d):
            return d

        def _fake_next_id(tasks, prefix):
            return f"{prefix}-1"

        def _fake_validate(t):
            pass

        def _fake_save(tasks):
            fake_tasks.extend(tasks)

        with patch("orchestrator.load_tasks", _fake_load), \
             patch("orchestrator._normalize_task_schema", _fake_normalize), \
             patch("orchestrator._next_id_with_prefix", _fake_next_id), \
             patch("orchestrator.validate_task", _fake_validate), \
             patch("orchestrator.save_tasks", _fake_save), \
             patch("agent_runner.run_agent") as mock_agent:
            result = telegram_create_router.create_bug_fast("Тестовый баг")

        mock_agent.assert_not_called()
        self.assertEqual(result["id"], "BUG-1")
        self.assertEqual(result["type"], "bug")
        self.assertEqual(result["title"], "Тестовый баг")

    def test_create_bug_fast_type_is_bug(self):
        """Resulting bug dict must have type='bug'."""
        fake_tasks = []
        with patch("orchestrator.load_tasks", return_value=[]), \
             patch("orchestrator._normalize_task_schema", lambda d: d), \
             patch("orchestrator._next_id_with_prefix", return_value="BUG-5"), \
             patch("orchestrator.validate_task"), \
             patch("orchestrator.save_tasks"):
            result = telegram_create_router.create_bug_fast("Баг в форме")
        self.assertEqual(result["type"], "bug")


# ---------------------------------------------------------------------------
# 5. Bot integration: _try_create_fast via handle_user_text
# ---------------------------------------------------------------------------

class TestBotCreateFastIntegration(unittest.IsolatedAsyncioTestCase):
    """Test that handle_user_text uses the create router for create intents."""

    def _make_update(self, text, user_id="42"):
        return _FakeUpdate(user_id=user_id, text=text)

    def _make_ctx(self, owner_id="42"):
        return _make_ctx(owner_id)

    # -----------------------------------------------------------------------
    # Create task
    # -----------------------------------------------------------------------

    async def test_create_task_phrase_creates_task(self):
        upd = self._make_update("Создай задачу проверить голосовое управление проектом")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-10", "Проверить голосовое управление проектом")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task) as mock_ct, \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "created"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_ct.assert_called_once()
        combined = " ".join(upd.message.replies)
        self.assertIn("TASK-10", combined)
        self.assertIn("Проверить", combined)

    async def test_sozdaj_zadachu_creates_task(self):
        upd = self._make_update("создай задачу добавить мониторинг")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-11", "Добавить мониторинг")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("TASK-11", " ".join(upd.message.replies))

    async def test_dobav_zadachu_creates_task(self):
        upd = self._make_update("добавь задачу тест oauth")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-12", "Тест oauth")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("TASK-12", " ".join(upd.message.replies))

    async def test_zadacha_na_creates_task(self):
        upd = self._make_update("Задача на проверку Board sync")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-13", "Проверку Board sync")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("TASK-13", " ".join(upd.message.replies))

    async def test_zadachu_na_creates_task(self):
        """Voice STT output: 'задачу на проверку аудио сообщений'"""
        upd = self._make_update("задачу на проверку аудио сообщений")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-14", "Проверку аудио сообщений")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("TASK-14", " ".join(upd.message.replies))

    async def test_postav_zadachu_creates_task(self):
        upd = self._make_update("Поставь задачу добавить кнопку синхронизации Board")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-15", "Добавить кнопку синхронизации Board")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        combined = " ".join(upd.message.replies)
        self.assertIn("TASK-15", combined)

    # -----------------------------------------------------------------------
    # Create bug
    # -----------------------------------------------------------------------

    async def test_sozdat_bag_creates_bug(self):
        upd = self._make_update("Создать баг не обновляется карточка")
        ctx = self._make_ctx()
        fake_bug = _fake_bug("BUG-5", "Не обновляется карточка")

        with patch("telegram_create_router.create_bug_fast", return_value=fake_bug) as mock_cb, \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "created"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_cb.assert_called_once()
        combined = " ".join(upd.message.replies)
        self.assertIn("BUG-5", combined)
        self.assertIn("🐞", combined)

    async def test_nashol_bag_creates_bug(self):
        upd = self._make_update("Нашёл баг карточка не обновляется")
        ctx = self._make_ctx()
        fake_bug = _fake_bug("BUG-6", "Карточка не обновляется")

        with patch("telegram_create_router.create_bug_fast", return_value=fake_bug), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("BUG-6", " ".join(upd.message.replies))

    async def test_est_bag_creates_bug(self):
        upd = self._make_update("есть баг при загрузке файла")
        ctx = self._make_ctx()
        fake_bug = _fake_bug("BUG-7", "При загрузке файла")

        with patch("telegram_create_router.create_bug_fast", return_value=fake_bug), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("BUG-7", " ".join(upd.message.replies))

    # -----------------------------------------------------------------------
    # Supervisor NOT called for create-router matches
    # -----------------------------------------------------------------------

    async def test_create_task_does_not_call_supervisor(self):
        upd = self._make_update("Создай задачу тест без Claude")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-20", "Тест без Claude")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None), \
             patch("telegram_bot.plan_supervisor_action") as mock_supervisor, \
             patch("telegram_bot.execute_supervisor_action") as mock_execute:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_supervisor.assert_not_called()
        mock_execute.assert_not_called()

    async def test_create_bug_does_not_call_supervisor(self):
        upd = self._make_update("Нашёл баг тест без LLM")
        ctx = self._make_ctx()
        fake_bug = _fake_bug("BUG-20", "Тест без LLM")

        with patch("telegram_create_router.create_bug_fast", return_value=fake_bug), \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None), \
             patch("telegram_bot.plan_supervisor_action") as mock_supervisor, \
             patch("telegram_bot.execute_supervisor_action") as mock_execute:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_supervisor.assert_not_called()
        mock_execute.assert_not_called()

    # -----------------------------------------------------------------------
    # Board sync is called
    # -----------------------------------------------------------------------

    async def test_board_sync_called_after_task_create(self):
        upd = self._make_update("Создай задачу тест Board sync")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-30", "Тест Board sync")
        synced = []

        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "created"}

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", side_effect=_fake_sync), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("TASK-30", synced)

    async def test_board_sync_called_after_bug_create(self):
        upd = self._make_update("Нашёл баг тест Board sync")
        ctx = self._make_ctx()
        fake_bug = _fake_bug("BUG-30", "Тест Board sync")
        synced = []

        async def _fake_sync(task_id, *, bot=None, source="system"):
            synced.append(task_id)
            return {"status": "created"}

        with patch("telegram_create_router.create_bug_fast", return_value=fake_bug), \
             patch("telegram_board.sync_task_to_board", side_effect=_fake_sync), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertIn("BUG-30", synced)

    async def test_board_sync_failure_does_not_prevent_reply(self):
        """Board sync failure must be silent — user still gets a reply."""
        upd = self._make_update("Создай задачу тест")
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-31", "Тест")

        async def _boom(*_, **__):
            raise RuntimeError("Board down")

        with patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", side_effect=_boom), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        self.assertTrue(any("TASK-31" in r for r in upd.message.replies))

    # -----------------------------------------------------------------------
    # Empty title → friendly error
    # -----------------------------------------------------------------------

    async def test_empty_title_task_friendly_error(self):
        upd = self._make_update("Создать задачу")
        ctx = self._make_ctx()

        with patch("telegram_create_router.create_task_fast") as mock_ct, \
             patch("telegram_bot.plan_supervisor_action") as mock_sup:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        combined = " ".join(upd.message.replies)
        self.assertIn("Не понял", combined)
        mock_ct.assert_not_called()
        mock_sup.assert_not_called()

    async def test_empty_title_bug_friendly_error(self):
        upd = self._make_update("Создать баг")
        ctx = self._make_ctx()

        with patch("telegram_create_router.create_bug_fast") as mock_cb, \
             patch("telegram_bot.plan_supervisor_action") as mock_sup:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        combined = " ".join(upd.message.replies)
        self.assertIn("Не понял", combined)
        mock_cb.assert_not_called()
        mock_sup.assert_not_called()

    # -----------------------------------------------------------------------
    # Regression: other phrases still go to supervisor / fast-router
    # -----------------------------------------------------------------------

    async def test_chto_delat_dalsche_uses_fast_router(self):
        """'что делать дальше?' must NOT go to create router."""
        upd = self._make_update("что делать дальше?")
        ctx = self._make_ctx()

        with patch("telegram_create_router.create_task_fast") as mock_ct, \
             patch("telegram_create_router.create_bug_fast") as mock_cb, \
             patch("telegram_fast_router.try_route", return_value="Нет готовых задач.") as mock_fr:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_ct.assert_not_called()
        mock_cb.assert_not_called()

    async def test_status_goes_to_fast_router_not_create(self):
        """'статус проекта' must go to fast router, not create router."""
        upd = self._make_update("статус проекта")
        ctx = self._make_ctx()

        with patch("telegram_create_router.create_task_fast") as mock_ct, \
             patch("telegram_fast_router.try_route", return_value="Статус OK") as mock_fr:
            await telegram_bot.handle_user_text(upd, ctx, upd.message.text)

        mock_ct.assert_not_called()
        mock_fr.assert_called()


# ---------------------------------------------------------------------------
# 6. Voice UX: transcript-success but downstream error
# ---------------------------------------------------------------------------

class TestVoiceErrorUX(unittest.IsolatedAsyncioTestCase):

    def _make_update(self, user_id="42"):
        upd = _FakeUpdate(user_id=user_id)
        import uuid
        upd.message.voice = SimpleNamespace(file_id="fake_file_id")
        return upd

    def _make_ctx(self, owner_id="42"):
        class _FakeTGFile:
            async def download_to_drive(self, custom_path):
                from pathlib import Path
                Path(custom_path).write_text("fake", encoding="utf-8")

        class _FakeBot:
            async def get_file(self, _fid):
                return _FakeTGFile()

        ctx = SimpleNamespace()
        ctx.bot = _FakeBot()
        ctx.bot_data = {
            "telegram_config": {"owner_id": owner_id, "dry_run_by_default": False}
        }
        return ctx

    async def test_stt_error_says_oshibka_golosovogo_vvoda(self):
        """SpeechToTextError → 'Ошибка голосового ввода'."""
        from speech_to_text import SpeechToTextError

        upd = self._make_update()
        ctx = self._make_ctx()

        with patch("telegram_bot.is_voice_enabled", return_value=True), \
             patch("telegram_bot.ensure_voice_work_dir", return_value=__import__("pathlib").Path(".tmp/voice")), \
             patch("telegram_bot.convert_voice_to_wav", side_effect=SpeechToTextError("ffmpeg not found")), \
             patch("telegram_bot.cleanup_voice_files"), \
             patch("telegram_bot.should_keep_voice_files", return_value=False):
            await telegram_bot.voice_handler(upd, ctx)

        combined = " ".join(upd.message.replies)
        self.assertIn("Ошибка голосового ввода", combined)
        self.assertNotIn("Голос распознан", combined)

    async def test_stt_success_downstream_error_says_golos_priznan(self):
        """STT succeeds but handle_user_text raises → 'Голос распознан, но...'"""
        upd = self._make_update()
        ctx = self._make_ctx()

        with patch("telegram_bot.is_voice_enabled", return_value=True), \
             patch("telegram_bot.ensure_voice_work_dir", return_value=__import__("pathlib").Path(".tmp/voice")), \
             patch("telegram_bot.convert_voice_to_wav"), \
             patch("telegram_bot.transcribe_audio", return_value="тестовый текст"), \
             patch("telegram_bot.handle_user_text", side_effect=RuntimeError("Claude returned empty")), \
             patch("telegram_bot.cleanup_voice_files"), \
             patch("telegram_bot.should_keep_voice_files", return_value=False):
            await telegram_bot.voice_handler(upd, ctx)

        combined = " ".join(upd.message.replies)
        self.assertIn("Голос распознан", combined)
        self.assertNotIn("Ошибка обработки голосового сообщения", combined)

    async def test_voice_transcript_hits_create_router(self):
        """Voice transcript 'задачу на проверку аудио' → create-router path, no supervisor."""
        upd = self._make_update()
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-50", "Проверку аудио")

        with patch("telegram_bot.is_voice_enabled", return_value=True), \
             patch("telegram_bot.ensure_voice_work_dir", return_value=__import__("pathlib").Path(".tmp/voice")), \
             patch("telegram_bot.convert_voice_to_wav"), \
             patch("telegram_bot.transcribe_audio", return_value="задачу на проверку аудио"), \
             patch("telegram_create_router.create_task_fast", return_value=fake_task) as mock_ct, \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None), \
             patch("telegram_bot.plan_supervisor_action") as mock_sup, \
             patch("telegram_bot.cleanup_voice_files"), \
             patch("telegram_bot.should_keep_voice_files", return_value=False):
            await telegram_bot.voice_handler(upd, ctx)

        mock_ct.assert_called_once()
        mock_sup.assert_not_called()
        self.assertTrue(any("TASK-50" in r for r in upd.message.replies))


# ---------------------------------------------------------------------------
# 6. detect_imperative_create_intent — unit tests
# ---------------------------------------------------------------------------

class TestDetectImperativeCreateIntent(unittest.TestCase):
    """Unit tests for detect_imperative_create_intent()."""

    def _detect(self, text):
        return telegram_create_router.detect_imperative_create_intent(text)

    def test_dobavit_matches(self):
        result = self._detect("Добавить CLI-команду task-summary, которая показывает задачи.")
        self.assertIsNotNone(result)
        kind, title = result
        self.assertEqual(kind, "task")
        self.assertIn("CLI-команду", title)

    def test_sdelat_matches(self):
        kind, title = self._detect("Сделать страницу авторизации")
        self.assertEqual(kind, "task")
        self.assertIn("Сделать", title)

    def test_realizovat_matches(self):
        kind, title = self._detect("Реализовать OAuth через GitHub")
        self.assertEqual(kind, "task")
        self.assertIn("OAuth", title)

    def test_dorabotat_matches(self):
        kind, title = self._detect("Доработать интерфейс поиска")
        self.assertEqual(kind, "task")
        self.assertIn("поиска", title)

    def test_ispravit_matches(self):
        kind, title = self._detect("Исправить баг в форме логина")
        self.assertEqual(kind, "task")
        self.assertIn("форме", title)

    def test_proverit_matches(self):
        kind, title = self._detect("Проверить CI/CD конфигурацию")
        self.assertEqual(kind, "task")
        self.assertIn("CI/CD", title)

    def test_title_is_full_normalized_text(self):
        """Entire input message (verb included) becomes the title."""
        kind, title = self._detect("Реализовать OAuth через GitHub")
        self.assertEqual(title, "Реализовать OAuth через GitHub")

    def test_trailing_punct_stripped(self):
        kind, title = self._detect("Добавить CLI-команду task-summary.")
        self.assertFalse(title.endswith("."))
        self.assertIn("task-summary", title)

    def test_dobavit_zadachu_not_matched(self):
        """'добавить задачу X' must NOT match imperative pattern (negative lookahead)."""
        result = self._detect("добавить задачу проверить систему")
        self.assertIsNone(result)

    def test_sozdat_zadachu_not_matched(self):
        """Explicit create patterns must not match imperative detector."""
        self.assertIsNone(self._detect("создать задачу проверить"))
        self.assertIsNone(self._detect("создай задачу новая фича"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(self._detect(""))

    def test_status_proekta_returns_none(self):
        self.assertIsNone(self._detect("Статус проекта"))

    def test_chto_delat_returns_none(self):
        self.assertIsNone(self._detect("Что делать дальше?"))

    def test_lowercase_matches(self):
        """Patterns are case-insensitive."""
        result = self._detect("добавить мониторинг системы")
        self.assertIsNotNone(result)

    def test_kind_is_always_task(self):
        """Imperative patterns always yield 'task' kind."""
        for text in [
            "Добавить что-то",
            "Сделать форму",
            "Реализовать API",
            "Доработать UI",
            "Исправить ошибку",
            "Проверить окружение",
        ]:
            result = self._detect(text)
            self.assertIsNotNone(result, f"Expected match for: {text!r}")
            self.assertEqual(result[0], "task")

    def test_bare_verb_no_content_returns_none(self):
        """Verb alone without content should not match (requires at least one space + char)."""
        self.assertIsNone(self._detect("Добавить"))
        self.assertIsNone(self._detect("Сделать"))


# ---------------------------------------------------------------------------
# 7. Imperative router + focus integration tests
# ---------------------------------------------------------------------------

class TestImperativeFocusIntegration(unittest.IsolatedAsyncioTestCase):
    """Tests that imperative patterns respect active focus."""

    def _make_update(self, text, user_id="42"):
        return _FakeUpdate(user_id=user_id, text=text)

    def _make_ctx(self):
        return _make_ctx()

    _NO_FOCUS = {
        "active_task_id": None, "active_release_id": None, "active_decision_id": None,
        "summary": "Фокус не установлен.",
    }
    _HAS_FOCUS = {
        "active_task_id": "TASK-1", "active_release_id": None, "active_decision_id": None,
        "summary": "Фокус: TASK-1",
    }

    async def test_imperative_no_focus_creates_task(self):
        """'Добавить ...' without active focus creates task locally — no LLM call."""
        text = "Добавить CLI-команду task-summary, которая показывает количество задач по статусам и типам."
        upd = self._make_update(text)
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-77", normalize_title := telegram_create_router.normalize_title(text))

        with patch("telegram_bot.get_focus", return_value=self._NO_FOCUS), \
             patch("telegram_create_router.create_task_fast", return_value=fake_task) as mock_ct, \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None), \
             patch("telegram_bot.plan_supervisor_action") as mock_sup:
            await telegram_bot.handle_user_text(upd, ctx, text)

        mock_ct.assert_called_once()
        mock_sup.assert_not_called()
        combined = " ".join(upd.message.replies)
        self.assertIn("TASK-77", combined)

    async def test_imperative_no_focus_board_sync_called(self):
        """Board sync runs after imperative task creation."""
        text = "Реализовать OAuth через GitHub"
        upd = self._make_update(text)
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-78", "Реализовать OAuth через GitHub")
        synced = []

        async def _fake_sync(tid, *, bot=None, source="system"):
            synced.append(tid)
            return {"status": "created"}

        with patch("telegram_bot.get_focus", return_value=self._NO_FOCUS), \
             patch("telegram_create_router.create_task_fast", return_value=fake_task), \
             patch("telegram_board.sync_task_to_board", side_effect=_fake_sync), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None):
            await telegram_bot.handle_user_text(upd, ctx, text)

        self.assertIn("TASK-78", synced)

    async def test_imperative_with_focus_goes_to_supervisor(self):
        """'Добавить ...' with active focus must NOT be intercepted by create router."""
        text = "Добавить тест для TASK-1"
        upd = self._make_update(text)
        ctx = self._make_ctx()

        with patch("telegram_bot.get_focus", return_value=self._HAS_FOCUS), \
             patch("telegram_create_router.create_task_fast") as mock_ct, \
             patch("telegram_bot.plan_supervisor_action", return_value={
                 "intent": "unknown", "confidence": 0.5,
                 "requires_confirmation": False, "action": None,
                 "explanation": "уточни", "warnings": [],
             }) as mock_sup, \
             patch("telegram_bot.execute_supervisor_action") as mock_exec:
            await telegram_bot.handle_user_text(upd, ctx, text)

        mock_ct.assert_not_called()
        mock_sup.assert_called_once()

    async def test_explicit_sozdaj_zadachu_creates_even_with_focus(self):
        """Explicit 'создай задачу ...' always creates a task even when focus is active."""
        text = "Создай задачу добавить тесты для нового модуля"
        upd = self._make_update(text)
        ctx = self._make_ctx()
        fake_task = _fake_task("TASK-79", "Добавить тесты для нового модуля")

        with patch("telegram_bot.get_focus", return_value=self._HAS_FOCUS), \
             patch("telegram_create_router.create_task_fast", return_value=fake_task) as mock_ct, \
             patch("telegram_board.sync_task_to_board", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("telegram_bot._send_task_card", new=AsyncMock()), \
             patch("telegram_bot.get_status_chat_id", return_value=None), \
             patch("telegram_bot.plan_supervisor_action") as mock_sup:
            await telegram_bot.handle_user_text(upd, ctx, text)

        mock_ct.assert_called_once()
        mock_sup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
