"""Finite, non-executing reader for Open-AutoGLM's public action protocol.

Compatibility reference (read 2026-11-14):
https://github.com/zai-org/Open-AutoGLM/blob/main/phone_agent/config/prompts_zh.py
https://github.com/zai-org/Open-AutoGLM/blob/main/phone_agent/actions/handler.py
This is an independent implementation; no upstream agent or execution code is
imported. Screen coordinates are integers in [0, 999], scaled by value / 1000.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any

from domain import WorkflowError


PROTOCOL_REASON_CODES = frozenset({
    "invalid_envelope", "invalid_syntax", "invalid_call", "invalid_arguments",
    "unsupported_action", "invalid_coordinates", "invalid_text",
})
SYNTAX_DIAGNOSTICS = frozenset({"unclosed_delimiter", "unmatched_delimiter", "unterminated_string",
                                "invalid_character", "argument_order", "indentation", "other"})
ACTION_DIAGNOSTICS = frozenset({"Launch", "Tap", "Type", "Swipe", "Back", "Wait", "Take_over", "Interact", "Finish", "unknown"})
STRING_END_DIAGNOSTICS = frozenset({"linebreak", "end_of_response", "unknown"})


class ProtocolError(WorkflowError):
    """Invalid model output; messages intentionally exclude its raw contents."""

    def __init__(self, reason_code="invalid_arguments"):
        self.reason_code = (reason_code if type(reason_code) is str and reason_code in PROTOCOL_REASON_CODES
                            else "invalid_arguments")
        super().__init__("AutoGLM 动作格式无效或不在本次允许范围内，未执行手机操作")


@dataclass(frozen=True)
class Action:
    kind: str
    params: dict[str, Any]


def _invalid(reason_code):
    return ProtocolError(reason_code)


def _text(value, limit=500, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not value.strip() and not empty)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise _invalid("invalid_text")
    return value


def _point(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(c) is not int or not 0 <= c < 1000 for c in value)):
        raise _invalid("invalid_coordinates")
    return value


def _literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, float}:
        return node.value
    if isinstance(node, ast.List) and len(node.elts) == 2:
        return [_literal(value) for value in node.elts]
    raise _invalid("invalid_arguments")


def _expression(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 64000:
        raise _invalid("invalid_envelope")
    content = raw.strip()
    if content.startswith("<think>"):
        end = content.find("</think>")
        if end < 0:
            raise _invalid("invalid_envelope")
        content = content[end + len("</think>"):].strip()
    if content.startswith("<answer>"):
        content = content[len("<answer>"):].strip()
        # Upstream examples permit an unclosed answer tag. The action itself
        # must still be one complete AST expression; truncated API completions
        # are separately rejected by the client using finish_reason.
        if content.endswith("</answer>"):
            content = content[:-len("</answer>")].strip()
    elif not re.match(r"^(?:do|finish)\s*\(", content):
        # The current upstream client also supports untagged reasoning followed
        # by an action. Accept only a single line-start marker in that form.
        matches = list(re.finditer(r"(?m)^\s*(?:do|finish)\s*\(", content))
        if len(matches) != 1:
            raise _invalid("invalid_envelope")
        content = content[matches[0].start():].strip()
    if len(content) > 8192:
        raise _invalid("invalid_envelope")
    return content


def _syntax_diagnostics(content, error):
    # Classify only into hardcoded values. Never persist error.text, a model
    # argument, or any part of the model's private reasoning.
    msg = error.msg
    if "was never closed" in msg: category = "unclosed_delimiter"
    elif msg.startswith("unmatched "): category = "unmatched_delimiter"
    elif "unterminated string" in msg or "unterminated triple-quoted string" in msg: category = "unterminated_string"
    elif msg.startswith("invalid character"): category = "invalid_character"
    elif "argument follows" in msg: category = "argument_order"
    elif isinstance(error, IndentationError): category = "indentation"
    else: category = "other"
    if re.match(r'^finish\s*\(', content): action = "Finish"
    else:
        match = re.match(r'''^do\s*\(\s*action\s*=\s*(["'])(Launch|Tap|Type|Swipe|Back|Wait|Take_over|Interact)\1''', content)
        action = match.group(2) if match else "unknown"
    return category, action


def parse_action(raw: str) -> Action:
    content = _expression(raw)
    try:
        parsed = ast.parse(content, mode="eval")
        call = parsed.body
        if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                or call.func.id not in {"do", "finish"} or call.args
                or len(list(ast.walk(parsed))) > 64
                or ast.get_source_segment(content, call) != content):
            raise _invalid("invalid_call")
        values = {}
        for item in call.keywords:
            if item.arg is None or item.arg in values:
                raise _invalid("invalid_arguments")
            values[item.arg] = _literal(item.value)
    except SyntaxError as error:
        invalid = _invalid("invalid_syntax")
        invalid.syntax_category, invalid.syntax_action = _syntax_diagnostics(content, error)
        if invalid.syntax_category == "unterminated_string":
            lines = content.splitlines(keepends=True)
            if type(error.lineno) is int and 1 <= error.lineno <= len(lines):
                line = lines[error.lineno - 1]
                invalid.syntax_string_end = "linebreak" if line.endswith(("\n", "\r")) else "end_of_response"
            else:
                invalid.syntax_string_end = "unknown"
        raise invalid from None
    except (ValueError, TypeError, RecursionError, MemoryError):
        raise _invalid("invalid_syntax") from None
    if call.func.id == "finish":
        if set(values) != {"message"}:
            raise _invalid("invalid_arguments")
        return Action("Finish", {"message": _text(values["message"])})
    kind = values.pop("action", None)
    if not isinstance(kind, str):
        raise _invalid("invalid_arguments")
    if kind == "Launch" and set(values) == {"app"}:
        if values["app"] not in {"高德地图", "com.autonavi.minimap"}:
            raise _invalid("unsupported_action")
        return Action(kind, values)
    if kind == "Tap" and {"element"} <= set(values) <= {"element", "message"}:
        _point(values["element"])
        if "message" in values:
            _text(values["message"])
        return Action(kind, values)
    if kind == "Type" and set(values) == {"text"}:
        return Action(kind, {"text": _text(values["text"])})
    if kind == "Swipe" and set(values) == {"start", "end"}:
        try:
            _point(values["start"]); _point(values["end"])
        except ProtocolError as error:
            if error.reason_code == "invalid_coordinates":
                error.action_hint = "Swipe"
            raise
        if values["start"] == values["end"]:
            raise _invalid("invalid_coordinates")
        return Action(kind, values)
    if kind == "Back" and not values:
        return Action(kind, {})
    if kind == "Wait" and set(values) == {"duration"}:
        duration = values["duration"]
        if isinstance(duration, str):
            match = re.fullmatch(r"(\d+(?:\.\d+)?) seconds?", duration)
            if not match:
                raise _invalid("invalid_arguments")
            duration = float(match.group(1))
        if type(duration) not in {int, float} or not math.isfinite(duration) or not 0 < duration <= 5:
            raise _invalid("invalid_arguments")
        return Action(kind, {"duration": float(duration)})
    if kind in {"Take_over", "Interact"}:
        if set(values) - {"message"} or (kind == "Take_over" and "message" not in values):
            raise _invalid("invalid_arguments")
        return Action("Help", {"message": _text(values.get("message", "需要用户核对或选择地点"))})
    raise _invalid("invalid_arguments" if kind in {"Launch", "Tap", "Type", "Swipe", "Back", "Wait", "Take_over", "Interact"}
                   else "unsupported_action")
