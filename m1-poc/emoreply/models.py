"""M1 概念验证·数据模型。

字段对齐 SRS FR-1.2 的通知载荷字段（发送方/会话/内容/时间/@提及/引用），
只保留平台无关部分——Android 侧 NotificationListenerService 的解析产物即此结构，
因此本模块可在无 Android 环境下用测试集验证，无需改动。
"""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class Notification:
    """一次系统通知（捕获层输出，只读，不触碰聊天数据库）。"""

    source: str
    timestamp: float
    body: Optional[str] = None
    sender: Optional[str] = None
    chat: Optional[str] = None
    is_group: bool = False
    mentions_me: bool = False
    quotes_me: bool = False
    from_self: bool = False
    is_system: bool = False
    title: Optional[str] = None


Action = Literal["generate", "fallback", "drop"]


@dataclass
class Decision:
    """预处理链路对单条通知的判定结果。"""

    action: Action
    reason: str
    notification: Notification
    fallback_hint: Optional[str] = None


@dataclass
class Candidate:
    """一条待注入输入框的候选回复。"""

    text: str
    style: str
    rationale: Optional[str] = None