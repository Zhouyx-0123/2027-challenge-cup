# -*- coding: utf-8 -*-
"""DeepSeek 文本披露附注生成；只消费展示快照，不参与碳核算。"""
from __future__ import annotations

import json
from typing import Any, Dict

import requests

from src.invoice import API_TIMEOUT, DEEPSEEK_CHAT_URL, DEEPSEEK_TEXT_MODEL


def generate_disclosure_note(snapshot: Dict[str, Any], api_key: str) -> str:
    if not api_key:
        raise ValueError("未配置 DeepSeek API Key")
    payload = {
        "model": DEEPSEEK_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": (
                "你是企业碳披露助手。根据给定数字生成一段120字以内的中文披露附注。"
                "不得改变数字，不得虚构政策、标准或已完成的审计；估算值需提示人工复核。"
                "只输出附注正文。")},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
        ],
        "max_tokens": 500,
        "stream": False,
    }
    response = requests.post(
        DEEPSEEK_CHAT_URL,
        headers={"Authorization": "Bearer %s" % api_key,
                 "Content-Type": "application/json"},
        json=payload, timeout=API_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else "")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        error = data.get("error") or {}
        raise RuntimeError(str(error.get("message") or "DeepSeek 返回空附注"))
    return str(content).strip()
