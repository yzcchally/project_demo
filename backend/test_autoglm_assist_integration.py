"""Rule-first integration at the single supported transit entry."""
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys
import xml.etree.ElementTree as ET

import pytest
from device.amap import AmapDevice, ExpectedTransitControlMissing, ID, PACKAGE
from domain import Cancelled, WorkflowError


def frame(*, mode=None, start='体育西路(地铁站)', end='广州塔(地铁站)', selected='false', transit_strip=False):
    def n(text, rid, selected='false'):
        return ET.Element('node', {'package': PACKAGE, 'resource-id': ID + rid, 'text': text,
                                  'bounds': '[10,100][400,200]', 'selected': selected})
    nodes = [n(start, 'route_edit_summary_start'), n(end, 'route_edit_summary_end')]
    if mode is not None:
        nodes.append(n(mode, 'route_input_conbine_text', selected))
    if transit_strip:
        strip = ET.Element('node', {'package': PACKAGE, 'class': 'com.autonavi.minimap.ajx3.widget.view.SpringHorizontalScrollView',
                                   'bounds': '[10,250][1100,400]', 'scrollable': 'true'})
        strip.append(n('高德推荐', '')); strip.append(n('换乘少', ''))
        nodes.extend([strip, *list(strip)])
    return {'nodes': nodes, 'epoch': 0, 'evidence': {'observation_id': 'obs'}}


@pytest.fixture
def subject(tmp_path, monkeypatch):
    monkeypatch.delenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', raising=False)
    monkeypatch.delenv('SILVERGUIDE_AUTOGLM_ASSIST_TEST_MISS', raising=False)
    monkeypatch.setattr('device.amap.time.sleep', lambda _: None)
    base = SimpleNamespace(context_epoch=0, evidence_root=tmp_path, transport=None)
    obj = AmapDevice(base, Mock())
    obj.request = {'origin': '体育西路地铁站', 'destination': '广州塔地铁站'}
    obj._assist = None; obj._assist_trigger = None
    obj.task_id = 'task-one'; obj.query_id = 'query-one'; obj.folder = tmp_path
    obj._assist_page_safe = Mock()
    obj._tap = Mock(); obj._observe = Mock()
    places = {'origin': {'name': '体育西路(地铁站)'}, 'destination': {'name': '广州塔(地铁站)'}}
    return obj, places


def fake_assist(monkeypatch, seen):
    module = ModuleType('autoglm.assist')
    class Assist:
        def __init__(self, adapter): self.adapter = adapter; self.calls = 1
        def _record(self, **item): seen.append(('log', item['status']))
        def tap(self, step, checkpoint, postcondition, *, precondition=None):
            seen.append((step, self.adapter._assist_trigger))
            precondition(frame(mode='公共交通'))
            with pytest.raises(WorkflowError): precondition(frame(end='另一终点'))
            assert not postcondition(frame(mode='公共交通'))
            assert not postcondition(frame(mode='公共交通', selected='true'))
            hidden = frame(mode='公共交通', transit_strip=True)
            hidden['nodes'][3].set('visible-to-user', 'false')
            assert not postcondition(hidden)
            wrong = frame(mode='公共交通', transit_strip=True)
            wrong['nodes'][4].set('text', '无关设置')
            assert not postcondition(wrong)
            assert postcondition(frame(mode='公共交通', transit_strip=True))
    module.AutoGLMAssist = Assist
    monkeypatch.setitem(sys.modules, 'autoglm.assist', module)


def test_rule_found_uses_native_once_and_never_constructs_assist(subject, monkeypatch):
    obj, places = subject
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    obj._observe.return_value = frame(mode='公共交通')
    obj._assist_page_safe.side_effect = AssertionError('rule hit must not run assist preflight')
    seen = []; fake_assist(monkeypatch, seen)
    obj._select_public_transit(places, lambda: None)
    assert obj._tap.call_count == 1 and seen == []


def test_original_exact_label_rule_still_works_without_a_resource_id(subject, monkeypatch):
    obj, places = subject
    shot = frame(mode='公共交通'); shot['nodes'][-1].set('resource-id', '')
    obj._observe.return_value = shot
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    seen = []; fake_assist(monkeypatch, seen)
    obj._select_public_transit(places, lambda: None)
    assert obj._tap.call_count == 1 and seen == []


def test_missing_disabled_never_constructs_assist(subject, monkeypatch):
    obj, places = subject; obj._observe.return_value = frame()
    seen = []; fake_assist(monkeypatch, seen)
    with pytest.raises(ExpectedTransitControlMissing): obj._select_public_transit(places, lambda: None)
    assert not seen and not obj._tap.called and obj._observe.call_count == 8


def test_missing_enabled_calls_assist_and_never_repeats_rule_click(subject, monkeypatch):
    obj, places = subject; obj._observe.return_value = frame()
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    seen = []; fake_assist(monkeypatch, seen)
    obj._select_public_transit(places, lambda: None)
    assert seen == [('transit', 'expected_control_not_found'), ('log', 'continued_rules')] and not obj._tap.called


def test_controlled_miss_is_explicit_and_once(subject, monkeypatch):
    obj, places = subject; obj._observe.return_value = frame(mode='公共交通')
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_TEST_MISS', 'transit')
    seen = []; fake_assist(monkeypatch, seen)
    obj._select_public_transit(places, lambda: None)
    obj._select_public_transit(places, lambda: None)
    assert seen == [('transit', 'controlled_test_miss'), ('log', 'continued_rules')] and obj._tap.call_count == 1
    # A fresh adapter / process for the same task and query still cannot
    # replay the controlled fault; a distinct query gets its own claim.
    fresh = AmapDevice(obj.base, Mock()); fresh.folder = obj.folder
    fresh.request = obj.request; fresh.task_id = obj.task_id; fresh.query_id = obj.query_id
    fresh._assist_page_safe = Mock()
    fresh._assist = None; fresh._observe = Mock(return_value=frame(mode='公共交通')); fresh._tap = Mock()
    fresh._select_public_transit(places, lambda: None)
    assert fresh._tap.call_count == 1 and len(seen) == 2
    other = obj.folder / 'other-query'; other.mkdir(); fresh.folder = other; fresh.query_id = 'query-two'
    fresh._select_public_transit(places, lambda: None)
    assert seen[-2:] == [('transit', 'controlled_test_miss'), ('log', 'continued_rules')] and len(seen) == 4


def test_reenter_same_query_reuses_assist_session_but_new_query_does_not(subject):
    obj, _ = subject; session = object()
    obj._assist_sessions[('task-one', 'query-one')] = session
    obj.check_ready = Mock(side_effect=WorkflowError('stop before device work'))
    with pytest.raises(WorkflowError): obj._query(obj.request, 'task-one', 'query-one', lambda: None)
    assert obj._assist is session
    with pytest.raises(WorkflowError): obj._query(obj.request, 'task-one', 'query-two', lambda: None)
    assert obj._assist is None


@pytest.mark.parametrize('error', [Cancelled('cancelled'), WorkflowError('bridge failed')])
def test_read_failure_or_cancellation_never_triggers_assist(subject, monkeypatch, error):
    obj, places = subject; obj._observe.side_effect = error
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    seen = []; fake_assist(monkeypatch, seen)
    with pytest.raises(type(error)): obj._select_public_transit(places, lambda: None)
    assert not seen and not obj._tap.called


def test_wrong_endpoints_never_trigger_assist(subject, monkeypatch):
    obj, places = subject; obj._observe.return_value = frame(end='错误目的地')
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    seen = []; fake_assist(monkeypatch, seen)
    with pytest.raises(WorkflowError, match='起终点'): obj._select_public_transit(places, lambda: None)
    assert not seen and not obj._tap.called


def safe_shot(*, foreign=False, foreign_parent=False, hidden_field=False, loading=False, login=False):
    shot = frame(mode='公共交通', transit_strip=True)
    root = ET.Element('hierarchy')
    container = ET.SubElement(root, 'node', {'package': PACKAGE, 'bounds': '[0,0][1200,2640]'})
    for item in shot['nodes'][:4]:
        container.append(item)
    if hidden_field:
        container.remove(shot['nodes'][0])
        hidden = ET.SubElement(container, 'node', {'package': PACKAGE, 'visible-to-user': 'false', 'bounds':'[0,0][1200,2640]'})
        hidden.append(shot['nodes'][0])
    if foreign:
        ET.SubElement(root, 'node', {'package':'com.android.permissioncontroller', 'bounds':'[0,0][1200,2640]', 'text':'允许'})
    if foreign_parent:
        root.remove(container)
        wrapper = ET.SubElement(root, 'node', {'package':'com.foreign.app', 'bounds':'[0,0][0,0]'})
        wrapper.append(container)
    if loading:
        ET.SubElement(container, 'node', {'package': PACKAGE, 'resource-id':ID+'loading_view', 'bounds':'[300,500][900,900]'})
    if login:
        ET.SubElement(container, 'node', {'package': PACKAGE, 'text':'输入密码', 'bounds':'[300,500][900,900]'})
    shot['xml'] = ET.tostring(root, encoding='unicode')
    return shot


@pytest.mark.parametrize('condition', ['foreign', 'foreign_parent', 'hidden_field', 'loading', 'login'])
def test_assist_page_preflight_rejects_foreign_hidden_and_modal_states(subject, monkeypatch, condition):
    obj, _ = subject
    monkeypatch.setattr('autoglm.executor.AutoGLMTransitDevice._foreground', lambda _: PACKAGE)
    shot = safe_shot(**{condition: True})
    with pytest.raises(WorkflowError): AmapDevice._assist_page_safe(obj, shot, lambda: None)


def test_assist_page_preflight_requires_actual_foreground(subject, monkeypatch):
    obj, _ = subject
    monkeypatch.setattr('autoglm.executor.AutoGLMTransitDevice._foreground', lambda _: 'com.other.app')
    with pytest.raises(WorkflowError): AmapDevice._assist_page_safe(obj, safe_shot(), lambda: None)


def test_assist_page_preflight_accepts_complete_route_page(subject, monkeypatch):
    obj, _ = subject
    monkeypatch.setattr('autoglm.executor.AutoGLMTransitDevice._foreground', lambda _: PACKAGE)
    AmapDevice._assist_page_safe(obj, safe_shot(), lambda: None)


@pytest.mark.parametrize('condition', ['foreign', 'foreign_parent', 'hidden_field', 'loading', 'login'])
def test_modal_or_foreign_page_never_triggers_model(subject, monkeypatch, condition):
    obj, places = subject
    monkeypatch.setenv('SILVERGUIDE_AUTOGLM_ASSIST_ENABLED', 'true')
    monkeypatch.setattr('autoglm.executor.AutoGLMTransitDevice._foreground', lambda _: PACKAGE)
    obj._assist_page_safe = lambda shot, checkpoint: AmapDevice._assist_page_safe(obj, shot, checkpoint)
    shot = safe_shot(**{condition: True})
    shot['nodes'] = [node for node in shot['nodes'] if node.get('text') != '公共交通']
    tree = ET.fromstring(shot['xml'])
    for parent in tree.iter():
        for child in list(parent):
            if child.get('text') == '公共交通': parent.remove(child)
    shot['xml'] = ET.tostring(tree, encoding='unicode')
    obj._observe.return_value = shot
    seen = []; fake_assist(monkeypatch, seen)
    with pytest.raises(WorkflowError): obj._select_public_transit(places, lambda: None)
    assert not seen and not obj._tap.called


@pytest.mark.parametrize('alias', ['公交', '公交地铁', '公交/地铁'])
def test_known_native_route_control_alias_needs_no_model(subject, alias):
    obj, places = subject; obj._observe.return_value = frame(mode=alias)
    obj._select_public_transit(places, lambda: None)
    assert obj._tap.call_count == 1
