import xml.etree.ElementTree as ET
import json
from unittest.mock import Mock
import shlex
import subprocess

import pytest
from domain import WorkflowError
from device.dingtalk import (DingTalkDevice, _parse, _self_target, _home_self_target, _confirmation,
                            _share_chooser_target, _verified_self_message, recipient_digest, route_text)
from device.dingtalk_bridge import TransientRead

PKG = 'com.alibaba.android.rimet'
TASK = 'task12345678'
QUERY = 'query12345678'


def test_launcher_resolves_unique_component_then_starts_it(monkeypatch):
    component = PKG + '/.biz.LaunchHomeActivity'
    outputs = [f'1 activities found:\n  Activity #0:\n    priority=0 isDefault=false\n    {component}\n',
               f'priority=0 isDefault=false\n{component}\n', 'Starting: Intent\n']
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, out, '') for out in outputs])
    monkeypatch.setattr('device.dingtalk.subprocess.run', run)
    monkeypatch.setattr('gmail_demo._adb', lambda: 'adb')
    check = Mock()
    DingTalkDevice(Mock())._launch_home(check)
    commands = [shlex.split(call.args[0][-1]) for call in run.call_args_list]
    assert commands[0][:4] == ['cmd', 'package', 'query-activities', '--brief']
    assert commands[1][:4] == ['cmd', 'package', 'resolve-activity', '--brief']
    assert commands[2] == ['am', 'start', '-n', component]
    assert check.call_count == 6


@pytest.mark.parametrize('listing,resolved', [
    ('No activities found\n', ''),
    ('2 activities found:\ncom.alibaba.android.rimet/.A\ncom.alibaba.android.rimet/.B\n', ''),
    ('1 activities found:\ncom.other/.Activity\n', ''),
    ('1 activities found:\ncom.alibaba.android.rimet/.ResolverActivity\n', ''),
    ('1 activities found:\ncom.alibaba.android.rimet/.A\ncom.other/.B\n', ''),
    ('1 activities found:\ncom.alibaba.android.rimet/.A;echo-danger\n', ''),
    ('1 activities found:\ncom.alibaba.android.rimet/.A\n', 'com.other/.Activity\n'),
    ('1 activities found:\ncom.alibaba.android.rimet/.A\n', 'com.alibaba.android.rimet/.B\n'),
])
def test_launcher_rejects_missing_ambiguous_or_foreign_components(monkeypatch, listing, resolved):
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, listing, ''),
                            subprocess.CompletedProcess([], 0, resolved, '')])
    monkeypatch.setattr('device.dingtalk.subprocess.run', run)
    monkeypatch.setattr('gmail_demo._adb', lambda: 'adb')
    with pytest.raises(WorkflowError): DingTalkDevice(Mock())._launch_home(lambda: None)
    assert all(shlex.split(c.args[0][-1])[:2] != ['am', 'start'] for c in run.call_args_list)


def route():
    return {'task_id': TASK, 'query_id': QUERY, 'source_app': '高德地图', 'classification': 'COMPLETE',
            'route_verified': True, 'missing_fields': [], 'segments': [
                {'mode': 'subway', 'line': '3号线', 'board': '体育西路', 'alight': '广州塔', 'direction': '海傍方向'}]}


def tree(*nodes):
    root = ET.Element('hierarchy', {'window-id': '3', 'nonce': 'nonce-test'})
    for text, bounds in nodes:
        ET.SubElement(root, 'node', {'text': text, 'package': PKG, 'bounds': bounds,
                                     'visible-to-user': 'true', 'enabled': 'true'})
    return ET.tostring(root, encoding='unicode')


def test_share_chooser_supports_the_dingtalk_friend_row_on_huawei():
    root = ET.fromstring(tree(('发送给好友', '[238,796][518,872]')))
    row = ET.SubElement(root, 'node', {'package': PKG, 'resource-id': PKG + ':id/item_share_to_friend',
                                      'clickable': 'true', 'bounds': '[53,555][1147,741]'})
    title = ET.SubElement(row, 'node', {'package': PKG, 'resource-id': PKG + ':id/text_title_main',
                                       'text': '发送给好友', 'bounds': '[238,610][518,686]'})
    assert _share_chooser_target(root, {title: row}) is row
    title.set('text', '发送给联系人')
    with pytest.raises(WorkflowError, match='标题不可唯一核对'):
        _share_chooser_target(root, {title: row})


def listing():
    root = ET.Element('hierarchy', {'window-id': '3'})
    row = ET.SubElement(root, 'node', {'package': PKG, 'bounds': '[0,100][900,300]', 'visible-to-user': 'true'})
    for text, rect in [('我（测试本人）', '[100,120][500,170]'), ('文件助手，多端同步', '[100,180][700,230]')]:
        ET.SubElement(row, 'node', {'text': text, 'package': PKG, 'bounds': rect, 'visible-to-user': 'true'})
    return ET.tostring(root, encoding='unicode')


def home(name='测试本人'):
    root = ET.Element('hierarchy', {'window-id': '3'})
    row = ET.SubElement(root, 'node', {'resource-id': PKG + ':id/session_item', 'package': PKG,
                        'bounds': '[0,100][900,300]', 'clickable': 'true', 'visible-to-user': 'true'})
    ET.SubElement(row, 'node', {'resource-id': PKG + ':id/session_title', 'text': f'我（{name}）',
                  'package': PKG, 'bounds': '[100,120][500,170]', 'visible-to-user': 'true'})
    return ET.tostring(root, encoding='unicode')


def confirmation(text):
    root = ET.fromstring(tree())
    panel = ET.SubElement(root, 'node', {'package': PKG, 'bounds': '[0,0][900,900]',
                          'resource-id': PKG + ':id/parentPanel', 'visible-to-user': 'true'})
    for text, bounds in [('发送给：', '[100,100][400,150]'), ('测试本人', '[100,160][400,210]'),
                         (text, '[100,220][800,400]'), ('发送', '[500,500][600,560]')]:
        ET.SubElement(panel, 'node', {'text': text, 'package': PKG, 'bounds': bounds,
                                     'visible-to-user': 'true', 'enabled': 'true'})
    return ET.tostring(root, encoding='unicode')


def test_route_must_be_current_complete_and_verified():
    good = route()
    assert QUERY in route_text(TASK, QUERY, good)
    for field, value in [('task_id', 'other'), ('query_id', 'other'), ('classification', 'PARTIAL'),
                         ('route_verified', False), ('missing_fields', ['segments'])]:
        with pytest.raises(WorkflowError): route_text(TASK, QUERY, {**good, field: value})


def test_version_two_reports_only_actual_segment_facts_and_keeps_legacy_output_stable():
    current = route()
    current.update(duration_minutes=42, walking_meters=1803, transfers=0, applied_filter=None)
    current['segments'] = [
        {'mode': 'walk', 'walking_meters': 1400, 'duration_minutes': 23},
        {**current['segments'][0], 'direction': '开往海傍方向', 'stops': 2},
        {'mode': 'walk', 'walking_meters': 403, 'duration_minutes': 8},
    ]
    legacy = route_text(TASK, QUERY, current)
    assert legacy == ('高德地图已核对路线\n步行：具体走法请查看高德地图\n'
                      '地铁3号线：体育西路上车，往开往海傍方向，广州塔下车\n'
                      f'步行：具体走法请查看高德地图\n[SilverGuide路线:{QUERY}]')
    v2 = route_text(TASK, QUERY, current, version=2)
    assert '步行（约1.4公里，约23分钟）' in v2
    assert '步行（约403米，约8分钟）' in v2
    assert '乘坐2站' in v2 and '换乘0次' in v2
    assert '往海傍方向' in v2 and '往开往' not in v2
    assert f'[SilverGuide路线:{QUERY}]' in v2
    assert '少换乘' not in v2 and '换乘少' not in v2
    assert '总步行约1.803公里' in v2


def test_version_two_never_guesses_missing_segment_facts_or_accepts_unknown_version():
    current = route()
    current['segments'] = [
        {'mode': 'walk', 'walking_meters': None, 'duration_minutes': float('nan')},
        {**current['segments'][0], 'stops': True},
        {'mode': 'walk', 'walking_meters': 403, 'duration_minutes': None},
    ]
    current['walking_meters'] = 1803
    text = route_text(TASK, QUERY, current, version=2)
    assert '步行（距离和时间未显示）' in text
    assert '步行（约403米）' in text and '总步行约1.803公里' in text
    assert '乘坐1站' not in text
    with pytest.raises(WorkflowError): route_text(TASK, QUERY, current, version=3)
    with pytest.raises(WorkflowError): route_text(TASK, QUERY, current, version=True)


def test_only_self_row_with_adjacent_file_assistant_is_accepted():
    root, parents = _parse(listing())
    assert _self_target(root, parents)[1] == '测试本人'
    root, parents = _parse(tree(('我（测试本人）', '[0,0][200,50]'), ('文件助手', '[0,1000][300,1050]')))
    with pytest.raises(WorkflowError): _self_target(root, parents)


def test_home_navigation_rejects_other_account_and_ambiguous_self_rows():
    root, parents = _parse(home())
    assert _home_self_target(root, parents, '测试本人').get('resource-id', '').endswith('session_title')
    with pytest.raises(WorkflowError): _home_self_target(root, parents, '其他人')
    row = next(root.iter('node'))
    root.append(ET.fromstring(home())[0])
    parents = {c: n for n in root.iter('node') for c in n}
    with pytest.raises(WorkflowError): _home_self_target(root, parents, '测试本人')


def test_confirmation_requires_exact_recipient_and_entire_content():
    text = route_text(TASK, QUERY, route())
    root, parents = _parse(confirmation(text))
    assert _confirmation(root, parents, '测试本人', text).get('text') == '发送'
    with pytest.raises(WorkflowError): _confirmation(root, parents, '其他人', text)
    root, parents = _parse(confirmation(text[:8] + '…'))
    with pytest.raises(WorkflowError): _confirmation(root, parents, '测试本人', text)


def test_click_is_once_only_after_durable_attempt_and_result_is_unknown(monkeypatch):
    text = route_text(TASK, QUERY, route())
    device = DingTalkDevice(Mock(context_epoch=0))
    device._launch = Mock(); device._launch_home = Mock()
    device._observe = Mock(side_effect=[_parse(listing()), _parse(listing()),
        _parse(confirmation(text)), _parse(confirmation(text)), _parse(confirmation(text))])
    actions = []
    device._click = lambda node, check: actions.append(_text_for_test(node))
    result = device.share_self(TASK, QUERY, route(), lambda: None, lambda digest: actions.append('registered'), text_version=1)
    assert actions == ['我（测试本人）', 'registered', '发送']
    assert result['status'] == 'unknown'


def _text_for_test(node): return node.get('text')


def chat(content, status='已读', title='我（测试本人）'):
    root = ET.Element('hierarchy', {'window-id': '4'})
    def add(parent, rid, text, box='[100,100][800,200]'):
        return ET.SubElement(parent, 'node', {'resource-id': PKG + ':id/' + rid, 'text': text,
                             'package': PKG, 'bounds': box, 'visible-to-user': 'true'})
    add(root, 'tv_title', title)
    row = add(root, 'chatting_content_view_container', '')
    add(row, 'chatting_content_tv', content)
    group = add(row, 'll_msg_status', '')
    add(group, 'chatting_unreadcount_tv1', status)
    return root


def test_sent_proof_requires_exact_self_chat_body_marker_and_same_row_status():
    text = route_text(TASK, QUERY, route())
    for status in ('已读', '已送达'):
        r = chat(text, status); p = {c: n for n in r.iter('node') for c in n}
        assert _verified_self_message(r, p, '测试本人', text, QUERY)
    for r in (chat(text, ''), chat(text, '发送失败'), chat(text, title='其他人'),
              chat(text.replace(QUERY, 'other-query')), chat(text + '其他内容')):
        p = {c: n for n in r.iter('node') for c in n}
        assert not _verified_self_message(r, p, '测试本人', text, QUERY)
    r = chat(text, '')
    other = ET.SubElement(r, 'node', {'resource-id': PKG + ':id/chatting_content_view_container',
                          'bounds': '[0,0][900,900]', 'package': PKG, 'visible-to-user': 'true'})
    ET.SubElement(other, 'node', {'resource-id': PKG + ':id/chatting_unreadcount_tv1', 'text': '已读',
                  'bounds': '[1,1][50,50]', 'package': PKG, 'visible-to-user': 'true'})
    p = {c: n for n in r.iter('node') for c in n}
    assert not _verified_self_message(r, p, '测试本人', text, QUERY)
    # An unrelated message in the same malformed native container cannot lend
    # its success status to the exact matched target message.
    r = chat(text)
    row = next(n for n in r.iter('node') if n.get('resource-id','').endswith('chatting_content_view_container'))
    ET.SubElement(row, 'node', {'resource-id': PKG + ':id/chatting_content_tv', 'text': '另一条消息',
                  'bounds': '[1,1][50,50]', 'package': PKG, 'visible-to-user': 'true'})
    p = {c: n for n in r.iter('node') for c in n}
    assert not _verified_self_message(r, p, '测试本人', text, QUERY)
    # Two statuses under the same group are ambiguous, even if one says 已读.
    r = chat(text)
    group = next(n for n in r.iter('node') if n.get('resource-id','').endswith('ll_msg_status'))
    ET.SubElement(group, 'node', {'resource-id': PKG + ':id/chatting_unreadcount_tv1', 'text': '已读',
                  'bounds': '[1,1][50,50]', 'package': PKG, 'visible-to-user': 'true'})
    p = {c: n for n in r.iter('node') for c in n}
    assert not _verified_self_message(r, p, '测试本人', text, QUERY)
    r = chat(text)
    ET.SubElement(r, 'node', {'resource-id': PKG + ':id/parentPanel', 'bounds': '[0,0][900,900]',
                  'package': PKG, 'visible-to-user': 'true'})
    p = {c: n for n in r.iter('node') for c in n}
    assert not _verified_self_message(r, p, '测试本人', text, QUERY)


def test_read_only_chat_proof_after_send(monkeypatch):
    text = route_text(TASK, QUERY, route())
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch = Mock(); device._launch_home = Mock()
    r = chat(text); p = {c: n for n in r.iter('node') for c in n}
    device._observe = Mock(side_effect=[_parse(listing()), _parse(listing()),
        _parse(confirmation(text)), _parse(confirmation(text)), _parse(confirmation(text)),
        _parse(home()), _parse(home()), _parse(home()), (r, p)])
    actions = []
    device._click = lambda node, check: actions.append(_text_for_test(node))
    result = device.share_self(TASK, QUERY, route(), lambda: None, lambda digest: actions.append('registered'), text_version=1)
    assert result['status'] == 'sent' and actions == ['我（测试本人）', 'registered', '发送', '我（测试本人）']


def test_verify_only_requires_original_account_digest_and_never_clicks():
    text = route_text(TASK, QUERY, route())
    r = chat(text); p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0))
    device._observe = Mock(return_value=(r, p)); device._click = Mock(); device._launch_home = Mock()
    assert device.verify_self(TASK, QUERY, route(), lambda: None, '')['status'] == 'unknown'
    device._observe.assert_not_called()
    assert device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))['status'] == 'sent'
    device._click.assert_not_called(); device._launch_home.assert_not_called()


def test_verify_navigation_rejects_another_self_account_without_tapping(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    r = chat(text, title='我（其他账号）'); p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[(r, p)] + [_parse(home('其他账号'))] * 6)
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'unknown'
    device._click.assert_not_called()


def test_verify_waits_for_window_transition_without_another_click(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    r = chat(text); p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), _parse(home()), _parse(home()),
                                       TransientRead('tree_changed'), _parse(home()), (r, p)])
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'sent'
    assert device._click.call_count == 1


def test_verify_launcher_can_resume_existing_self_chat_without_click(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    r = chat(text); p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), (r, p), (r, p)])
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'sent'
    device._launch_home.assert_called_once(); device._click.assert_not_called()


def test_verify_resumed_wrong_chat_fails_without_click(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    r = chat(text, title='我（其他账号）'); p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), (r, p)])
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'unknown'
    assert device._observe.call_count == 2
    device._click.assert_not_called()


def test_verify_diagnostics_are_local_fixed_enums_without_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    wrong = chat(text, title='我（不应记录的名字）'); p = {c: n for n in wrong.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0, evidence_root=tmp_path)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), (wrong, p)])
    digest = recipient_digest('测试本人')
    result = device.verify_self(TASK, QUERY, route(), lambda: None, digest)
    assert result['status'] == 'unknown'
    saved = (tmp_path / TASK / 'transit' / QUERY / 'dingtalk-verify.jsonl').read_text()
    records = [json.loads(line) for line in saved.splitlines()]
    assert records[-2:] == [
        {'source': 'verify_only', 'stage': 'resumed_chat', 'outcome': 'failed'},
        {'source': 'verify_only', 'stage': 'resumed_chat', 'outcome': 'unknown'}]
    for private in ('测试本人', '不应记录的名字', text, digest, '其他账号'):
        assert private not in saved


def test_verify_diagnostic_write_failure_cannot_change_result(tmp_path, monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    blocked = tmp_path / 'not_a_directory'; blocked.write_text('x')
    text = route_text(TASK, QUERY, route()); r = chat(text)
    p = {c: n for n in r.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0, evidence_root=blocked))
    device._observe = Mock(return_value=(r, p)); device._click = Mock()
    assert device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))['status'] == 'sent'
    device._click.assert_not_called()


def test_verify_rejects_unrecognized_no_title_page_after_click(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), _parse(home()), _parse(home()),
                                       _parse(tree(('无关页面', '[0,0][200,50]')))])
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'unknown'
    assert device._observe.call_count == 4 and device._click.call_count == 1


@pytest.mark.parametrize('changed_before_click', [True, False])
def test_verify_rejects_identical_home_row_in_a_different_window(monkeypatch, changed_before_click):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    changed = home().replace('window-id="3"', 'window-id="8"')
    observations = [_parse(home()), _parse(home())]
    if not changed_before_click:
        observations.append(_parse(home()))
    observations.append(_parse(changed))
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=observations)
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'unknown'
    assert device._click.call_count == (0 if changed_before_click else 1)


def test_verify_stops_at_wrong_identity_after_navigating(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    wrong = chat(text, title='我（其他账号）'); p = {c: n for n in wrong.iter('node') for c in n}
    device = DingTalkDevice(Mock(context_epoch=0)); device._launch_home = Mock(); device._click = Mock()
    device._observe = Mock(side_effect=[_parse(home()), _parse(home()), _parse(home()), (wrong, p)])
    result = device.verify_self(TASK, QUERY, route(), lambda: None, recipient_digest('测试本人'))
    assert result['status'] == 'unknown'
    assert device._click.call_count == 1 and device._observe.call_count == 4


def test_page_change_after_recorded_attempt_is_unknown_without_click(monkeypatch):
    monkeypatch.setattr('device.dingtalk.time.sleep', lambda _: None)
    text = route_text(TASK, QUERY, route())
    changed = confirmation(text).replace('测试本人', '其他人')
    device = DingTalkDevice(Mock(context_epoch=0))
    device._launch = Mock(); device._launch_home = Mock()
    device._observe = Mock(side_effect=[_parse(listing()), _parse(listing()),
        _parse(confirmation(text)), _parse(confirmation(text))] + [_parse(changed)] * 6)
    actions = []
    device._click = lambda node, check: actions.append(_text_for_test(node))
    result = device.share_self(TASK, QUERY, route(), lambda: None, lambda digest: actions.append('registered'), text_version=1)
    assert result['status'] == 'unknown' and actions == ['我（测试本人）', 'registered']
