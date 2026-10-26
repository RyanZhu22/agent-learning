"""第 6 天：用 FastAPI 和网页操作第 5 天的本地草稿、确认与工单。"""

import argparse
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Annotated, Callable, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import Path as PathParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from day04_draft import TicketDraft
from day05_agent import DraftProposal, propose_with_model
from day05_store import DEFAULT_DB_PATH, StoreError, TicketStore

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = ROOT / "artifacts/day06_api.log"
DraftId = Annotated[str, PathParameter(pattern=r"^D[0-9a-f]{12}$")]
TicketId = Annotated[str, PathParameter(pattern=r"^N[0-9a-f]{12}$")]


class PrepareRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=5000)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    confirmation_text: str = Field(min_length=1, max_length=100)


class DraftRecord(BaseModel):
    draft_id: str
    status: Literal["pending", "submitted"]
    draft: TicketDraft
    created_at: str
    ticket_id: str | None


class TicketRecord(TicketDraft):
    ticket_id: str
    draft_id: str
    status: Literal["待处理"]
    created_at: str


class PrepareReply(BaseModel):
    status: Literal["ready", "needs_information"]
    draft: DraftRecord | None
    missing_fields: list[str]
    follow_up: str


class ConfirmReply(BaseModel):
    created: bool
    ticket: TicketRecord


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request.state.error_code = code
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "request_id": request.state.request_id,
    }})


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    log_path: Path | str = DEFAULT_LOG_PATH,
    proposer: Callable[[str], DraftProposal] | None = None,
) -> FastAPI:
    """测试可注入临时数据库和模拟模型，默认复用 Day 5 的真实实现。"""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"day06.{uuid4().hex}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        try:
            app.state.request_logger = logger
            app.state.store = TicketStore(db_path)
            app.state.proposer = proposer if proposer is not None else propose_with_model
            yield
        finally:
            logger.removeHandler(handler)
            handler.close()

    app = FastAPI(title="第 6 天：本地报修工作台", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = uuid4().hex[:12]
        request.state.request_id = request_id
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 不把异常原文、SQL、请求正文或模型响应写入日志或返回网页。
            response = error_response(request, 500, "internal_error", "服务处理失败，请根据请求编号排查日志。")
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        request.app.state.request_logger.info(json.dumps({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request_id": request_id,
            "method": request.method,
            # 记录路由模板，不记录查询参数或用户输入的路径原文。
            "path": getattr(route, "path", "unmatched"),
            "status_code": response.status_code,
            "error_code": getattr(request.state, "error_code", None),
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        }, ensure_ascii=False))
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI 默认的校验详情可能带有原始输入；本课只返回固定提示。
        return error_response(request, 422, "invalid_request", "请求参数不符合定义，请检查字段、类型和长度。")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        if isinstance(error.detail, dict):
            code, message = error.detail["code"], error.detail["message"]
        else:
            code, message = {
                404: ("not_found", "未找到该页面或接口。"),
                405: ("method_not_allowed", "该接口不支持此请求方法。"),
            }.get(error.status_code, ("request_failed", "请求未能完成。"))
        return error_response(request, error.status_code, code, message)

    @app.exception_handler(StoreError)
    async def storage_error(request: Request, error: StoreError):
        return error_response(request, 503, "storage_unavailable", "本地存储暂时不可用，请保留原草稿编号后重试。")

    def find_draft(store: TicketStore, draft_id: str) -> dict:
        try:
            return store.get_draft(draft_id)
        except ValueError:
            raise HTTPException(404, detail={"code": "not_found", "message": "未找到该草稿。"}) from None

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static/day06.html", media_type="text/html")

    @app.get("/health")
    def health() -> dict:
        """表示服务已启动，不执行真实模型请求。"""
        return {"status": "ok"}

    @app.post("/api/drafts", response_model=DraftRecord, status_code=201)
    def save_structured_draft(draft: TicketDraft, request: Request):
        """离线保存六字段草稿，不创建工单。"""
        return request.app.state.store.save_draft(draft)

    @app.post("/api/drafts/prepare", response_model=PrepareReply)
    def prepare_draft(body: PrepareRequest, request: Request):
        """同步函数由框架在线程池运行，模型调用不阻塞整个服务。"""
        try:
            proposal = request.app.state.proposer(body.question)
        except (ValidationError, ValueError, RuntimeError):
            raise HTTPException(502, detail={
                "code": "model_failed", "message": "模型整理失败，未保存新草稿。请检查本地模型配置或稍后重试。",
            }) from None
        if proposal.draft is None:
            return {"status": "needs_information", "draft": None,
                    "missing_fields": proposal.missing_fields, "follow_up": proposal.follow_up}
        saved = request.app.state.store.save_draft(proposal.draft)
        return {"status": "ready", "draft": saved, "missing_fields": [], "follow_up": ""}

    @app.get("/api/drafts/{draft_id}", response_model=DraftRecord)
    def read_draft(draft_id: DraftId, request: Request):
        return find_draft(request.app.state.store, draft_id)

    @app.post("/api/drafts/{draft_id}/confirm", response_model=ConfirmReply)
    def confirm_draft(draft_id: DraftId, body: ConfirmRequest, request: Request, response: Response):
        """服务端同样验证确认短语；页面按钮置灰不能替代这项检查。"""
        store = request.app.state.store
        record = find_draft(store, draft_id)
        if body.confirmation_text != f"确认创建 {record['draft_id']}":
            raise HTTPException(400, detail={"code": "confirmation_required", "message": "确认短语与当前草稿编号不匹配，未创建工单。"})
        result = store.confirm_draft(draft_id, confirmed=True)
        response.status_code = 201 if result["created"] else 200
        return result

    @app.get("/api/tickets/{ticket_id}", response_model=TicketRecord)
    def read_ticket(ticket_id: TicketId, request: Request):
        try:
            return request.app.state.store.get_ticket(ticket_id)
        except ValueError:
            raise HTTPException(404, detail={"code": "not_found", "message": "未找到该本地工单。"}) from None

    return app


# 导入模块不打开数据库；服务启动的 lifespan 才初始化资源。
app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址；容器内需用 0.0.0.0 才能从宿主机访问")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须为 1–65535。")
    # 监听 0.0.0.0 时，终端仍提示本机可访问的地址。
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    print(f"本地报修工作台：http://{display_host}:{args.port}/")
    print(f"接口文档：http://{display_host}:{args.port}/docs")
    print(f"日志：{args.log}")
    uvicorn.run(create_app(args.db, args.log), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
