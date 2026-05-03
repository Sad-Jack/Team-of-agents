import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telegram_message_links


class TestTelegramMessageLinks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        links_path = Path(self._tmp.name) / "sessions" / "telegram_message_links.json"
        self._patcher = patch.object(telegram_message_links, "_LINKS_PATH", links_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_load_empty_when_no_file(self):
        links = telegram_message_links.load_all_links()
        self.assertEqual(links, [])

    def test_add_and_find_link(self):
        telegram_message_links.add_message_link("100", 42, "task", "TASK-1")
        result = telegram_message_links.find_link("100", 42)
        self.assertIsNotNone(result)
        self.assertEqual(result["work_item_id"], "TASK-1")
        self.assertEqual(result["work_item_type"], "task")
        self.assertEqual(result["telegram_chat_id"], "100")
        self.assertEqual(result["telegram_message_id"], 42)

    def test_find_unknown_link_returns_none(self):
        result = telegram_message_links.find_link("100", 999)
        self.assertIsNone(result)

    def test_add_multiple_links_independent(self):
        telegram_message_links.add_message_link("100", 1, "task", "TASK-1")
        telegram_message_links.add_message_link("100", 2, "bug", "TASK-2")
        r1 = telegram_message_links.find_link("100", 1)
        r2 = telegram_message_links.find_link("100", 2)
        self.assertEqual(r1["work_item_id"], "TASK-1")
        self.assertEqual(r2["work_item_id"], "TASK-2")
        self.assertEqual(r2["work_item_type"], "bug")

    def test_different_chats_do_not_collide(self):
        telegram_message_links.add_message_link("100", 1, "task", "TASK-1")
        telegram_message_links.add_message_link("200", 1, "task", "TASK-2")
        r1 = telegram_message_links.find_link("100", 1)
        r2 = telegram_message_links.find_link("200", 1)
        self.assertEqual(r1["work_item_id"], "TASK-1")
        self.assertEqual(r2["work_item_id"], "TASK-2")

    def test_corrupt_file_returns_empty(self):
        links_path = telegram_message_links._LINKS_PATH
        links_path.parent.mkdir(parents=True, exist_ok=True)
        links_path.write_text("{ not valid json ~~~", encoding="utf-8")
        links = telegram_message_links.load_all_links()
        self.assertEqual(links, [])

    def test_non_list_file_returns_empty(self):
        links_path = telegram_message_links._LINKS_PATH
        links_path.parent.mkdir(parents=True, exist_ok=True)
        links_path.write_text(json.dumps({"oops": "not a list"}), encoding="utf-8")
        links = telegram_message_links.load_all_links()
        self.assertEqual(links, [])

    def test_add_creates_sessions_dir(self):
        telegram_message_links.add_message_link("100", 5, "task", "TASK-5")
        self.assertTrue(telegram_message_links._LINKS_PATH.exists())

    def test_entry_has_created_at(self):
        entry = telegram_message_links.add_message_link("100", 10, "task", "TASK-10")
        self.assertIn("created_at", entry)
        self.assertIsInstance(entry["created_at"], str)

    def test_int_chat_id_stringified(self):
        telegram_message_links.add_message_link(100, 7, "task", "TASK-7")
        result = telegram_message_links.find_link("100", 7)
        self.assertIsNotNone(result)

    def test_find_by_int_chat_id(self):
        telegram_message_links.add_message_link("100", 8, "task", "TASK-8")
        result = telegram_message_links.find_link(100, 8)
        self.assertIsNotNone(result)
        self.assertEqual(result["work_item_id"], "TASK-8")


if __name__ == "__main__":
    unittest.main()
