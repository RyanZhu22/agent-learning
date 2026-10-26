"""离线验证第 7 天：评测集加载、评分口径、确认边界与摘要生成。

不调用 DeepSeek，不依赖知识库索引；检索层只测评分函数，不测真实召回。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from day03_rag import RagAnswer
from day07_eval import (
    EvalCase,
    analyze_failures,
    check_bad_phrase,
    check_idempotent_confirm,
    check_unconfirmed_draft,
    grade_agent,
    grade_classify,
    grade_draft,
    grade_retrieval,
    load_cases,
    write_summary,
)


def make_case(**fields) -> EvalCase:
    return EvalCase.model_validate(fields)


class LoadCasesTests(unittest.TestCase):
    def test_fixed_suite_covers_every_layer(self):
        cases = load_cases()
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.id for case in cases}), len(cases))
        self.assertEqual(
            {case.layer for case in cases},
            {"retrieval", "contract", "classify", "draft", "agent"},
        )

    def test_case_missing_layer_field_is_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            path.write_text(
                '{"id":"D7-R99","layer":"retrieval","question":"只有问题"}\n', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_duplicate_ids_are_rejected(self):
        line = '{"id":"D7-C01","layer":"classify","question":"q","expected_intent":"knowledge_qa"}'
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            path.write_text(line + "\n" + line + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_empty_suite_is_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            path.write_text("\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)


class RetrievalGradeTests(unittest.TestCase):
    def setUp(self):
        self.case = make_case(
            id="D7-R01", layer="retrieval", question="端口是多少？",
            expected_source="mail_notifications.md", expected_fact="587",
        )

    def test_source_and_fact_both_hit(self):
        hits = [{"id": "mail_notifications.md#chunk-1", "source": "mail_notifications.md",
                 "text": "端口为587，并启用STARTTLS加密。"}]
        passed, reason, detail = grade_retrieval(self.case, hits)
        self.assertTrue(passed, detail)
        self.assertIsNone(reason)

    def test_wrong_document_is_source_miss(self):
        hits = [{"id": "password_reset.md#chunk-1", "source": "password_reset.md", "text": "10分钟"}]
        passed, reason, _ = grade_retrieval(self.case, hits)
        self.assertFalse(passed)
        self.assertEqual(reason, "source_miss")

    def test_right_document_without_fact_is_fact_miss(self):
        hits = [{"id": "mail_notifications.md#chunk-2", "source": "mail_notifications.md",
                 "text": "最多重试4次。"}]
        passed, reason, _ = grade_retrieval(self.case, hits)
        self.assertFalse(passed)
        self.assertEqual(reason, "fact_miss")

    def test_empty_hits_are_source_miss(self):
        passed, reason, _ = grade_retrieval(self.case, [])
        self.assertFalse(passed)
        self.assertEqual(reason, "source_miss")


class ClassifyGradeTests(unittest.TestCase):
    def setUp(self):
        self.case = make_case(
            id="D7-C02", layer="classify", question="查询工单 T1001",
            expected_intent="ticket_query",
        )

    def test_matching_intent_passes(self):
        passed, reason, _ = grade_classify(self.case, "ticket_query")
        self.assertTrue(passed)
        self.assertIsNone(reason)

    def test_different_intent_is_reported(self):
        passed, reason, detail = grade_classify(self.case, "knowledge_qa")
        self.assertFalse(passed)
        self.assertEqual(reason, "intent_mismatch")
        self.assertIn("ticket_query", detail)


class DraftGradeTests(unittest.TestCase):
    def test_ready_status_passes_without_missing_fields(self):
        case = make_case(id="D7-D02", layer="draft", question="完整描述",
                         expected_status="ready")
        passed, reason, _ = grade_draft(case, "ready", [])
        self.assertTrue(passed)
        self.assertIsNone(reason)

    def test_status_mismatch_is_reported(self):
        case = make_case(id="D7-D02", layer="draft", question="完整描述",
                         expected_status="ready")
        passed, reason, _ = grade_draft(case, "needs_information", ["occurred_at"])
        self.assertFalse(passed)
        self.assertEqual(reason, "status_mismatch")

    def test_missing_field_must_be_covered(self):
        case = make_case(
            id="D7-D01", layer="draft", question="邮件通知失败",
            expected_status="needs_information",
            expected_missing=["reproduction_steps", "occurred_at"],
        )
        passed, reason, detail = grade_draft(case, "needs_information", ["reproduction_steps"])
        self.assertFalse(passed)
        self.assertEqual(reason, "missing_fields_mismatch")
        self.assertIn("occurred_at", detail)

    def test_extra_missing_fields_are_allowed(self):
        case = make_case(
            id="D7-D01", layer="draft", question="邮件通知失败",
            expected_status="needs_information", expected_missing=["occurred_at"],
        )
        passed, _, _ = grade_draft(case, "needs_information", ["occurred_at", "actual_result"])
        self.assertTrue(passed)


class AgentGradeTests(unittest.TestCase):
    def test_ticket_answer_passes(self):
        case = make_case(
            id="D7-A01", layer="agent", question="查询工单 T1004",
            expect_can_answer=True, expect_contains=["已解决"], expect_min_sources=0,
        )
        reply = RagAnswer(can_answer=True, answer="工单 T1004 当前状态为已解决。", source_ids=[])
        passed, reason, _ = grade_agent(case, reply)
        self.assertTrue(passed)
        self.assertIsNone(reason)

    def test_should_refuse_but_answered(self):
        case = make_case(
            id="D7-A03", layer="agent", question="资料外问题",
            expect_can_answer=False, expect_min_sources=0,
        )
        reply = RagAnswer(can_answer=True, answer="一般需要 3 个工作日。", source_ids=[])
        passed, reason, _ = grade_agent(case, reply)
        self.assertFalse(passed)
        self.assertEqual(reason, "can_answer_mismatch")

    def test_missing_expected_text(self):
        case = make_case(
            id="D7-A02", layer="agent", question="重试几次",
            expect_can_answer=True, expect_contains=["4"], expect_min_sources=1,
        )
        reply = RagAnswer(can_answer=True, answer="最多重试五次。", source_ids=["S1"])
        passed, reason, detail = grade_agent(case, reply)
        self.assertFalse(passed)
        self.assertEqual(reason, "answer_text_miss")
        self.assertIn("4", detail)

    def test_source_count_too_low(self):
        case = make_case(
            id="D7-A02", layer="agent", question="重试几次",
            expect_can_answer=True, expect_contains=["4"], expect_min_sources=1,
        )
        reply = RagAnswer(can_answer=True, answer="最多重试4次。", source_ids=[])
        passed, reason, _ = grade_agent(case, reply)
        self.assertFalse(passed)
        self.assertEqual(reason, "source_count_miss")


class ContractCheckTests(unittest.TestCase):
    def test_unconfirmed_draft_never_creates_ticket(self):
        with TemporaryDirectory() as folder:
            passed, detail = check_unconfirmed_draft(Path(folder))
        self.assertTrue(passed, detail)

    def test_wrong_confirmation_phrase_is_rejected(self):
        with TemporaryDirectory() as folder:
            passed, detail = check_bad_phrase(Path(folder))
        self.assertTrue(passed, detail)

    def test_repeated_confirmation_returns_same_ticket(self):
        with TemporaryDirectory() as folder:
            passed, detail = check_idempotent_confirm(Path(folder))
        self.assertTrue(passed, detail)


class ReportTests(unittest.TestCase):
    def test_failures_grouped_by_layer_and_reason(self):
        rows = [
            {"id": "D7-R01", "layer": "retrieval", "passed": False, "reason": "source_miss"},
            {"id": "D7-R02", "layer": "retrieval", "passed": False, "reason": "source_miss"},
            {"id": "D7-C01", "layer": "classify", "passed": False, "reason": "intent_mismatch"},
            {"id": "D7-K01", "layer": "contract", "passed": True, "reason": None},
        ]
        groups = analyze_failures(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["reason"], "source_miss")
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["cases"], ["D7-R01", "D7-R02"])
        self.assertIn("相似度", groups[0]["hint"])

    def test_passing_run_has_no_failure_groups(self):
        rows = [{"id": "D7-R01", "layer": "retrieval", "passed": True, "reason": None}]
        self.assertEqual(analyze_failures(rows), [])

    def test_summary_lists_failures_and_metrics(self):
        report = {
            "generated_at": "2026-10-26T12:00:00+00:00",
            "mode": "offline",
            "model": None,
            "top_k": 3,
            "min_similarity": 0.35,
            "overall": {"total": 2, "passed": 1, "rate": 0.5},
            "layers": {
                "retrieval": {"label": "检索", "total": 2, "passed": 1, "rate": 0.5},
            },
            "results": [],
            "failure_analysis": [{
                "layer": "retrieval", "reason": "fact_miss", "count": 1,
                "cases": ["D7-R01"], "hint": "答案可能跨片段。",
            }],
        }
        with TemporaryDirectory() as folder:
            path = Path(folder) / "summary.md"
            write_summary(report, path)
            text = path.read_text(encoding="utf-8")
        self.assertIn("总通过：1/2", text)
        self.assertIn("fact_miss", text)
        self.assertIn("答案可能跨片段。", text)
        self.assertIn("指标口径", text)


if __name__ == "__main__":
    unittest.main()
