"""The Unicode map input retains current-control binding on the protected RPC."""
import base64
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from device.amap import AmapDevice, PACKAGE
from domain import WorkflowError


@pytest.mark.parametrize("result,reason", [(1, None), (0, "bridge_timeout"), (0, "field_match_0")])
def test_map_input_uses_bound_provider_and_requires_readback(monkeypatch, tmp_path, result, reason):
    from device import mobilerun
    old = "我的位置"
    target = ET.Element("node", {"package": PACKAGE, "class": "android.widget.EditText",
        "text": old, "resource-id": "", "bounds": "[251,194][785,292]", "enabled": "true"})
    shot = {"nodes": [target], "epoch": 0, "evidence": {"observation_id": "fresh-map-frame"}}
    base = SimpleNamespace(context_epoch=0, shell=Mock(), tap=Mock())
    adapter = AmapDevice(base, Mock())
    adapter.folder = tmp_path; adapter.query_id = "current-query"
    adapter._observe = Mock(return_value=shot)
    adapter._wait = Mock()
    call = Mock(return_value={"result": result, "reason": reason, "nonce": "current-nonce", "data": None})
    monkeypatch.setattr(mobilerun, "call_transit_bridge", call)
    text = "体育西路(地铁站)"
    if result:
        adapter._enter(shot, target, text, "origin", lambda: None)
        adapter._wait.assert_called_once()
        predicate = adapter._wait.call_args.args[0]
        assert not predicate(shot)
        target.set("text", text)
        assert predicate(shot) is target
    else:
        with pytest.raises(WorkflowError, match="中文输入"):
            adapter._enter(shot, target, text, "origin", lambda: None)
        adapter._wait.assert_not_called()
        assert reason in (tmp_path / "input-rejection.txt").read_text()
    operation, extras = call.call_args.args
    assert operation == "set_transit_field"
    assert extras["field"] == "origin" and extras["bounds"] == "[251,194][785,292]"
    assert extras["resource_id"] == ""
    assert base64.urlsafe_b64decode(extras["expected_text"]).decode() == old
    assert base64.urlsafe_b64decode(extras["text"]).decode() == text
    base.shell.assert_not_called(); base.tap.assert_not_called()
