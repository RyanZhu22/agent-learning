"""报修草稿：只整理字段，不创建、保存或修改工单。"""

from pydantic import BaseModel, ConfigDict, Field


class TicketDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=100, description="根据用户描述概括的简短标题")
    description: str = Field(min_length=1, max_length=1000, description="用户说明的问题和受影响功能")
    reproduction_steps: str = Field(min_length=1, max_length=1000, description="用户提供的复现步骤")
    expected_result: str = Field(min_length=1, max_length=500, description="用户期望的结果")
    actual_result: str = Field(min_length=1, max_length=500, description="用户实际观察到的结果")
    occurred_at: str = Field(min_length=1, max_length=200, description="用户提供的发生时间，保留其日期、时刻和时区表述，不推算")


def prepare_ticket_draft(**fields: str) -> dict:
    """返回结构化草稿；不分配工单编号，也不写文件或数据库。"""
    draft = TicketDraft.model_validate(fields)
    return {"kind": "ticket_draft", "created": False, "draft": draft.model_dump()}
