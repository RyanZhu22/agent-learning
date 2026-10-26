"""离线验证第 6 天的 HTTP 边界；所有模型调用都替换为本地函数。"""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from day04_draft import TicketDraft
from day05_agent import DraftProposal
from day05_store import StoreError
from day06_api import create_app


def example_draft() -> dict:
    return {
        "title": "测试邮件发送超时",
        "description": "邮件通知功能异常，测试邮箱收不到测试邮件。",
        "reproduction_steps": "进入通知设置，点击发送测试邮件。",
        "expected_result": "测试邮箱收到邮件。",
        "actual_result": "页面提示连接超时，邮箱未收到邮件。",
        "occurred_at": "2026年10月24日10:00，UTC+8",
    }


def offline_proposal(question: str) -> DraftProposal:
    return DraftProposal(
        draft=TicketDraft.model_validate(example_draft()),
        missing_fields=[],
        follow_up="",
    )


class Day06ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory(prefix="day06-api-test-")
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "tickets.sqlite3"
        self.log_path = Path(self.temp_directory.name) / "requests.jsonl"

    def make_app(self, proposer=offline_proposal):
        return create_app(
            db_path=self.db_path,
            log_path=self.log_path,
            proposer=proposer,
        )

    def count_records(self, table: str) -> int:
        # 表名来自测试自身，不接受请求数据。
        self.assertIn(table, ("drafts", "tickets"))
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def assert_request_id(self, response) -> str:
        request_id = response.headers.get("X-Request-ID")
        self.assertIsInstance(request_id, str)
        self.assertTrue(request_id)
        return request_id

    def assert_api_error(self, response, status_code: int) -> str:
        self.assertEqual(response.status_code, status_code, response.text)
        request_id = self.assert_request_id(response)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "request_id"})
        self.assertEqual(body["error"]["request_id"], request_id)
        self.assertTrue(body["error"]["code"])
        self.assertTrue(body["error"]["message"])
        return request_id

    def read_logs(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_health_and_page_work_without_model_or_external_cdn(self):
        def never_call_model(question):
            self.fail("健康检查和网页不应调用模型。")

        with TestClient(self.make_app(never_call_model)) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
            self.assert_request_id(response)
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("text/html", page.headers["content-type"])
            self.assertIn("<html", page.text.lower())
            self.assertNotRegex(page.text, r"(?i)(?:src|href)\s*=\s*['\"]https?://")
            self.assert_request_id(page)
        self.assertEqual(self.count_records("drafts"), 0)

    def test_manual_draft_requires_exact_confirmation_and_is_idempotent(self):
        with TestClient(self.make_app()) as client:
            response = client.post("/api/drafts", json=example_draft())
            self.assertEqual(response.status_code, 201, response.text)
            self.assert_request_id(response)
            saved = response.json()
            draft_id = saved["draft_id"]
            self.assertTrue(draft_id.startswith("D"))
            self.assertEqual(saved["draft"], example_draft())
            self.assertEqual(saved["status"], "pending")
            self.assertIsNone(saved["ticket_id"])
            self.assertEqual(self.count_records("tickets"), 0)
            self.assertEqual(client.get(f"/api/drafts/{draft_id}").json(), saved)

            url = f"/api/drafts/{draft_id}/confirm"
            self.assert_api_error(client.post(url, json={}), 422)
            self.assert_api_error(
                client.post(url, json={"confirmation_text": "取消"}), 400
            )
            self.assert_api_error(
                client.post(url, json={"confirmation_text": "确认创建 Dother"}), 400
            )
            self.assertEqual(self.count_records("tickets"), 0)
            self.assertEqual(client.get(f"/api/drafts/{draft_id}").json(), saved)

            body = {"confirmation_text": f"确认创建 {draft_id}"}
            first = client.post(url, json=body)
            self.assertEqual(first.status_code, 201, first.text)
            self.assert_request_id(first)
            first_result = first.json()
            self.assertIs(first_result["created"], True)
            ticket = first_result["ticket"]
            self.assertTrue(ticket["ticket_id"].startswith("N"))
            self.assertEqual(ticket["draft_id"], draft_id)
            self.assertEqual(ticket["status"], "待处理")
            for key, value in example_draft().items():
                self.assertEqual(ticket[key], value)
            fetched = client.get(f"/api/tickets/{ticket['ticket_id']}")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json(), ticket)

            second = client.post(url, json=body)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertIs(second.json()["created"], False)
            self.assertEqual(second.json()["ticket"], ticket)
            self.assertEqual(self.count_records("tickets"), 1)
            submitted = client.get(f"/api/drafts/{draft_id}").json()
            self.assertEqual(submitted["status"], "submitted")
            self.assertEqual(submitted["ticket_id"], ticket["ticket_id"])

    def test_prepare_missing_information_saves_nothing(self):
        questions = []

        def incomplete(question):
            questions.append(question)
            return DraftProposal(
                draft=None,
                missing_fields=["occurred_at"],
                follow_up="请补充发生时间。",
            )

        with TestClient(self.make_app(incomplete)) as client:
            response = client.post("/api/drafts/prepare", json={"question": "邮件发送失败"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json(),
                {
                    "status": "needs_information",
                    "draft": None,
                    "follow_up": "请补充发生时间。",
                    "missing_fields": ["occurred_at"],
                },
            )
            self.assert_request_id(response)
            self.assertEqual(questions, ["邮件发送失败"])
            self.assertEqual(self.count_records("drafts"), 0)
            self.assertEqual(self.count_records("tickets"), 0)

    def test_prepare_complete_proposal_only_saves_pending_draft(self):
        with TestClient(self.make_app()) as client:
            response = client.post("/api/drafts/prepare", json={"question": "完整报修描述"})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["follow_up"], "")
            self.assertEqual(result["missing_fields"], [])
            self.assertEqual(result["draft"]["draft"], example_draft())
            self.assertEqual(result["draft"]["status"], "pending")
            self.assertIsNone(result["draft"]["ticket_id"])
            self.assertEqual(self.count_records("drafts"), 1)
            self.assertEqual(self.count_records("tickets"), 0)

    def test_new_app_keeps_draft_and_ticket(self):
        with TestClient(self.make_app()) as client:
            saved = client.post("/api/drafts", json=example_draft()).json()
            draft_id = saved["draft_id"]
            result = client.post(
                f"/api/drafts/{draft_id}/confirm",
                json={"confirmation_text": f"确认创建 {draft_id}"},
            ).json()
            ticket = result["ticket"]
        with TestClient(self.make_app()) as reopened:
            draft = reopened.get(f"/api/drafts/{draft_id}").json()
            self.assertEqual(draft["status"], "submitted")
            self.assertEqual(draft["ticket_id"], ticket["ticket_id"])
            self.assertEqual(
                reopened.get(f"/api/tickets/{ticket['ticket_id']}").json(), ticket
            )

    def test_invalid_bodies_are_strict_and_never_save_records(self):
        def never_call_model(question):
            self.fail("非法输入应在调用模型前被拒绝。")

        invalid_bodies = [
            ("/api/drafts", {}),
            ("/api/drafts", {**example_draft(), "title": 1}),
            ("/api/drafts", {**example_draft(), "description": "  "}),
            ("/api/drafts", {**example_draft(), "unexpected": "value"}),
            ("/api/drafts/prepare", {}),
            ("/api/drafts/prepare", {"question": 1}),
            ("/api/drafts/prepare", {"question": "  "}),
            ("/api/drafts/prepare", {"question": "x" * 5001}),
            ("/api/drafts/prepare", {"question": "问题", "unexpected": True}),
            ("/api/drafts/Dmissing/confirm", {"confirmation_text": 1}),
            (
                "/api/drafts/Dmissing/confirm",
                {"confirmation_text": "确认创建 Dmissing", "confirmed": True},
            ),
        ]
        with TestClient(self.make_app(never_call_model)) as client:
            for url, body in invalid_bodies:
                with self.subTest(url=url, body=body):
                    self.assert_api_error(client.post(url, json=body), 422)
            self.assert_api_error(
                client.post(
                    "/api/drafts/prepare",
                    content='{"question":',
                    headers={"Content-Type": "application/json"},
                ),
                422,
            )
            self.assertEqual(self.count_records("drafts"), 0)
            self.assertEqual(self.count_records("tickets"), 0)

    def test_missing_records_return_404(self):
        with TestClient(self.make_app()) as client:
            self.assert_api_error(client.get("/api/drafts/Dffffffffffff"), 404)
            self.assert_api_error(client.get("/api/tickets/Nffffffffffff"), 404)
            self.assert_api_error(
                client.post(
                    "/api/drafts/Dffffffffffff/confirm",
                    json={"confirmation_text": "确认创建 Dffffffffffff"},
                ),
                404,
            )

    def test_store_failure_returns_503_and_does_not_expose_exception(self):
        app = self.make_app()
        with TestClient(app) as client:
            with patch.object(
                app.state.store, "save_draft", side_effect=StoreError("private database detail")
            ):
                response = client.post("/api/drafts", json=example_draft())
            self.assert_api_error(response, 503)
            self.assertNotIn("private database detail", response.text)
            self.assertEqual(self.count_records("drafts"), 0)

    def test_failed_confirmation_rolls_back_then_allows_retry(self):
        with TestClient(self.make_app()) as client:
            saved = client.post("/api/drafts", json=example_draft()).json()
            draft_id = saved["draft_id"]
            with closing(sqlite3.connect(self.db_path)) as connection, connection:
                connection.execute("""
                    CREATE TRIGGER fail_draft_update BEFORE UPDATE OF status ON drafts
                    BEGIN
                        SELECT RAISE(ABORT, 'private rollback detail');
                    END
                """)
            url = f"/api/drafts/{draft_id}/confirm"
            body = {"confirmation_text": f"确认创建 {draft_id}"}
            response = client.post(url, json=body)
            self.assert_api_error(response, 503)
            self.assertNotIn("private rollback detail", response.text)
            self.assertEqual(self.count_records("tickets"), 0)
            self.assertEqual(client.get(f"/api/drafts/{draft_id}").json(), saved)
            with closing(sqlite3.connect(self.db_path)) as connection, connection:
                connection.execute("DROP TRIGGER fail_draft_update")
            retry = client.post(url, json=body)
            self.assertEqual(retry.status_code, 201, retry.text)
            self.assertIs(retry.json()["created"], True)
            self.assertEqual(self.count_records("tickets"), 1)

    def test_error_responses_and_logs_hide_bodies_keys_and_exception_details(self):
        secret = "sk-day06-test-only-very-private-key"
        private_body = "PRIVATE-BODY-day06-邮件故障详情"

        def failing_model(question):
            raise RuntimeError(f"{secret}: {question}: private upstream detail")

        app = self.make_app(failing_model)
        responses = []
        with TestClient(app, raise_server_exceptions=False) as client:
            responses.append(
                client.post(
                    "/api/drafts",
                    json={**example_draft(), "description": private_body, secret: secret},
                    headers={"Authorization": f"Bearer {secret}"},
                )
            )
            self.assert_api_error(responses[-1], 422)
            responses.append(
                client.post("/api/drafts/prepare", json={"question": private_body})
            )
            self.assert_api_error(responses[-1], 502)
            with patch.object(
                app.state.store, "save_draft", side_effect=RuntimeError(f"{secret} {private_body}")
            ):
                responses.append(client.post("/api/drafts", json=example_draft()))
            self.assert_api_error(responses[-1], 500)
            for response in responses:
                for sensitive in (secret, private_body, "private upstream detail"):
                    self.assertNotIn(sensitive, response.text)
        log_text = self.log_path.read_text(encoding="utf-8")
        for sensitive in (secret, private_body, "private upstream detail"):
            self.assertNotIn(sensitive, log_text)
        records = self.read_logs()
        for response in responses:
            matches = [
                record for record in records
                if record.get("request_id") == response.headers["X-Request-ID"]
            ]
            self.assertTrue(matches)
            self.assertTrue(any(
                record.get("status_code") == response.status_code for record in matches
            ))
            self.assertTrue(any(
                record.get("error_code") == response.json()["error"]["code"] for record in matches
            ))

    def test_request_log_correlates_success_and_error_without_recording_body(self):
        with TestClient(self.make_app()) as client:
            responses = [
                client.get("/health"),
                client.post("/api/drafts", json=example_draft()),
                client.get("/api/drafts/Dffffffffffff"),
            ]
            request_ids = [self.assert_request_id(response) for response in responses]
        self.assertEqual(len(set(request_ids)), len(request_ids))
        records = self.read_logs()
        for response, expected_path in zip(
            responses, ("/health", "/api/drafts", "/api/drafts/{draft_id}")
        ):
            request_records = [
                record for record in records
                if record.get("request_id") == response.headers["X-Request-ID"]
                and "status_code" in record
            ]
            self.assertEqual(len(request_records), 1)
            record = request_records[0]
            self.assertEqual(record["status_code"], response.status_code)
            self.assertEqual(record["method"], response.request.method)
            self.assertEqual(record["path"], expected_path)
            self.assertIsInstance(record["elapsed_ms"], (float, int))
            self.assertGreaterEqual(record["elapsed_ms"], 0)
        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(example_draft()["description"], log_text)
        self.assertNotIn(example_draft()["occurred_at"], log_text)


if __name__ == "__main__":
    unittest.main()
