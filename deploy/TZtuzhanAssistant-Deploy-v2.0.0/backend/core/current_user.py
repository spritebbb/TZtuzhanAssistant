# -*- coding: utf-8 -*-
"""当前会话用户身份：通过 contextvars 沿 async 调用链传递，供工具和子系统读取。

用法：
  from .current_user import current_user_id
  uid = current_user_id.get()  # 默认 'assistant-main'
"""
import contextvars

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default="assistant-main"
)