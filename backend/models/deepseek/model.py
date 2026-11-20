"""DeepSeek vision model. LangGraph asks for one command and checks it against the contract."""
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from models.base import AgentModel, ModelCancelled
from models.deepseek.graph import _text, build_graph, initial_state
from models.deepseek.retrieve import guard, retrieve
from models.registry import registry
from models.turn import Turn


@registry.register("deepseek")
class DeepSeek(AgentModel):
    def __init__(self, options: dict):
        super().__init__(options)
        self.model_name = str(options.get("model") or "").strip()
        self.base_url = str(options.get("base_url") or "https://api.deepseek.com").strip()
        self.api_key = str(options.get("api_key") or "").strip()
        self._llm = None
        self._graph = None

    def complete(self, turn: Turn) -> dict:
        if not self.api_key:
            raise RuntimeError("没有配置 DEEPSEEK_API_KEY")
        if not self.model_name:
            raise RuntimeError("没有配置 DeepSeek 的模型名")
        if self._graph is None:
            self._graph = build_graph(self._client(), self._cancel)
        try:
            notes = retrieve(turn)
            print(f"\nDeepSeek 正在决定第 {turn.index} 步")
            result = self._graph.invoke(initial_state(turn, notes, guard(turn)))
        except ModelCancelled:
            raise
        except Exception as error:
            raise RuntimeError(self._safe(error)) from None
        decision = result.get("decision")
        if not decision:
            raise RuntimeError(f"DeepSeek 没有给出合法指令: {result.get('error') or '空'}")
        decision = _drop_repeat(decision, turn)
        print(f"DeepSeek 输出: {json.dumps(decision, ensure_ascii=False)}")
        return decision

    def summarize(self, notes: str) -> str:
        text = notes.strip()
        if not text:
            return ""
        if not self.api_key or not self.model_name:
            return ""
        print("\nDeepSeek 正在写这轮回复")
        try:
            message = self._client().invoke([
                SystemMessage(content=_REPLY),
                HumanMessage(content=text),
            ])
        except Exception as error:
            raise RuntimeError(self._safe(error)) from None
        reply = _text(message.content).strip().strip("`").strip()
        print(f"回复: {reply}")
        return reply

    def _client(self):
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.model_name,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=0,
                timeout=90,
                max_retries=1,
                max_tokens=800,
            )
        return self._llm

    def _safe(self, error: Exception) -> str:
        text = str(error)
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return text or error.__class__.__name__


_REPLY = """根据下面的记录，用中文写一段回复，告诉用户这轮手机操作干了什么。
像当面说完一件事：先说结果，再补上关键过程。两三句到一小段。不要列步骤编号，不要写坐标，不要编造记录里没有的事。
没做完就说明停在哪里。只输出这段回复。"""


def _drop_repeat(decision: dict, turn: Turn) -> dict:
    text = str(decision.get("progress") or "").strip()
    if not text:
        return decision
    previous = [str(item.get("progress") or "").strip() for item in turn.commands]
    previous = [item for item in previous if item]
    if any(_same_progress(text, old) for old in previous):
        print(f"进度与已有记录重复，不再写入: {text}")
        decision = dict(decision)
        decision["progress"] = None
    return decision


def _same_progress(text: str, old: str) -> bool:
    if text == old or text in old or old in text:
        return True
    left = set(text)
    right = set(old)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= 0.72
