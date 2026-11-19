"""DeepSeek vision model. LangGraph asks for one command and checks it against the contract."""
import json

from langchain_openai import ChatOpenAI

from models.base import AgentModel, ModelCancelled
from models.deepseek.graph import build_graph, initial_state
from models.deepseek.summary import prepare_file, refresh
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
        prepare_file()

    def complete(self, turn: Turn) -> dict:
        if not self.api_key:
            raise RuntimeError("没有配置 DEEPSEEK_API_KEY")
        if not self.model_name:
            raise RuntimeError("没有配置 DeepSeek 的模型名")
        if self._graph is None:
            self._llm = ChatOpenAI(
                model=self.model_name,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=0,
                timeout=90,
                max_retries=1,
                max_tokens=800,
            )
            self._graph = build_graph(self._llm, self._cancel)
        try:
            summary = refresh(self._llm, turn, self._cancel)
            print(f"\nDeepSeek 正在决定第 {turn.index} 步")
            result = self._graph.invoke(initial_state(turn, summary))
        except ModelCancelled:
            raise
        except Exception as error:
            raise RuntimeError(self._safe(error)) from None
        decision = result.get("decision")
        if not decision:
            raise RuntimeError(f"DeepSeek 没有给出合法指令: {result.get('error') or '空'}")
        print(f"DeepSeek 输出: {json.dumps(decision, ensure_ascii=False)}")
        return decision

    def _safe(self, error: Exception) -> str:
        text = str(error)
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return text or error.__class__.__name__
