"""Project one native Chat Completions result without changing model behavior."""
from __future__ import annotations

import re


def _text(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("推理输出包含无效文本编码") from exc
        return value
    if isinstance(value, list):
        return "".join(_text(part["text"]) for part in value if isinstance(part, dict)
                       and part.get("type") == "text" and isinstance(part.get("text"), str))
    return ""


def _metadata(value, maximum):
    if not isinstance(value, str) or not 0 < len(value) <= maximum \
            or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    return value


def inference_output(payload):
    """Answer, thinking, and termination remain distinct, including empty output."""
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list) \
            or not payload["choices"] or not isinstance(payload["choices"][0], dict):
        raise ValueError("推理返回缺少有效 choices，尚未通过输出验收")
    choice = payload["choices"][0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = _text(message.get("content")) or _text(choice.get("text"))
    reasoning = _text(message.get("reasoning_content")) or _text(message.get("reasoning"))
    finish = _metadata(choice.get("finish_reason"), 96)
    truncated = finish == "length"
    status = "reasoning_only" if not text.strip() and reasoning.strip() else "empty" if not text.strip() \
        else "truncated" if truncated else "complete" if finish in {None, "stop"} else "incomplete"
    result = {"text": text, "reasoning": reasoning, "finish_reason": finish,
              "truncated": truncated, "output_status": status}
    for source, target in (("id", "response_id"), ("model", "model")):
        value = _metadata(payload.get(source), 256)
        if value is not None:
            if source == "id" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:#-]{0,255}", value):
                continue
            result[target] = value
    return result
