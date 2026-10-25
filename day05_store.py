"""第 5 天：用 SQLite 保存草稿，并在明确确认后创建本地工单。

每次保存都会创建一个独立草稿；相同内容也可能代表两次报修意图。
幂等边界是 draft_id：同一个草稿重复确认，只能得到同一张工单。
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from day04_draft import TicketDraft


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "artifacts" / "day05_tickets.sqlite3"


class StoreError(Exception):
    """本地持久化失败；向调用方提供不含底层路径或 SQL 的错误。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return prefix + uuid4().hex[:12]


class TicketStore:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StoreError("无法初始化本地工单存储。") from None

        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted')),
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ticket_id TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(draft_id),
                    status TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """每次操作使用一个连接；异常回滚，结束后关闭。"""
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        except (sqlite3.Error, OSError):
            raise StoreError("本地工单存储操作失败，请稍后重试。") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _draft_result(row: sqlite3.Row) -> dict:
        return {
            "draft_id": row["draft_id"],
            "status": row["status"],
            "draft": json.loads(row["draft_json"]),
            "created_at": row["created_at"],
            "ticket_id": row["ticket_id"],
        }

    @staticmethod
    def _ticket_result(row: sqlite3.Row) -> dict:
        return {
            "ticket_id": row["ticket_id"],
            "draft_id": row["draft_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            **json.loads(row["draft_json"]),
        }

    def save_draft(self, draft: TicketDraft) -> dict:
        """只保存待确认草稿，不创建工单，也不按内容合并草稿。"""
        draft_id = _new_id("D")
        draft_json = json.dumps(draft.model_dump(), ensure_ascii=False)
        created_at = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO drafts
                   (draft_id, status, draft_json, created_at, ticket_id)
                   VALUES (?, 'pending', ?, ?, NULL)""",
                (draft_id, draft_json, created_at),
            )
            row = connection.execute(
                "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            return self._draft_result(row)

    def get_draft(self, draft_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                raise ValueError("未找到该草稿。")
            return self._draft_result(row)

    def confirm_draft(self, draft_id: str, *, confirmed: bool = False) -> dict:
        """调用方取得用户明确确认后才传 confirmed=True。

        读取草稿、插入工单、更新草稿在同一个事务中完成。
        BEGIN IMMEDIATE 串行化并发提交，UNIQUE(draft_id) 再保证不会重复建单。
        """
        if confirmed is not True:
            raise ValueError("需要用户明确确认后才能创建工单。")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = connection.execute(
                "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            if draft is None:
                raise ValueError("未找到该草稿。")

            # 重复确认直接返回已经创建的工单。
            ticket = connection.execute(
                "SELECT * FROM tickets WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            if ticket is not None:
                return {"created": False, "ticket": self._ticket_result(ticket)}

            ticket_id = _new_id("N")
            connection.execute(
                """INSERT INTO tickets
                   (ticket_id, draft_id, status, draft_json, created_at)
                   VALUES (?, ?, '待处理', ?, ?)""",
                (ticket_id, draft_id, draft["draft_json"], _now()),
            )
            connection.execute(
                "UPDATE drafts SET status = 'submitted', ticket_id = ? WHERE draft_id = ?",
                (ticket_id, draft_id),
            )
            ticket = connection.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            return {"created": True, "ticket": self._ticket_result(ticket)}

    def get_ticket(self, ticket_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise ValueError("未找到该本地工单。")
            return self._ticket_result(row)
