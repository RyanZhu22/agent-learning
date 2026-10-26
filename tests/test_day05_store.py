"""离线验证第 5 天的确认边界、持久化、并发幂等与事务回滚。"""

import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from day04_draft import TicketDraft
from day05_store import StoreError, TicketStore


def example_draft() -> TicketDraft:
    return TicketDraft(
        title="导出失败",
        description="点击报表导出后失败",
        reproduction_steps="打开报表，点击导出",
        expected_result="下载报表文件",
        actual_result="页面显示导出失败",
        occurred_at="今天上午 10 点",
    )


class TicketStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "tickets.sqlite3"
        self.store = TicketStore(self.db_path)

    def count_tickets(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

    def test_unconfirmed_draft_never_creates_ticket(self):
        saved = self.store.save_draft(example_draft())
        self.assertEqual(saved["status"], "pending")
        self.assertIsNone(saved["ticket_id"])
        for confirmation in (False, None, "yes", 1):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(ValueError):
                    self.store.confirm_draft(saved["draft_id"], confirmed=confirmation)
        with self.assertRaises(ValueError):
            self.store.confirm_draft(saved["draft_id"])
        self.assertEqual(self.count_tickets(), 0)
        self.assertEqual(self.store.get_draft(saved["draft_id"]), saved)

    def test_reopen_keeps_draft_and_ticket(self):
        draft = example_draft()
        saved = self.store.save_draft(draft)
        reopened = TicketStore(self.db_path)
        self.assertEqual(reopened.get_draft(saved["draft_id"]), saved)

        result = reopened.confirm_draft(saved["draft_id"], confirmed=True)
        ticket = result["ticket"]
        self.assertTrue(result["created"])
        self.assertTrue(saved["draft_id"].startswith("D"))
        self.assertTrue(ticket["ticket_id"].startswith("N"))
        self.assertEqual(ticket["draft_id"], saved["draft_id"])
        self.assertEqual(ticket["status"], "待处理")
        for key, value in draft.model_dump().items():
            self.assertEqual(ticket[key], value)

        reopened_again = TicketStore(self.db_path)
        self.assertEqual(reopened_again.get_ticket(ticket["ticket_id"]), ticket)
        submitted = reopened_again.get_draft(saved["draft_id"])
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["ticket_id"], ticket["ticket_id"])
        self.assertEqual(submitted["draft"], draft.model_dump())

    def test_repeated_confirmation_returns_same_ticket(self):
        draft_id = self.store.save_draft(example_draft())["draft_id"]
        first = self.store.confirm_draft(draft_id, confirmed=True)
        second = self.store.confirm_draft(draft_id, confirmed=True)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["ticket"], second["ticket"])
        self.assertEqual(self.count_tickets(), 1)

    def test_identical_drafts_are_independent_intents(self):
        first = self.store.save_draft(example_draft())
        second = self.store.save_draft(example_draft())
        self.assertNotEqual(first["draft_id"], second["draft_id"])
        first_ticket = self.store.confirm_draft(first["draft_id"], confirmed=True)
        second_ticket = self.store.confirm_draft(second["draft_id"], confirmed=True)
        self.assertNotEqual(
            first_ticket["ticket"]["ticket_id"], second_ticket["ticket"]["ticket_id"]
        )
        self.assertEqual(self.count_tickets(), 2)

    def test_concurrent_connections_create_only_one_ticket(self):
        draft_id = self.store.save_draft(example_draft())["draft_id"]
        stores = [TicketStore(self.db_path), TicketStore(self.db_path)]
        barrier = Barrier(2)

        def confirm(store):
            barrier.wait(timeout=5)
            return store.confirm_draft(draft_id, confirmed=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, stores))
        self.assertEqual(sorted(result["created"] for result in results), [False, True])
        self.assertEqual(results[0]["ticket"], results[1]["ticket"])
        self.assertEqual(self.count_tickets(), 1)

    def test_missing_draft_and_ticket_raise_value_error(self):
        with self.assertRaises(ValueError):
            self.store.get_draft("Dmissing")
        with self.assertRaises(ValueError):
            self.store.confirm_draft("Dmissing", confirmed=True)
        with self.assertRaises(ValueError):
            self.store.get_ticket("Nmissing")
        self.assertEqual(self.count_tickets(), 0)

    def test_failed_update_rolls_back_insert_and_allows_retry(self):
        saved = self.store.save_draft(example_draft())
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER fail_draft_update BEFORE UPDATE OF status ON drafts
                BEGIN
                    SELECT RAISE(ABORT, 'simulated private failure');
                END
            """)
        with self.assertRaises(StoreError) as error:
            self.store.confirm_draft(saved["draft_id"], confirmed=True)
        self.assertNotIn("simulated private failure", str(error.exception))
        self.assertEqual(self.count_tickets(), 0)
        self.assertEqual(self.store.get_draft(saved["draft_id"]), saved)

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_draft_update")
        result = self.store.confirm_draft(saved["draft_id"], confirmed=True)
        self.assertTrue(result["created"])
        self.assertEqual(self.count_tickets(), 1)

    def test_invalid_database_paths_raise_store_error_without_path(self):
        parent_file = Path(self.temp_directory.name) / "not_a_directory"
        parent_file.write_text("occupied", encoding="utf-8")
        for path in (Path(self.temp_directory.name), parent_file / "db.sqlite3"):
            with self.subTest(path=path):
                with self.assertRaises(StoreError) as error:
                    TicketStore(path)
                self.assertNotIn(str(path), str(error.exception))


if __name__ == "__main__":
    unittest.main()
