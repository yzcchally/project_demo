import base64
import json

import memory_store
from models import Human, build_turn, load_model
from models import config, context
from models.human import model as human_model


def test_human_model_validates_and_records_the_unified_turns_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(memory_store, "ROOT", tmp_path / "memory")
    monkeypatch.setattr(context, "BACKEND", tmp_path)
    monkeypatch.setattr(human_model, "BACKEND", tmp_path)
    monkeypatch.setattr(human_model, "WORK", tmp_path / "human-work")
    monkeypatch.setattr(config, "_load_dotenv", lambda path: None)
    answers = iter((
        '{"type": "tap", "x": -1, "y": 0}',
        '{"command": {"type": "tap", "x": 0, "y": 0}, "progress": "查看测试页面"}',
        '{"command": {"type": "end"}, "progress": "完成"}',
    ))
    monkeypatch.setattr(human_model, "read_line", lambda cancelled: next(answers))
    png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jMZkAAAAASUVORK5CYII="
    screen = {"image_png_base64": png_base64, "width": 1, "height": 1}

    model = load_model()
    assert isinstance(model, Human)
    assert model.name == model.kind == "human"
    task = "查看本地测试页面"
    memory_store.remember_prompt(task)
    memory_store.remember_note("仅使用测试图")

    first_turn = build_turn(task, screen)
    first_decision = model.decide(first_turn)
    assert first_decision == {
        "command": {"type": "tap", "x": 0, "y": 0},
        "progress": "查看测试页面",
    }
    assert "指令不合法" in capsys.readouterr().out

    second_turn = build_turn(task, screen)
    assert second_turn.index == 2
    (previous,) = second_turn.previous
    assert previous.image_png == base64.b64decode(png_base64)
    assert previous.text == first_decision["progress"]
    assert previous.info == {"width": 1, "height": 1, "command": first_decision["command"], "latest": False}
    assert second_turn.memory == {"urls": [], "notes": ["仅使用测试图"]}
    assert model.decide(second_turn) == {"command": {"type": "end"}, "progress": "完成"}

    recorded = json.loads((tmp_path / "memory" / "002" / "input.json").read_text(encoding="utf-8"))
    assert recorded["previous"] == [previous.record()]
    assert (tmp_path / "last_screen.png").read_bytes() == previous.image_png
