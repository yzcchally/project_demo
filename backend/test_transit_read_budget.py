"""Protected native read budgets across transport/device layers, no phone/API."""
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from device.huawei import HuaweiDevice, ScreenContextChanged
from device.mobilerun import MobilerunTransport
from device.amap import AmapDevice
from domain import Cancelled, WorkflowError


XML = '<hierarchy nonce="offline-read"><node package="com.autonavi.minimap" text="公共交通" bounds="[0,100][1200,300]"/></hierarchy>'


def protected_device(tmp_path, monkeypatch, responses):
    transport = MobilerunTransport(); transport.prefer_transit_bridge = True
    transport._device = Mock(); transport._fresh_file_xml = Mock()
    transport._protected_transit_xml = Mock(side_effect=responses)
    monkeypatch.setattr("time.sleep", Mock())
    return HuaweiDevice(tmp_path, transport=transport), transport


def test_outer_device_does_not_multiply_two_provider_attempts(tmp_path, monkeypatch):
    device, transport = protected_device(tmp_path, monkeypatch, [None, None])
    with pytest.raises(WorkflowError, match="受保护界面读取失败"):
        device.observe()
    assert transport._protected_transit_xml.call_count == 2
    transport._device.ui.assert_not_called(); transport._fresh_file_xml.assert_not_called()


def test_second_fresh_protected_read_can_recover_and_only_that_xml_is_saved(tmp_path, monkeypatch):
    device, transport = protected_device(tmp_path, monkeypatch, [None, XML])
    device.task_id = "offline-budget-recovery"
    assert device.observe(evidence=True) == XML
    assert transport._protected_transit_xml.call_count == 2
    saved = list((tmp_path / device.task_id).glob("screen-*.xml"))
    assert len(saved) == 1 and ET.parse(saved[0]).getroot().get("nonce") == "offline-read"
    transport._device.ui.assert_not_called(); transport._fresh_file_xml.assert_not_called()


@pytest.mark.parametrize("error", [Cancelled("用户取消"), WorkflowError("query_id 已变化")])
def test_checkpoint_stops_before_another_native_attempt_after_failure(tmp_path, monkeypatch, error):
    device, transport = protected_device(tmp_path, monkeypatch, [])
    interrupted = [False]
    def read():
        interrupted[0] = True
        return None
    transport._protected_transit_xml.side_effect = read
    def checkpoint():
        if interrupted[0]: raise error
    with pytest.raises(type(error), match=str(error)):
        device.observe(checkpoint)
    assert transport._protected_transit_xml.call_count == 1
    transport._device.ui.assert_not_called(); transport._fresh_file_xml.assert_not_called()


def test_resume_during_protected_read_discards_xml_without_spending_another_budget_or_saving(tmp_path, monkeypatch):
    device, transport = protected_device(tmp_path, monkeypatch, [])
    device.task_id = "offline-resume-read"
    def read():
        device.context_epoch += 1
        return XML
    transport._protected_transit_xml.side_effect = read
    with pytest.raises(ScreenContextChanged):
        device.observe(evidence=True)
    assert transport._protected_transit_xml.call_count == 1
    assert not list(tmp_path.rglob("screen-*.xml"))


def test_invalid_protected_success_is_not_retried_or_replaced_by_another_serializer(tmp_path, monkeypatch):
    device, transport = protected_device(tmp_path, monkeypatch, [RuntimeError("过期或包名不符")])
    with pytest.raises(WorkflowError, match="受保护界面读取失败"):
        device.observe()
    assert transport._protected_transit_xml.call_count == 1
    transport._device.ui.assert_not_called(); transport._fresh_file_xml.assert_not_called()


def test_non_transit_legacy_no_argument_transport_keeps_its_three_attempt_contract(tmp_path):
    transport = SimpleNamespace(xml=Mock(side_effect=[RuntimeError("transient"), RuntimeError("transient"), XML]))
    device = HuaweiDevice(tmp_path, transport=transport)
    assert device.observe() == XML
    assert transport.xml.call_count == 3 and all(not call.kwargs for call in transport.xml.call_args_list)


@pytest.mark.parametrize('error',[Cancelled('用户已停止本轮查询'),ScreenContextChanged('恢复已废弃旧界面')])
def test_adapter_preserves_cancellation_identity_after_native_timeout_reason(tmp_path,monkeypatch,error):
    """A stale bridge reason must not relabel a stop as a retryable read error."""
    device,transport=protected_device(tmp_path,monkeypatch,[])
    interrupted=[False]
    def timeout_read():
        transport.last_bridge_reason='bridge_timeout'
        interrupted[0]=True
        return None
    transport._protected_transit_xml.side_effect=timeout_read
    def checkpoint():
        if interrupted[0]:raise error
    adapter=AmapDevice(device,Mock());adapter.folder=tmp_path;adapter.query_id='offline-cancelled-query'
    with pytest.raises(type(error)) as caught:
        adapter._observe(checkpoint)
    assert caught.value is error
    assert transport._protected_transit_xml.call_count==1
    assert adapter.observations==[] and not list(tmp_path.rglob('*.xml'))
    assert not (tmp_path/'observation-error.json').exists()
    transport._device.ui.assert_not_called();transport._fresh_file_xml.assert_not_called()
