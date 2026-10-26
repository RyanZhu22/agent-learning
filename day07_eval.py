"""第 7 天：固定评测集、失败分析与上线前自检。

离线模式只运行本地检索和确认边界检查，不调用模型、不需要密钥。
加 --live 追加分类、草稿整理和端到端 Agent 的真实 API 评测。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "data/day07_cases.jsonl"
REPORT_PATH = ROOT / "artifacts/day07_report.json"
SUMMARY_PATH = ROOT / "artifacts/day07_summary.md"
SAMPLE_DRAFT_PATH = ROOT / "data/day05_draft.json"

LAYERS = ("retrieval", "contract", "classify", "draft", "agent")
LIVE_LAYERS = {"classify", "draft", "agent"}
LAYER_LABELS = {
    "retrieval": "检索",
    "contract": "确认边界",
    "classify": "分类",
    "draft": "草稿整理",
    "agent": "端到端 Agent",
}
LAYER_DESCRIPTIONS = {
    "retrieval": "正确文档进入前 3 条且片段含答案",
    "contract": "未确认不建单、错短语被拒、重复确认幂等",
    "classify": "意图标签与标注一致",
    "draft": "状态一致且缺失字段覆盖标注",
    "agent": "能回答/拒答、答案含事实、来源数量达标",
}
# 失败原因 → 排查方向。报告里的每条失败都会归到其中一类。
HINTS = {
    "source_miss": "检索没有召回正确文档：检查切分粒度、top_k、相似度阈值或问题表述。",
    "fact_miss": "召回了正确文档，但片段不含答案：答案可能跨片段或被切碎。",
    "intent_mismatch": "分类边界模糊：补充提示词中的判定标准，并把该案例留在回归集。",
    "status_mismatch": "模型对“信息是否齐全”的判断与你不同：检查追问规则与字段说明。",
    "missing_fields_mismatch": "缺失字段集合不一致：模型可能把可推断字段当成已提供。",
    "can_answer_mismatch": "该拒答时作答（或相反）：检查资料不足的提示与检索阈值。",
    "answer_text_miss": "答案缺少预期事实：可能没调用工具，或引用了错误片段。",
    "source_count_miss": "来源引用不足：检查 source_ids 规则与工具返回。",
    "guardrail_broken": "确认边界被绕过：先停下修服务端校验，不要合并提交。",
    "api_error": "API 层失败：检查密钥、余额、网络或服务状态；案例本身未必有问题。",
    "dependency_missing": "缺少运行依赖：在项目环境执行 uv sync。",
    "exception": "运行异常：按错误类型排查输入校验、索引或存储。",
    "unknown": "未归类失败：先复现单条案例，再决定是否修改评测集。",
}
API_ERROR_NAMES = {
    "APIError", "APIConnectionError", "APITimeoutError",
    "APIStatusError", "RateLimitError", "InternalServerError",
}


class EvalCase(BaseModel):
    """一行一个案例；字段随层不同，缺失必要字段在加载时就报错。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(pattern=r"^D7-[A-Z][0-9]{2}$")
    layer: Literal["retrieval", "contract", "classify", "draft", "agent"]
    question: str | None = None
    check: Literal["unconfirmed_draft", "bad_phrase", "idempotent_confirm"] | None = None
    expected_source: str | None = None
    expected_fact: str | None = None
    expected_intent: Literal["knowledge_qa", "ticket_query", "incident_report"] | None = None
    expected_status: Literal["ready", "needs_information"] | None = None
    expected_missing: list[str] = Field(default_factory=list)
    expect_can_answer: bool | None = None
    expect_contains: list[str] = Field(default_factory=list)
    expect_min_sources: int | None = None
    note: str = ""

    @model_validator(mode="after")
    def require_layer_fields(self) -> EvalCase:
        if self.layer != "contract" and not (self.question and self.question.strip()):
            raise ValueError(f"{self.layer} 案例需要 question")
        if self.layer == "retrieval" and not (self.expected_source and self.expected_fact):
            raise ValueError("retrieval 案例需要 expected_source 和 expected_fact")
        if self.layer == "contract" and self.check is None:
            raise ValueError("contract 案例需要 check")
        if self.layer == "classify" and self.expected_intent is None:
            raise ValueError("classify 案例需要 expected_intent")
        if self.layer == "draft" and self.expected_status is None:
            raise ValueError("draft 案例需要 expected_status")
        if self.layer == "agent" and self.expect_can_answer is None:
            raise ValueError("agent 案例需要 expect_can_answer")
        return self


def load_cases(path: Path = CASES_PATH) -> list[EvalCase]:
    if not path.exists():
        raise ValueError(f"评测集不存在：{path.name}")
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(EvalCase.model_validate_json(line))
        except ValidationError as error:
            raise ValueError(f"第 {number} 行案例不合法，共 {error.error_count()} 处字段问题。") from None
    if not cases:
        raise ValueError("评测集不能为空。")
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("案例编号重复，报告将无法区分记录。")
    return cases


def sample_draft() -> dict:
    return json.loads(SAMPLE_DRAFT_PATH.read_text(encoding="utf-8-sig"))


def count_tickets(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]


# ---- 评分：纯函数，便于离线测试；不读写数据库、不调用模型 ----

def grade_retrieval(case: EvalCase, hits: list[dict]) -> tuple[bool, str | None, str]:
    ids = [hit["id"] for hit in hits]
    matching = [hit for hit in hits if hit["source"] == case.expected_source]
    if not matching:
        return False, "source_miss", f"前 {len(hits)} 条未命中 {case.expected_source}；实际：{ids}"
    if not any(case.expected_fact in hit["text"] for hit in matching):
        return False, "fact_miss", f"命中 {case.expected_source}，但片段不含“{case.expected_fact}”"
    return True, None, f"命中 {matching[0]['id']}"


def grade_classify(case: EvalCase, intent: str) -> tuple[bool, str | None, str]:
    if intent != case.expected_intent:
        return False, "intent_mismatch", f"期望 {case.expected_intent}，实际 {intent}"
    return True, None, f"intent={intent}"


def grade_draft(case: EvalCase, status: str, missing_fields: list[str]) -> tuple[bool, str | None, str]:
    if status != case.expected_status:
        return False, "status_mismatch", f"期望 {case.expected_status}，实际 {status}"
    if case.expected_status == "needs_information":
        uncovered = [name for name in case.expected_missing if name not in missing_fields]
        if uncovered:
            return False, "missing_fields_mismatch", f"未列出缺失字段：{uncovered}"
        return True, None, f"缺失字段：{missing_fields}"
    return True, None, "草稿字段齐全"


def grade_agent(case: EvalCase, reply) -> tuple[bool, str | None, str]:
    if reply.can_answer != case.expect_can_answer:
        return False, "can_answer_mismatch", f"期望 can_answer={case.expect_can_answer}，实际 {reply.can_answer}"
    missing_text = [text for text in case.expect_contains if text not in reply.answer]
    if missing_text:
        return False, "answer_text_miss", f"答案缺少：{missing_text}"
    if case.expect_min_sources is not None and len(reply.source_ids) < case.expect_min_sources:
        return False, "source_count_miss", f"来源 {len(reply.source_ids)} 条，要求至少 {case.expect_min_sources} 条"
    return True, None, f"来源 {reply.source_ids}"


# ---- 确认边界：用临时数据库和模拟模型检查，不产生真实学习数据 ----

def check_unconfirmed_draft(folder: Path) -> tuple[bool, str]:
    from day04_draft import TicketDraft
    from day05_store import TicketStore

    database = folder / "tickets.sqlite3"
    store = TicketStore(database)
    saved = store.save_draft(TicketDraft.model_validate(sample_draft()))
    for confirmed in (False, None, "yes"):
        try:
            store.confirm_draft(saved["draft_id"], confirmed=confirmed)
        except ValueError:
            continue
        return False, f"confirmed={confirmed!r} 时仍然创建了工单"
    if count_tickets(database) != 0:
        return False, "拒绝确认后数据库仍出现工单"
    if store.get_draft(saved["draft_id"])["status"] != "pending":
        return False, "被拒绝的草稿状态被改动"
    return True, "三种确认取值均被拒绝，工单数为 0"


def make_test_client(folder: Path):
    from fastapi.testclient import TestClient

    from day04_draft import TicketDraft
    from day05_agent import DraftProposal
    from day06_api import create_app

    def proposer(question: str) -> DraftProposal:
        return DraftProposal(
            draft=TicketDraft.model_validate(sample_draft()), missing_fields=[], follow_up=""
        )

    app = create_app(
        db_path=folder / "tickets.sqlite3",
        log_path=folder / "api.log",
        proposer=proposer,
    )
    return TestClient(app)


def check_bad_phrase(folder: Path) -> tuple[bool, str]:
    with make_test_client(folder) as client:
        created = client.post("/api/drafts", json=sample_draft())
        if created.status_code != 201:
            return False, f"保存草稿返回 {created.status_code}，期望 201"
        draft_id = created.json()["draft_id"]
        response = client.post(
            f"/api/drafts/{draft_id}/confirm", json={"confirmation_text": "确认创建"}
        )
        if response.status_code != 400:
            return False, f"错误短语返回 {response.status_code}，期望 400"
        if response.json()["error"]["code"] != "confirmation_required":
            return False, "错误码不是 confirmation_required"
    if count_tickets(folder / "tickets.sqlite3") != 0:
        return False, "拒绝后仍写入了工单"
    return True, "错误短语返回 400，工单数为 0"


def check_idempotent_confirm(folder: Path) -> tuple[bool, str]:
    with make_test_client(folder) as client:
        draft_id = client.post("/api/drafts", json=sample_draft()).json()["draft_id"]
        body = {"confirmation_text": f"确认创建 {draft_id}"}
        first = client.post(f"/api/drafts/{draft_id}/confirm", json=body)
        second = client.post(f"/api/drafts/{draft_id}/confirm", json=body)
    if first.status_code != 201:
        return False, f"首次确认返回 {first.status_code}，期望 201"
    if second.status_code != 200:
        return False, f"重复确认返回 {second.status_code}，期望 200"
    if second.json()["created"] is not False:
        return False, "重复确认没有返回 created=false"
    if first.json()["ticket"]["ticket_id"] != second.json()["ticket"]["ticket_id"]:
        return False, "两次确认得到不同工单"
    if count_tickets(folder / "tickets.sqlite3") != 1:
        return False, "重复确认后工单数不是 1"
    return True, "首次 201、再次 200，返回同一张工单"


CONTRACT_CHECKS: dict[str, Callable[[Path], tuple[bool, str]]] = {
    "unconfirmed_draft": check_unconfirmed_draft,
    "bad_phrase": check_bad_phrase,
    "idempotent_confirm": check_idempotent_confirm,
}


# ---- 执行：每层把真实结果交给上面的评分函数 ----

def make_result(case: EvalCase, passed: bool, reason: str | None, detail: str) -> dict:
    return {"id": case.id, "layer": case.layer, "passed": passed, "reason": reason, "detail": detail}


def run_retrieval(case: EvalCase, top_k: int, min_similarity: float) -> dict:
    from day03_retrieval import search_docs

    hits = search_docs(case.question, top_k, min_similarity)
    passed, reason, detail = grade_retrieval(case, hits)
    return make_result(case, passed, reason, detail)


def run_contract(case: EvalCase) -> dict:
    with TemporaryDirectory(prefix=f"day07-{case.id}-") as folder:
        passed, detail = CONTRACT_CHECKS[case.check](Path(folder))
    return make_result(case, passed, None if passed else "guardrail_broken", detail)


def run_classify(case: EvalCase, client, model: str) -> dict:
    from day01_classify import classify

    result, _ = classify(client, model, case.question)
    passed, reason, detail = grade_classify(case, result.intent)
    return make_result(case, passed, reason, detail)


def run_draft(case: EvalCase, client, model: str) -> dict:
    from day05_agent import extract_proposal

    proposal = extract_proposal(client, model, case.question)
    status = "ready" if proposal.draft is not None else "needs_information"
    passed, reason, detail = grade_draft(case, status, list(proposal.missing_fields))
    return make_result(case, passed, reason, detail)


def run_agent(case: EvalCase, client, model: str) -> dict:
    from day04_agent import run_agent as agent

    # 第 4 天的入口会把每轮工具选择打印到终端；评测时收起来，只保留评分结果。
    with redirect_stdout(StringIO()):
        reply, _ = agent(client, model, case.question)
    passed, reason, detail = grade_agent(case, reply)
    return make_result(case, passed, reason, detail)


def describe_error(error: Exception) -> tuple[str, str]:
    name = type(error).__name__
    if name in API_ERROR_NAMES:
        status = getattr(error, "status_code", None)
        return "api_error", f"{name}（HTTP {status}）" if status else name
    if isinstance(error, ImportError):
        return "dependency_missing", f"缺少模块：{error.name or '未知'}"
    return "exception", f"{name}：{error}"[:200]


def evaluate(
    cases: list[EvalCase], *, live: bool = False, top_k: int = 3, min_similarity: float = 0.35
) -> dict:
    client = model = None
    if live:
        from llm_config import create_client

        client, model = create_client()

    results = []
    for case in cases:
        started = perf_counter()
        try:
            if case.layer == "retrieval":
                row = run_retrieval(case, top_k, min_similarity)
            elif case.layer == "contract":
                row = run_contract(case)
            elif case.layer == "classify":
                row = run_classify(case, client, model)
            elif case.layer == "draft":
                row = run_draft(case, client, model)
            else:
                row = run_agent(case, client, model)
        except Exception as error:  # 单条失败不终止整轮评测。
            reason, detail = describe_error(error)
            row = make_result(case, False, reason, detail)
        row["elapsed_seconds"] = round(perf_counter() - started, 3)
        results.append(row)
        mark = "通过" if row["passed"] else f"失败（{row['reason']}）"
        print(f"{case.id} {LAYER_LABELS[case.layer]}：{mark}")

    layers = {}
    for layer in LAYERS:
        rows = [row for row in results if row["layer"] == layer]
        if not rows:
            continue
        passed = sum(row["passed"] for row in rows)
        layers[layer] = {
            "label": LAYER_LABELS[layer],
            "total": len(rows),
            "passed": passed,
            "rate": round(passed / len(rows), 4),
        }
    passed_total = sum(row["passed"] for row in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if live else "offline",
        "model": model,
        "top_k": top_k,
        "min_similarity": min_similarity,
        "overall": {
            "total": len(results),
            "passed": passed_total,
            "rate": round(passed_total / len(results), 4) if results else 0.0,
        },
        "layers": layers,
        "results": results,
        "failure_analysis": analyze_failures(results),
    }
    return report


def analyze_failures(results: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for row in results:
        if row["passed"]:
            continue
        key = (row["layer"], row["reason"] or "unknown")
        group = groups.setdefault(key, {
            "layer": row["layer"],
            "reason": row["reason"] or "unknown",
            "count": 0,
            "cases": [],
            "hint": HINTS.get(row["reason"] or "unknown", HINTS["unknown"]),
        })
        group["count"] += 1
        group["cases"].append(row["id"])
    return sorted(groups.values(), key=lambda item: (-item["count"], item["layer"], item["reason"]))


def write_summary(report: dict, path: Path = SUMMARY_PATH) -> None:
    lines = [
        "# 第 7 天评测摘要",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 模式：{'真实模型' if report['mode'] == 'live' else '离线（未调用模型）'}",
        f"- 模型：{report['model'] or '—'}",
        f"- 检索参数：top_k={report['top_k']}，min_similarity={report['min_similarity']}",
        f"- 总通过：{report['overall']['passed']}/{report['overall']['total']}",
        "",
        "| 层 | 检查内容 | 通过 | 总数 | 通过率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for layer, item in report["layers"].items():
        lines.append(
            f"| {item['label']} | {LAYER_DESCRIPTIONS[layer]} | "
            f"{item['passed']} | {item['total']} | {item['rate']:.0%} |"
        )
    lines += ["", "## 失败分析", ""]
    if not report["failure_analysis"]:
        lines.append("没有失败案例。这只说明这批固定案例通过，不代表所有输入都正确。")
    else:
        lines += ["| 层 | 原因 | 条数 | 案例 | 下一步 |", "| --- | --- | --- | --- | --- |"]
        for group in report["failure_analysis"]:
            lines.append(
                f"| {LAYER_LABELS[group['layer']]} | {group['reason']} | {group['count']} | "
                f"{', '.join(group['cases'])} | {group['hint']} |"
            )
    lines += [
        "",
        "## 指标口径",
        "",
        "- 检索：正确文档进入前 3 条，且片段确实包含预期事实，才算通过；相似度不是正确率。",
        "- 确认边界：未确认不建单、错短语被拒、重复确认幂等，三者缺一不可。",
        "- 分类：只看 intent 是否与标注一致，不看 summary 措辞。",
        "- 草稿：状态一致，且缺失字段覆盖标注；模型可以多报，不能漏报。",
        "- 端到端：can_answer、答案关键词、来源数量同时满足才算通过。",
        "",
        "完整逐条结果见 `artifacts/day07_report.json`。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_failures(report: dict) -> None:
    groups = report["failure_analysis"]
    if not groups:
        print("\n没有失败案例。固定案例通过不等于系统正确，人工抽查仍要做。")
        return
    print("\n失败分析：")
    for group in groups:
        print(f"- [{LAYER_LABELS[group['layer']]}] {group['reason']} × {group['count']}"
              f"（{', '.join(group['cases'])}）")
        print(f"  {group['hint']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="追加分类、草稿与 Agent 的真实模型评测（会调用 API）")
    parser.add_argument("--layer", choices=LAYERS, help="只运行某一层")
    parser.add_argument("--top-k", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--min-similarity", type=float, default=0.35)
    parser.add_argument("--list", action="store_true", help="只列出案例，不执行")
    args = parser.parse_args(argv)
    if not -1 <= args.min_similarity <= 1:
        parser.error("相似度阈值必须在 -1 到 1 之间。")

    try:
        cases = load_cases()
    except ValueError as error:
        print(f"已停止：{error}")
        return 1

    if args.list:
        for layer in LAYERS:
            rows = [case for case in cases if case.layer == layer]
            if rows:
                print(f"\n[{LAYER_LABELS[layer]}] {len(rows)} 条")
                for case in rows:
                    print(f"  {case.id} {case.question or case.check} {case.note}")
        return 0

    if args.layer:
        cases = [case for case in cases if case.layer == args.layer]
        if not cases:
            print("该层没有案例。")
            return 1
    if not args.live:
        skipped = [case for case in cases if case.layer in LIVE_LAYERS]
        cases = [case for case in cases if case.layer not in LIVE_LAYERS]
        if skipped:
            print(f"离线模式：跳过 {len(skipped)} 条真实模型案例，加 --live 运行。")
    if not cases:
        print("没有可运行的案例；真实模型案例请加 --live。")
        return 0

    print(f"共 {len(cases)} 条案例；模式：{'真实模型' if args.live else '离线'}。")
    try:
        report = evaluate(cases, live=args.live, top_k=args.top_k, min_similarity=args.min_similarity)
    except ValueError as error:
        print(f"已停止：{error}")
        return 1

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(report)

    overall = report["overall"]
    print(f"\n总通过：{overall['passed']}/{overall['total']}")
    for item in report["layers"].values():
        print(f"  {item['label']}：{item['passed']}/{item['total']}（{item['rate']:.0%}）")
    print_failures(report)
    print(f"\n报告：{REPORT_PATH.relative_to(ROOT)}")
    print(f"摘要：{SUMMARY_PATH.relative_to(ROOT)}")
    return 0 if overall["passed"] == overall["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
