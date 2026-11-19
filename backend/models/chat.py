"""OpenAI-compatible chat. api is a hosted service, local is a process on this machine."""
import base64
import json
import urllib.error
import urllib.request

from models.base import AgentModel, ModelCancelled
from models.decision import normalize_decision
from models.registry import registry
from models.turn import Turn


class ChatModel(AgentModel):
    def complete(self, turn: Turn) -> dict:
        if self._cancel.is_set():
            raise ModelCancelled()
        self._require("base_url")
        self._require("model")
        print(f"正在请求模型 {self.name}")
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(self._body(turn), ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        timeout = float(self.options.get("timeout_s") or 90)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"模型请求失败: HTTP {error.code}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"模型连不上: {error.reason}") from None
        if self._cancel.is_set():
            raise ModelCancelled()
        return normalize_decision(self._message_json(body))

    def _require(self, key: str):
        if not str(self.options.get(key) or "").strip():
            raise RuntimeError(f"模型 {self.name} 缺少 {key}")

    def _endpoint(self) -> str:
        base = str(self.options["base_url"]).rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = str(self.options.get("api_key") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _body(self, turn: Turn) -> dict:
        messages = []
        instructions = str(self.options.get("instructions") or "").strip()
        if instructions:
            messages.append({"role": "system", "content": instructions})
        image = base64.b64encode(turn.image_png).decode("ascii")
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": json.dumps(turn.payload(), ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
            ],
        })
        return {
            "model": self.options["model"],
            "messages": messages,
            "temperature": self.options.get("temperature", 0),
        }

    def _message_json(self, body: str) -> dict:
        try:
            payload = json.loads(body)
            text = payload["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise RuntimeError("模型响应里没有内容") from None
        text = str(text).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError("模型没有返回合法 JSON") from None


@registry.register("api")
class ApiModel(ChatModel):
    def __init__(self, options: dict):
        super().__init__(options)
        if not str(options.get("api_key") or "").strip():
            raise RuntimeError(f"模型 {self.name} 缺少 api_key")


@registry.register("local")
class LocalModel(ChatModel):
    def prepare(self, turn: Turn) -> Turn:
        """Local-only step. Put retrieval such as RAG on turn.extra, then return turn."""
        return turn
