"""Independent AutoGLM settings. Does not load files or mutate other models."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from domain import WorkflowError


def _number(env, name, default, low, high, cast):
    try:
        value = cast(env.get(name, str(default)))
    except (ValueError, TypeError, OverflowError):
        raise WorkflowError(f"AutoGLM 配置 {name} 无效") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise WorkflowError(f"AutoGLM 配置 {name} 超出允许范围")
    return value


@dataclass(frozen=True)
class Config:
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    model: str = "autoglm-phone"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 35
    max_steps: int = 30
    total_seconds: float = 240
    history_limit: int = 8

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        value = cls(
            base_url=env.get("SILVERGUIDE_AUTOGLM_BASE_URL", cls.base_url).strip(),
            model=env.get("SILVERGUIDE_AUTOGLM_MODEL", cls.model).strip(),
            api_key=env.get("SILVERGUIDE_AUTOGLM_API_KEY", "").strip(),
            timeout_seconds=_number(env, "SILVERGUIDE_AUTOGLM_TIMEOUT", 35, 1, 70, float),
            max_steps=_number(env, "SILVERGUIDE_AUTOGLM_MAX_STEPS", 30, 1, 60, int),
            total_seconds=_number(env, "SILVERGUIDE_AUTOGLM_TOTAL_SECONDS", 240, 5, 600, float),
            history_limit=_number(env, "SILVERGUIDE_AUTOGLM_HISTORY_LIMIT", 8, 1, 12, int),
        )
        value.validate(require_key=False)
        return value

    def validate(self, *, require_key=True):
        try:
            url = urlsplit(self.base_url)
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            valid = (url.scheme == "https" or url.scheme == "http" and local) and url.hostname
            valid = valid and not url.username and not url.password and not url.query and not url.fragment
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise WorkflowError("AutoGLM Base URL 必须是无内嵌凭据的 HTTPS 地址；仅本机兼容服务允许 HTTP")
        if not self.model or len(self.model) > 160 or any(ord(c) < 33 or ord(c) == 127 for c in self.model):
            raise WorkflowError("AutoGLM 模型名称配置无效")
        for name, value, low, high, integer in [
            ("timeout_seconds", self.timeout_seconds, 1, 70, False),
            ("max_steps", self.max_steps, 1, 60, True),
            ("total_seconds", self.total_seconds, 5, 600, False),
            ("history_limit", self.history_limit, 1, 12, True),
        ]:
            if (type(value) not in {int, float} or integer and type(value) is not int
                    or not math.isfinite(value) or not low <= value <= high):
                raise WorkflowError(f"AutoGLM 配置 {name} 超出允许范围")
        if not isinstance(self.api_key, str) or any(ord(c) < 32 or ord(c) == 127 for c in self.api_key):
            raise WorkflowError("AutoGLM API Key 格式无效")
        if require_key and (not self.api_key or self.api_key.lower() in {"empty", "your-api-key", "your-bigmodel-api-key"}):
            raise WorkflowError("AutoGLM 模式缺少 SILVERGUIDE_AUTOGLM_API_KEY，请在本机 .env 配置后重新查询")
        return self


AutoGLMConfig = Config
