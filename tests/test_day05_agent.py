"""离线验证人工确认边界与模型失败路径；不调用真实 API。"""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from day04_draft import TicketDraft
from day05_agent import confirm_from_console, extract_proposal, main
from day05_store import TicketStore

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data/day05_draft.json"


def fake_client(content: str | None, finish_reason: str = "stop") -> Mock:
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))
    ])
    return client


class ConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "tickets.sqlite3"
        self.store = TicketStore(self.path)
        self.draft = TicketDraft.model_validate_json(SAMPLE_PATH.read_text(encoding="utf-8"))
        self.record = self.store.save_draft(self.draft)

    def test_cancel_or_generic_yes_cannot_create(self) -> None:
        for answer in ("", "是", "确认", "确认创建 Dwrong", "帮我创建"):
            with self.subTest(answer=answer), redirect_stdout(io.StringIO()):
                result = confirm_from_console(self.store, self.record["draft_id"], input_fn=lambda _: answer)
                self.assertIsNone(result)
                self.assertIsNone(self.store.get_draft(self.record["draft_id"])["ticket_id"])

    def test_exact_confirmation_and_repeat_use_same_ticket(self) -> None:
        draft_id = self.record["draft_id"]
        with redirect_stdout(io.StringIO()):
            result = confirm_from_console(self.store, draft_id, input_fn=lambda _: f"确认创建 {draft_id}")
            repeated = confirm_from_console(self.store, draft_id, input_fn=Mock(side_effect=AssertionError))
        self.assertTrue(result["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(result["ticket"]["ticket_id"], repeated["ticket"]["ticket_id"])

    def test_preview_precedes_confirmation(self) -> None:
        output = io.StringIO()

        def user_input(prompt: str) -> str:
            self.assertIn(self.draft.description, output.getvalue())
            self.assertIn(self.draft.occurred_at, output.getvalue())
            self.assertIn(self.record["draft_id"], prompt)
            return "取消"

        with redirect_stdout(output):
            confirm_from_console(self.store, self.record["draft_id"], input_fn=user_input)

    def test_eof_or_interrupt_cancels(self) -> None:
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error), redirect_stdout(io.StringIO()):
                result = confirm_from_console(self.store, self.record["draft_id"], input_fn=Mock(side_effect=error))
                self.assertIsNone(result)
                self.assertIsNone(self.store.get_draft(self.record["draft_id"])["ticket_id"])

    def test_cli_get_preserves_generated_ticket_id(self) -> None:
        result = self.store.confirm_draft(self.record["draft_id"], confirmed=True)
        ticket_id = result["ticket"]["ticket_id"]
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--db", str(self.path), "get", ticket_id])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["ticket_id"], ticket_id)


class ProposalTests(unittest.TestCase):
    def test_valid_draft_parses_without_creating_ticket(self) -> None:
        content = json.dumps({"draft": json.loads(SAMPLE_PATH.read_text(encoding="utf-8")),
                              "missing_fields": [], "follow_up": ""})
        proposal = extract_proposal(fake_client(content), "fake-model", "虚构完整报修描述")
        self.assertIsNotNone(proposal.draft)

    def test_missing_fields_request_more_information(self) -> None:
        content = '{"draft":null,"missing_fields":["occurred_at"],"follow_up":"请提供发生时间。"}'
        proposal = extract_proposal(fake_client(content), "fake-model", "报修")
        self.assertIsNone(proposal.draft)
        self.assertEqual(proposal.missing_fields, ["occurred_at"])

    def test_incomplete_output_cannot_be_saved(self) -> None:
        for content, reason in ((None, "stop"), ("", "stop"), ("{}", "length")):
            with self.subTest(content=content, reason=reason), self.assertRaises(ValueError):
                extract_proposal(fake_client(content, reason), "fake-model", "报修")

    def test_inconsistent_or_unexpected_fields_rejected(self) -> None:
        for content in (
            '{"draft":null,"missing_fields":[],"follow_up":""}',
            '{"draft":null,"missing_fields":["unknown"],"follow_up":"补充"}',
            '{"draft":null,"missing_fields":["occurred_at"],"follow_up":"补充","created":true}',
        ):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                extract_proposal(fake_client(content), "fake-model", "报修")

    def test_missing_information_does_not_initialize_database(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tickets.sqlite3"
            proposal = extract_proposal(fake_client(
                '{"draft":null,"missing_fields":["occurred_at"],"follow_up":"请补充时间。"}'
            ), "fake-model", "报修")
            with patch("day05_agent.propose_with_model", return_value=proposal), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--db", str(path), "prepare", "不完整报修描述"]), 0)
            self.assertFalse(path.exists())

    def test_invalid_json_file_does_not_initialize_database(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tickets.sqlite3"
            draft_path = Path(folder) / "invalid.json"
            draft_path.write_text('{"title":"只有标题"}', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--db", str(path), "prepare", "--from-json", str(draft_path)]), 1)
            self.assertFalse(path.exists())

    def test_model_failure_does_not_initialize_database(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tickets.sqlite3"
            with patch("day05_agent.propose_with_model", side_effect=RuntimeError("模拟 API 超时")), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--db", str(path), "prepare", "报修描述"]), 1)
            self.assertFalse(path.exists())

    def test_sample_ticket_query_does_not_initialize_database(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "tickets.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--db", str(path), "get", " t1004 "]), 0)
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(output.getvalue())["ticket_id"], "T1004")


if __name__ == "__main__":
    unittest.main()
