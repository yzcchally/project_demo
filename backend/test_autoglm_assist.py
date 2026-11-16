import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from autoglm.assist import AutoGLMAssist
from autoglm.protocol import Action
from domain import WorkflowError


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_API_KEY', 'test-placeholder-value')
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_MAX_CALLS', '2')
    transport = SimpleNamespace(tap=Mock())
    base = SimpleNamespace(context_epoch=1, transport=transport)
    adapter = SimpleNamespace(task_id='t', query_id='q', request={'origin':'a'}, folder=tmp_path,
        total_seconds=50, started=time.monotonic(), base=base, _check=lambda check:check(),
        _action=Mock(side_effect=lambda check:check()), _wait=Mock())
    shots = [dict(epoch=1,observation_id=x,evidence={'observation_id':x},screenshot_base64='fake') for x in ['before','fresh']]
    grounded = SimpleNamespace(role='transit',point=(5,8),node={'bounds':'[0,0][10,10]'})
    monkeypatch.setattr('autoglm.assist.native_control_hints',lambda *a:[{'role':'transit'}])
    monkeypatch.setattr('autoglm.assist.resolve_action',lambda *a:grounded)
    monkeypatch.setattr('autoglm.assist.proposal_is_current',lambda *a,**k:True)
    client = SimpleNamespace(decide=Mock(return_value=SimpleNamespace(action=Action('Tap',{'element':[5,8]}),latency_ms=1)))
    help = AutoGLMAssist(adapter,client_factory=lambda *a,**k:client)
    help._observe=Mock(side_effect=shots)
    adapter._wait.return_value = ({'evidence':{'observation_id':'after'}},True)
    return help,adapter,client,grounded


def test_tap_dispatches_once_and_checks_postcondition(rig):
    helper,adapter,client,_=rig
    predicate = lambda shot: True
    assert helper.tap('transit',lambda:None,predicate)['evidence']['observation_id']=='after'
    adapter.base.transport.tap.assert_called_once_with(5,8)
    adapter._wait.assert_called_once()
    assert adapter._wait.call_args.args[0] is predicate
    assert '"status": "verified"' in (adapter.folder/'autoglm-assist.jsonl').read_text()


def test_unsupported_model_action_never_executes(rig):
    helper,adapter,client,_=rig
    client.decide.return_value.action=Action('Back',{})
    with pytest.raises(WorkflowError):helper.tap('transit',lambda:None,lambda s:True)
    adapter.base.transport.tap.assert_not_called()


def test_wrong_independent_role_never_executes(rig):
    helper,adapter,client,grounded=rig
    grounded.role='preference'
    with pytest.raises(WorkflowError):helper.tap('transit',lambda:None,lambda s:True)
    adapter.base.transport.tap.assert_not_called()


def test_query_changed_during_model_call_never_executes(rig):
    helper,adapter,client,_=rig
    def changed(*args):
        adapter.query_id='new'
        return SimpleNamespace(action=Action('Tap',{'element':[5,8]}),latency_ms=1)
    client.decide.side_effect=changed
    with pytest.raises(WorkflowError):helper.tap('transit',lambda:None,lambda s:True)
    adapter.base.transport.tap.assert_not_called()


def test_expired_frame_never_executes(rig,monkeypatch):
    helper,adapter,client,_=rig
    monkeypatch.setattr('autoglm.assist.proposal_is_current',lambda *a,**k:False)
    with pytest.raises(Exception):helper.tap('transit',lambda:None,lambda s:True)
    adapter.base.transport.tap.assert_not_called()


def test_failed_postcondition_does_not_claim_success(rig):
    helper,adapter,client,_=rig
    adapter._wait.side_effect=WorkflowError('missing')
    with pytest.raises(WorkflowError):helper.tap('transit',lambda:None,lambda s:True)
    adapter.base.transport.tap.assert_called_once()
    audit=(adapter.folder/'autoglm-assist.jsonl').read_text()
    assert '"status": "verified"' not in audit
    assert '"status": "executed"' in audit
    assert '"reason_code": "postcondition"' in audit


def test_slow_model_is_bounded_and_late_output_cannot_tap(rig):
    import threading
    helper,adapter,client,_=rig
    release=threading.Event()
    finished=threading.Event()
    def late(*args):
        try:
            release.wait(2)
            return SimpleNamespace(action=Action('Tap',{'element':[5,8]}),latency_ms=1)
        finally:
            finished.set()
    client.decide.side_effect=late
    adapter.started=time.monotonic()-48.8
    start=time.monotonic()
    try:
        with pytest.raises(WorkflowError, match='超时'):
            helper.tap('transit',lambda:None,lambda s:True)
        assert time.monotonic()-start<1.5
        adapter.base.transport.tap.assert_not_called()
    finally:
        release.set()
        assert finished.wait(1)
    adapter.base.transport.tap.assert_not_called()


def test_cancel_while_model_running_discards_late_response(rig):
    import threading
    helper,adapter,client,_=rig
    release=threading.Event(); started=threading.Event(); finished=threading.Event()
    def late(*args):
        started.set()
        try:
            release.wait(2)
            return SimpleNamespace(action=Action('Tap',{'element':[5,8]}),latency_ms=1)
        finally: finished.set()
    client.decide.side_effect=late
    def checkpoint():
        if started.is_set(): raise WorkflowError('cancelled')
    try:
        with pytest.raises(WorkflowError, match='cancelled'):
            helper.tap('transit',checkpoint,lambda s:True)
        adapter.base.transport.tap.assert_not_called()
    finally:
        release.set()
        assert finished.wait(1)
    adapter.base.transport.tap.assert_not_called()


def test_budget_survives_recreated_helper(rig):
    helper,adapter,client,_=rig
    helper.tap('transit',lambda:None,lambda s:True)
    next_helper=AutoGLMAssist(adapter, client_factory=lambda *a,**k:client)
    assert next_helper.calls == 1
    assert next_helper.max_calls == 2


def test_corrupted_budget_log_fails_closed(rig):
    _,adapter,client,_=rig
    (adapter.folder/'autoglm-assist.jsonl').write_text('not-json\n')
    with pytest.raises(WorkflowError,match='记录无效'):
        AutoGLMAssist(adapter,client_factory=lambda *a,**k:client)
    adapter.base.transport.tap.assert_not_called()


def test_no_independently_verified_candidate_does_not_call_model(rig, monkeypatch):
    helper, adapter, client, _ = rig
    monkeypatch.setattr('autoglm.assist.native_control_hints', lambda *args: [])
    with pytest.raises(WorkflowError, match='未调用'):
        helper.tap('transit', lambda: None, lambda shot: True)
    client.decide.assert_not_called()
    adapter.base.transport.tap.assert_not_called()
    assert helper.calls == 0
    assert 'no_verified_candidate' in (adapter.folder/'autoglm-assist.jsonl').read_text()


def _observed_tree_rig(tmp_path, monkeypatch, *, foreign=None):
    """Real helper capture path with real XML + PNG; rule view filters packages."""
    import struct
    import xml.etree.ElementTree as ET
    from device.amap import PACKAGE, ID
    from autoglm.executor import AutoGLMTransitDevice
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_API_KEY', 'test-placeholder-value')
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_MAX_CALLS', '2')
    root = ET.Element('hierarchy')
    def el(package, text, rect, **attrs):
        return ET.Element('node', {'package':package, 'class':'android.view.ViewGroup', 'text':text,
            'content-desc':'', 'bounds':rect, 'visible-to-user':'true', 'enabled':'true',
            'clickable':'true', **attrs})
    root.append(el(PACKAGE, '', '[50,50][1000,120]', **{'resource-id':ID+'route_edit_summary_start'}))
    root.append(el(PACKAGE, '', '[50,125][1000,190]', **{'resource-id':ID+'route_edit_summary_end'}))
    target=el(PACKAGE, '公共交通', '[400,200][700,320]')
    if foreign == 'ancestor':
        parent=el('com.foreign', '', '[0,0][1200,2400]')
        parent.append(target); root.append(parent)
    else:
        root.append(target)
        if foreign == 'overlay':
            root.append(el('com.foreign', 'cover', '[400,200][700,320]'))
    xml=ET.tostring(root, encoding='unicode')
    # Supply an authentic PNG structure as read from the adapter's capture path.
    png=(b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',1200,2400)+b'\x08\x02\x00\x00\x00'+b'\x00'*4)
    base=SimpleNamespace(context_epoch=1,transport=SimpleNamespace(tap=Mock()))
    adapter=SimpleNamespace(task_id='t',query_id='q',request={'origin':'a','destination':'b','preference':'recommended'},
        folder=tmp_path,total_seconds=50,started=time.monotonic(),base=base,_check=lambda check:check(),
        _action=Mock(side_effect=lambda check:check()),_wait=Mock(return_value=({'evidence':{'observation_id':'after'}},True)))
    def observe(check, page, screenshot=False):
        check(); assert screenshot
        (tmp_path/'frame.png').write_bytes(png)
        # Reproduce AmapDevice's filtered nodes exactly, while retaining raw XML.
        return {'xml':xml,'nodes':[n for n in root.iter('node') if n.get('package')==PACKAGE],
                'epoch':1,'evidence':{'observation_id':'frame','evidence_id':'frame'}}
    adapter._observe=observe
    adapter._route_cards=lambda shot: []
    from device.amap import AmapDevice
    adapter._assist_page_safe=lambda shot,check:AmapDevice._assist_page_safe(adapter,shot,check)
    monkeypatch.setattr(AutoGLMTransitDevice,'_foreground',lambda self:PACKAGE)
    monkeypatch.setattr(AutoGLMTransitDevice,'_geometry',lambda self:(1200,2400,0))
    client=SimpleNamespace(decide=Mock(return_value=SimpleNamespace(action=Action('Tap',{'element':[458,108]}),latency_ms=1)))
    return AutoGLMAssist(adapter,client_factory=lambda *args,**kwargs:client), adapter, client


def test_real_observation_grounding_and_device_action(tmp_path, monkeypatch):
    helper, adapter, client = _observed_tree_rig(tmp_path, monkeypatch)
    helper.tap('transit',lambda:None,lambda shot:True)
    client.decide.assert_called_once()
    adapter.base.transport.tap.assert_called_once()


@pytest.mark.parametrize('foreign', ['overlay','ancestor'])
def test_real_observation_foreign_overlap_or_ancestor_prevents_api(tmp_path, monkeypatch, foreign):
    helper, adapter, client = _observed_tree_rig(tmp_path, monkeypatch, foreign=foreign)
    original_guard=adapter._assist_page_safe
    def guard(shot,check):
        assert any(n.get('package')=='com.foreign' for n in shot['nodes'])
        return original_guard(shot,check)
    adapter._assist_page_safe=guard
    with pytest.raises(WorkflowError, match='覆盖'):
        helper.tap('transit',lambda:None,lambda shot:True)
    client.decide.assert_not_called()
    adapter.base.transport.tap.assert_not_called()


def test_precondition_checked_before_model_and_again_before_dispatch(rig):
    helper,adapter,client,_=rig
    checked=[]
    def precondition(shot):
        checked.append(shot['observation_id'])
        if len(checked)==2:
            raise WorkflowError('高德起终点发生变化')
    with pytest.raises(WorkflowError,match='起终点'):
        helper.tap('transit',lambda:None,lambda shot:True,precondition=precondition)
    assert checked==['before','fresh']
    client.decide.assert_called_once()
    adapter.base.transport.tap.assert_not_called()


def test_bad_initial_precondition_never_calls_model(rig):
    helper,adapter,client,_=rig
    def precondition(shot):raise WorkflowError('当前起终点不匹配')
    with pytest.raises(WorkflowError,match='起终点'):
        helper.tap('transit',lambda:None,lambda shot:True,precondition=precondition)
    client.decide.assert_not_called()
    adapter.base.transport.tap.assert_not_called()


def test_model_receives_current_grounded_normalized_region_and_no_route_card(tmp_path, monkeypatch):
    helper, adapter, client = _observed_tree_rig(tmp_path, monkeypatch)
    adapter.request['origin']='私人出发地点'
    adapter.request['destination']='私人目的地点'
    messages = []
    def decide(task, observation, history, checkpoint):
        messages.extend(client.messages(task, observation, history))
        return SimpleNamespace(action=Action('Tap',{'element':[458,108]}),latency_ms=1)
    client.decide.side_effect=decide
    helper.tap('transit',lambda:None,lambda shot:True)
    content=messages[-1]['content']
    goal=content[1]['text']
    assert content[0]['image_url']['url'].startswith('data:image/png;base64,')
    assert '[334, 84, 583, 133]' in goal
    assert '不要选择下面的路线卡片' in goal
    assert '仅在该范围内' in goal and '仍会独立检查' in goal
    assert '私人出发地点' not in goal and '私人目的地点' not in goal
    adapter.base.transport.tap.assert_called_once()


def test_assist_capture_uses_only_os_verified_status_bar_inset(tmp_path, monkeypatch):
    helper, adapter, _ = _observed_tree_rig(tmp_path, monkeypatch)
    adapter.base.shell=Mock(return_value='InsetsSource mType=ITYPE_STATUS_BAR mFrame=[0,0][1200,154] mVisible=true')
    assert helper._observe(lambda:None)['status_bar_frame']==[0,0,1200,154]
    adapter.base.shell=Mock(return_value='unknown window dump')
    assert helper._observe(lambda:None)['status_bar_frame'] is None
