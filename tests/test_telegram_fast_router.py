"""Tests for telegram_fast_router.py"""
import unittest
from unittest.mock import patch

import telegram_fast_router


# ---------------------------------------------------------------------------
# Intent matching — recognised read-only intents
# ---------------------------------------------------------------------------

class TestMatchIntentRecognised(unittest.TestCase):

    # project_status
    def test_status_exact(self):
        self.assertEqual(telegram_fast_router._match_intent("статус"), "project_status")

    def test_status_with_punctuation(self):
        self.assertEqual(telegram_fast_router._match_intent("статус?"), "project_status")

    def test_status_project(self):
        self.assertEqual(telegram_fast_router._match_intent("статус проекта"), "project_status")

    def test_pokaji_status_proekta(self):
        self.assertEqual(telegram_fast_router._match_intent("покажи статус проекта"), "project_status")

    def test_chto_po_proektu(self):
        self.assertEqual(telegram_fast_router._match_intent("что по проекту"), "project_status")

    def test_dai_status(self):
        self.assertEqual(telegram_fast_router._match_intent("дай статус"), "project_status")

    def test_pokaji_status(self):
        self.assertEqual(telegram_fast_router._match_intent("покажи статус"), "project_status")

    def test_obschaya_kartina(self):
        self.assertEqual(telegram_fast_router._match_intent("общая картина"), "project_status")

    def test_chto_proishodit(self):
        self.assertEqual(telegram_fast_router._match_intent("что сейчас происходит"), "project_status")

    # backlog
    def test_backlog_russian(self):
        self.assertEqual(telegram_fast_router._match_intent("бэклог"), "backlog")

    def test_backlog_english(self):
        self.assertEqual(telegram_fast_router._match_intent("покажи backlog"), "backlog")

    def test_pokaji_zadachi(self):
        self.assertEqual(telegram_fast_router._match_intent("покажи задачи"), "backlog")

    def test_chto_v_bekloge(self):
        self.assertEqual(telegram_fast_router._match_intent("что в бэклоге"), "backlog")

    def test_spisok_zadach(self):
        self.assertEqual(telegram_fast_router._match_intent("список задач"), "backlog")

    def test_zadachi(self):
        self.assertEqual(telegram_fast_router._match_intent("задачи"), "backlog")

    # next_action
    def test_chto_dalshe(self):
        self.assertEqual(telegram_fast_router._match_intent("что дальше"), "next_action")

    def test_chto_delat_dalshe(self):
        self.assertEqual(telegram_fast_router._match_intent("что делать дальше"), "next_action")

    def test_chto_dalshe_delat(self):
        self.assertEqual(telegram_fast_router._match_intent("что дальше делать"), "next_action")

    def test_sledujuschiy_shag(self):
        self.assertEqual(telegram_fast_router._match_intent("какой следующий шаг"), "next_action")

    def test_sledujuschaya_zadacha(self):
        self.assertEqual(telegram_fast_router._match_intent("следующая задача"), "next_action")

    def test_s_chego_nachat(self):
        self.assertEqual(telegram_fast_router._match_intent("с чего начать"), "next_action")

    # bugs
    def test_bagi(self):
        self.assertEqual(telegram_fast_router._match_intent("баги"), "bugs")

    def test_pokaji_bagi(self):
        self.assertEqual(telegram_fast_router._match_intent("покажи баги"), "bugs")

    def test_chto_s_bagami(self):
        self.assertEqual(telegram_fast_router._match_intent("что с багами"), "bugs")

    def test_spisok_bagov(self):
        self.assertEqual(telegram_fast_router._match_intent("список багов"), "bugs")

    # help
    def test_pomosh(self):
        self.assertEqual(telegram_fast_router._match_intent("помощь"), "help")

    def test_chto_ty_umeesh(self):
        self.assertEqual(telegram_fast_router._match_intent("что ты умеешь"), "help")

    def test_kak_polzovatsya(self):
        self.assertEqual(telegram_fast_router._match_intent("как тобой пользоваться"), "help")

    def test_spravka(self):
        self.assertEqual(telegram_fast_router._match_intent("справка"), "help")


# ---------------------------------------------------------------------------
# Intent matching — state-changing messages MUST return None
# ---------------------------------------------------------------------------

class TestMatchIntentBlocked(unittest.TestCase):

    def test_sozday_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("создай задачу: проверить логи"))

    def test_sozday_bag(self):
        self.assertIsNone(telegram_fast_router._match_intent("создай баг: падает при старте"))

    def test_zachfiksiruiy_bag(self):
        self.assertIsNone(telegram_fast_router._match_intent("зафиксируй баг: упало в проде"))

    def test_pochini_bag(self):
        self.assertIsNone(telegram_fast_router._match_intent("почини баг в авторизации"))

    def test_dobav_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("добавь задачу: написать тесты"))

    def test_izmeni_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("измени приоритет TASK-1"))

    def test_udali_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("удали TASK-3"))

    def test_zapusti(self):
        self.assertIsNone(telegram_fast_router._match_intent("запусти все задачи"))

    def test_primeni_patch(self):
        self.assertIsNone(telegram_fast_router._match_intent("примени патч к TASK-1"))

    def test_beri_v_rabotu(self):
        self.assertIsNone(telegram_fast_router._match_intent("бери в работу TASK-5"))

    def test_vozmi_v_rabotu(self):
        self.assertIsNone(telegram_fast_router._match_intent("возьми в работу"))

    def test_podgotov_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("подготовь TASK-1 к разработке"))

    def test_otmeni_zadachu(self):
        self.assertIsNone(telegram_fast_router._match_intent("отмени TASK-2"))

    def test_empty_unrecognised(self):
        self.assertIsNone(telegram_fast_router._match_intent(""))

    def test_arbitrary_phrase(self):
        self.assertIsNone(telegram_fast_router._match_intent("расскажи мне про архитектуру"))

    def test_sozdat_infinitive(self):
        self.assertIsNone(telegram_fast_router._match_intent("хочу создать задачу"))


# ---------------------------------------------------------------------------
# Action-verb guard
# ---------------------------------------------------------------------------

class TestActionVerbGuard(unittest.TestCase):

    def test_create_imperative(self):
        self.assertTrue(telegram_fast_router._has_action_verb("создай задачу"))

    def test_create_infinitive(self):
        self.assertTrue(telegram_fast_router._has_action_verb("хочу создать задачу"))

    def test_fix_bug(self):
        self.assertTrue(telegram_fast_router._has_action_verb("почини баг"))

    def test_status_no_verb(self):
        self.assertFalse(telegram_fast_router._has_action_verb("статус проекта"))

    def test_backlog_no_verb(self):
        self.assertFalse(telegram_fast_router._has_action_verb("покажи задачи"))

    def test_next_no_verb(self):
        self.assertFalse(telegram_fast_router._has_action_verb("что дальше делать"))

    def test_bugs_no_verb(self):
        self.assertFalse(telegram_fast_router._has_action_verb("баги"))


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestNormalise(unittest.TestCase):

    def test_strips_question_mark(self):
        self.assertEqual(telegram_fast_router._normalise("статус?"), "статус")

    def test_lowercase(self):
        self.assertEqual(telegram_fast_router._normalise("Статус Проекта"), "статус проекта")

    def test_extra_spaces(self):
        self.assertEqual(telegram_fast_router._normalise("  что  дальше  "), "что дальше")

    def test_punctuation_stripped(self):
        self.assertEqual(telegram_fast_router._normalise("Баги!"), "баги")


# ---------------------------------------------------------------------------
# try_route — disabled via env flag
# ---------------------------------------------------------------------------

class TestTryRouteDisabled(unittest.TestCase):

    def test_returns_none_when_disabled(self):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "false"}, clear=False):
            result = telegram_fast_router.try_route("статус")
        self.assertIsNone(result)

    def test_returns_none_for_action_verb_even_when_enabled(self):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            result = telegram_fast_router.try_route("создай задачу: тест")
        self.assertIsNone(result)

    def test_returns_none_for_create_bug(self):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            result = telegram_fast_router.try_route("создай баг: падает при старте")
        self.assertIsNone(result)

    def test_returns_none_for_pochini_bag(self):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            result = telegram_fast_router.try_route("почини баг в авторизации")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# try_route — enabled, matched
# ---------------------------------------------------------------------------

class TestTryRouteMatched(unittest.TestCase):

    def _route(self, text):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            return telegram_fast_router.try_route(text)

    def test_project_status_no_technical_fields(self):
        fake_status = {
            "total_tasks": 3,
            "by_status": {"in_progress": 1, "done": 2},
            "by_type": {},
            "ready_tasks_count": 1,
            "blocked_tasks_count": 0,
            "releases_count": 0,
            "ready_releases_count": 0,
            "next_recommendation": {"next_task": None, "blocked_count": 0},
            "top_blockers": [],
        }
        with patch("telegram_fast_router.get_project_status", return_value=fake_status):
            result = self._route("покажи статус проекта")
        self.assertIsNotNone(result)
        self.assertIn("📌", result)
        self.assertNotIn("intent", result)
        self.assertNotIn("confidence", result)
        self.assertNotIn("action", result)

    def test_chto_dalshe_delat_matched(self):
        rec = {"next_task": {"id": "TASK-1", "title": "X", "status": "ready_for_dev", "priority": "high"}, "blocked_count": 0}
        with patch("telegram_fast_router.get_next_work_recommendation", return_value=rec):
            result = self._route("что дальше делать")
        self.assertIsNotNone(result)
        self.assertIn("▶️", result)

    def test_pokaji_backlog_english_matched(self):
        tasks = [{"id": "TASK-1", "title": "Test", "status": "idea", "type": "feature"}]
        with patch("telegram_fast_router._orchestrator.list_tasks", return_value=tasks):
            result = self._route("покажи backlog")
        self.assertIsNotNone(result)
        self.assertIn("📋", result)

    def test_backlog_returns_tasks(self):
        tasks = [
            {"id": "TASK-1", "title": "Alpha", "status": "in_progress", "type": "feature"},
            {"id": "TASK-2", "title": "Beta", "status": "idea", "type": "bug"},
        ]
        with patch("telegram_fast_router._orchestrator.list_tasks", return_value=tasks):
            result = self._route("покажи задачи")
        self.assertIn("📋", result)
        self.assertIn("TASK-1", result)
        self.assertIn("TASK-2", result)
        self.assertIn("🐞", result)

    def test_backlog_empty(self):
        with patch("telegram_fast_router._orchestrator.list_tasks", return_value=[]):
            result = self._route("задачи")
        self.assertIn("Задач нет", result)

    def test_next_action_has_task(self):
        task = {"id": "TASK-3", "title": "Do X", "status": "ready_for_dev", "priority": "high"}
        rec = {"next_task": task, "blocked_count": 0}
        with patch("telegram_fast_router.get_next_work_recommendation", return_value=rec):
            result = self._route("что дальше")
        self.assertIn("▶️", result)
        self.assertIn("TASK-3", result)
        self.assertIn("Do X", result)

    def test_next_action_no_tasks(self):
        rec = {"next_task": None, "blocked_count": 2}
        with patch("telegram_fast_router.get_next_work_recommendation", return_value=rec):
            result = self._route("следующий шаг")
        self.assertIn("блокер", result.lower())

    def test_bugs_list(self):
        tasks = [
            {"id": "BUG-1", "title": "Crash", "status": "idea", "type": "bug", "severity": "critical"},
            {"id": "TASK-1", "title": "Feature", "status": "in_progress", "type": "feature"},
        ]
        with patch("telegram_fast_router._orchestrator.list_tasks", return_value=tasks):
            result = self._route("баги")
        self.assertIn("🐞", result)
        self.assertIn("BUG-1", result)
        self.assertNotIn("TASK-1", result)

    def test_bugs_none(self):
        with patch("telegram_fast_router._orchestrator.list_tasks", return_value=[]):
            result = self._route("покажи баги")
        self.assertIn("нет", result.lower())

    def test_help_returns_text(self):
        result = self._route("помощь")
        self.assertIn("🤖", result)
        self.assertIn("Создай задачу", result)
        self.assertIn("/help", result)

    def test_create_task_returns_none(self):
        result = self._route("создай задачу с нуля")
        self.assertIsNone(result)

    def test_create_bug_returns_none(self):
        result = self._route("создай баг: краш при авторизации")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# try_route — error handling
# ---------------------------------------------------------------------------

class TestTryRouteErrors(unittest.TestCase):

    def test_exception_in_handler_returns_warning(self):
        with patch.dict("os.environ", {"TELEGRAM_FAST_ROUTER_ENABLED": "true"}, clear=False):
            with patch("telegram_fast_router.get_project_status", side_effect=RuntimeError("boom")):
                result = telegram_fast_router.try_route("статус")
        self.assertIsNotNone(result)
        self.assertIn("⚠️", result)


if __name__ == "__main__":
    unittest.main()
