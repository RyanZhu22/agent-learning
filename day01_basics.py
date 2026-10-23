"""离线练习：字典、函数、类型注解和异常；不会调用模型。"""

import json


# 类似 Java 的 Map，或 Go 的 map。这里都是模拟数据。
TICKETS: dict[str, dict[str, str]] = {
    "T1001": {"title": "无法登录", "status": "处理中"},
    "T1002": {"title": "导出失败", "status": "已解决"},
    "T1003": {"title": "支付失败", "status": "待处理"},
    "T1004": {"title": "邮件通知失败", "status": "已解决"},
}


def get_ticket(ticket_id: str) -> dict[str, str]:
    """根据工单号查询本地模拟数据。第 2 天会把它注册为工具。"""
    normalized_id = ticket_id.strip().upper()
    if normalized_id not in TICKETS:
        raise ValueError(f"找不到工单 {normalized_id}")
    return {"ticket_id": normalized_id, **TICKETS[normalized_id]}


def main() -> None:
    print("这是离线 Python 练习，使用本地模拟数据。")
    for ticket_id in ["T1001", " t1002 ", "T1003", "T9999"]:
        try:
            result = get_ticket(ticket_id)
            print(json.dumps(result, ensure_ascii=False))
        except ValueError as error:
            print(f"查询失败：{error}")
    print("\n练习：添加 T1003，并把它加入上面的查询列表。")


if __name__ == "__main__":
    main()
