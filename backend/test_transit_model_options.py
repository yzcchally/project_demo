"""Extraction time limits and provider-specific options stay scoped to transit."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from agent.models import ModelGateway


class Reply(BaseModel):
    value: str


@pytest.mark.parametrize("stage,base_url,thinking", [
    ("transit_extraction", "https://api.deepseek.com", True),
    ("understand", "https://api.deepseek.com", False),
    ("transit_extraction", "https://other-provider.invalid/v1", False),
])
def test_extraction_options_preserve_other_stages_and_compatible_providers(monkeypatch, tmp_path, stage, base_url, thinking):
    import openai
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-placeholder")
    monkeypatch.setenv("SILVERGUIDE_MODEL_BASE_URL", base_url)
    completion = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"value":"observed"}'))], usage=None))
    constructor = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion))))
    monkeypatch.setattr(openai, "OpenAI", constructor)
    result = ModelGateway(tmp_path).structured(Reply, "Read facts", {}, stage)
    assert result.value == "observed"
    options = completion.call_args.kwargs
    assert ("extra_body" in options) == thinking
    if thinking:
        assert options["extra_body"] == {"thinking": {"type": "disabled"}}
    settings = constructor.call_args.kwargs
    assert settings["base_url"] == base_url
    if stage == "transit_extraction":
        assert settings["max_retries"] == 0 and settings["timeout"] <= 75
    else:
        assert settings["timeout"] == 35 and settings["max_retries"] == 1
