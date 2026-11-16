"""Offline provider-contract, privacy and cancellation tests; no network/phone."""
import base64
import json
import struct
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import openai
import pytest

from autoglm.client import AutoGLMClient, FACTS_DELIMITER, SELECTED_ROUTE_SUFFIX, _evidence_goal, _verified_destination_route
from autoglm.config import Config
from autoglm.protocol import ProtocolError
from domain import Cancelled, WorkflowError


def frame(width=1200, height=2640, image_width=None):
    header = b'\x89PNG\r\n\x1a\n' + struct.pack('>I',13) + b'IHDR' + struct.pack('>II',image_width or width,height) + b'\x08\x02\x00\x00\x00' + b'\x00'*4
    return {'screenshot_base64':base64.b64encode(header).decode(), 'width':width, 'height':height,
            'observation_id':'new-frame', 'current_app':'com.autonavi.minimap', 'screen_info':{'checked_places':['origin']}}


class Stream:
    def __init__(self, text='do(action="Back")', finish='stop'):
        self.parts = [NS(choices=[NS(delta=NS(content=text[:8]),finish_reason=None)]),
                      NS(choices=[NS(delta=NS(content=text[8:]),finish_reason=None)]),
                      NS(choices=[NS(delta=NS(content=None),finish_reason=finish)])]
        self.closed=False
    def __iter__(self): return iter(self.parts)
    def close(self): self.closed=True


def setup_client(tmp_path, stream=None):
    stream=Stream() if stream is None else stream
    create=Mock(return_value=stream)
    provider=NS(chat=NS(completions=NS(create=create)))
    config=Config(api_key='offline-test-fixture')
    return AutoGLMClient(config,tmp_path,provider),create,stream


def sent_payload(parts):
    assert [part['type'] for part in parts]==['image_url','text']
    goal,facts=parts[1]['text'].split(FACTS_DELIMITER,1)
    payload,end=json.JSONDecoder().raw_decode(facts)
    assert facts[end:] in ('',SELECTED_ROUTE_SUFFIX) or facts[end:].startswith(('\n坐标提醒：','\n当前进度：'))
    assert payload['current_goal']==goal
    return payload


@pytest.mark.parametrize('selected,current_app,loading,expect_suffix',[
    (True,'com.autonavi.minimap',False,True),
    (False,'com.autonavi.minimap',False,False),
    (True,'com.example.demo',False,False),
    (True,'com.autonavi.minimap',True,False),
])
def test_selected_route_final_format_prompt_is_after_json_and_only_when_safe(tmp_path,selected,current_app,loading,expect_suffix):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['current_app']=current_app
    shot['screen_info'].update(selected_route=selected,loading={'visible':loading})
    parts=client.messages({},shot,[])[-1]['content']
    payload=sent_payload(parts)
    assert payload['screen_info']['selected_route'] is selected
    assert parts[1]['text'].endswith(SELECTED_ROUTE_SUFFIX) is expect_suffix
    if expect_suffix:
        assert '否则继续选择动作' in SELECTED_ROUTE_SUFFIX


def test_current_png_task_and_actual_outcomes_reach_provider_as_native_protocol(tmp_path):
    client,create,stream=setup_client(tmp_path)
    task={'task_id':'task-current','query_id':'query-current', 'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'},
          'unrelated_secret':'never-send-this'}
    history=[{'action':'Wait','params':{'duration':1.0},'result':'executed','observation_id':str(i)} for i in range(20)]
    decision=client.decide(task,frame(),history,Mock())
    assert decision.action.kind=='Back' and decision.model=='autoglm-phone' and stream.closed
    args=create.call_args.kwargs
    assert args['stream'] is True and 'response_format' not in args
    assert args['messages'][-1]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
    sent=sent_payload(args['messages'][-1]['content'])
    assert sent['task']['request']['origin']=='体育西路地铁站'
    assert sent['observation_id']=='new-frame' and sent['actual_execution_history']==history[-8:]
    assert 'never-send-this' not in json.dumps(args)
    records=(tmp_path/'autoglm-model-calls.jsonl').read_text()
    assert 'offline-test-fixture' not in records and '体育西路' not in records and 'screenshot_base64' not in records
    assert json.loads(records)['action_kind']=='Back'


def test_native_control_hints_reach_current_request_without_becoming_executed_actions(tmp_path):
    client,create,_=setup_client(tmp_path,Stream('do(action="Tap", element=[850,250])'))
    shot=frame()
    hints=[{'text':'查找地点、公交、地铁','role':'query', 'bounds':[120,154,1080,313]},
           {'text':'搜索','role':'search_submit','bounds':[1000,154,1173,313]}]
    shot['screen_info']['native_control_hints']=hints
    history=[{'action':'Wait','params':{'duration':1.0},'result':'executed',
              'observation_id':'actual-previous-frame','screen_changed':False}]
    decision=client.decide({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,history,Mock())
    messages=create.call_args.kwargs['messages']
    sent=sent_payload(messages[-1]['content'])
    assert sent['screen_info']['native_control_hints']==hints
    assert sent['actual_execution_history']==history
    assert [m['content'] for m in messages if m['role']=='assistant']==['do(action="Wait", duration=1.0)']
    # The client returns the model's point unchanged; factual bounds do not
    # become a generated click, a centered coordinate, or an executed record.
    assert decision.action.kind=='Tap' and decision.action.params=={'element':[850,250]}
    assert shot['screen_info']['native_control_hints']==hints
    assert 'screen_info.native_control_hints' in messages[0]['content']
    assert '不表示动作已执行、执行顺序或框内任意位置都安全' in messages[0]['content']
    assert '执行前系统仍会独立重读页面并校验目标' in messages[0]['content']
    assert '查找地点、公交、地铁' not in (tmp_path/'autoglm-model-calls.jsonl').read_text()


def test_images_are_current_each_call_and_actual_state_is_fed_back(tmp_path):
    client,create,_=setup_client(tmp_path)
    a=frame();b=frame(width=1080,height=2400);b['observation_id']='after-executed-tap'
    client.decide({'request':{}},a,[],Mock())
    create.return_value=Stream('finish(message="交由验证")')
    client.decide({'request':{}},b,[{'action':'Tap','params':{'element':[500,300]},'result':'executed','screen_changed':True}],Mock())
    first=create.call_args_list[0].kwargs['messages'][-1]['content']
    second=create.call_args_list[1].kwargs['messages'][-1]['content']
    assert first[0]!=second[0]
    assert sent_payload(second)['observation_id']=='after-executed-tap'


def test_config_is_separate_from_deepseek_and_key_is_never_in_repr(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','other-provider-fixture')
    cfg=Config.from_env({'SILVERGUIDE_AUTOGLM_API_KEY':'private-config-fixture'})
    assert cfg.base_url=='https://open.bigmodel.cn/api/paas/v4' and cfg.model=='autoglm-phone'
    assert cfg.total_seconds==240 and cfg.max_steps==30
    assert 'private-config-fixture' not in repr(cfg)
    assert Config.from_env({}).api_key==''


@pytest.mark.parametrize('url',['https://user:password@example.org/v1','http://remote.example.org/v1','https://example.org/v1?key=private','file:///tmp/model'])
def test_credentials_in_url_and_nonlocal_plaintext_endpoints_are_rejected(url):
    with pytest.raises(WorkflowError): Config.from_env({'SILVERGUIDE_AUTOGLM_BASE_URL':url})


def test_explicit_loopback_compatible_service_is_allowed():
    assert Config.from_env({'SILVERGUIDE_AUTOGLM_BASE_URL':'http://127.0.0.1:8001/v1'}).base_url.startswith('http:')


@pytest.mark.parametrize('settings',[
    {'SILVERGUIDE_AUTOGLM_MAX_STEPS':'0'}, {'SILVERGUIDE_AUTOGLM_MAX_STEPS':'inf'},
    {'SILVERGUIDE_AUTOGLM_TIMEOUT':'nan'}, {'SILVERGUIDE_AUTOGLM_TOTAL_SECONDS':'999999'},
    {'SILVERGUIDE_AUTOGLM_HISTORY_LIMIT':'-1'},
])
def test_budget_config_is_bounded(settings):
    with pytest.raises(WorkflowError): Config.from_env(settings)


def test_missing_autoglm_key_is_clear_and_never_uses_other_provider(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','other-provider-fixture')
    client=AutoGLMClient(Config.from_env({}),tmp_path,Mock())
    with pytest.raises(WorkflowError,match='SILVERGUIDE_AUTOGLM_API_KEY'):
        client.decide({},frame(),[],Mock())
    client._client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize('error,message',[
    (TimeoutError('private-provider-body'), '超时'),
    (RuntimeError('private-provider-body'),'不可用'),
    (openai.AuthenticationError('private-provider-body',response=httpx.Response(401,request=httpx.Request('POST','https://example.org')),body=None),'认证'),
])
def test_provider_errors_are_truthful_and_do_not_disclose_response_or_key(tmp_path,error,message):
    client,create,_=setup_client(tmp_path);create.side_effect=error
    with pytest.raises(WorkflowError,match=message) as caught:
        client.decide({'task_id':'task'},frame(),[],Mock())
    assert 'private-provider-body' not in str(caught.value)
    assert 'private-provider-body' not in (tmp_path/'autoglm-model-calls.jsonl').read_text()


@pytest.mark.parametrize('text,finish',[
    ('', 'stop'), ('do(action="Back")','length'), ('do(action="Back")',None),
    ('do(action="Back"','stop'), ('do(action="Back");do(action="Back")','stop'),
])
def test_empty_truncated_multiple_actions_never_return_a_decision(tmp_path,text,finish):
    client,create,stream=setup_client(tmp_path,Stream(text,finish))
    with pytest.raises(WorkflowError): client.decide({},frame(),[],Mock())
    assert stream.closed
    assert json.loads((tmp_path/'autoglm-model-calls.jsonl').read_text())['ok'] is False


def test_cancel_during_api_discards_late_return_and_closes_stream(tmp_path):
    client,create,stream=setup_client(tmp_path)
    cancelled=False
    def response(**kwargs):
        nonlocal cancelled
        cancelled=True
        return stream
    create.side_effect=response
    def checkpoint():
        if cancelled:raise Cancelled('取消')
    with pytest.raises(Cancelled):client.decide({},frame(),[],checkpoint)
    assert stream.closed


def test_raw_reasoning_and_action_text_are_not_persisted(tmp_path):
    private='private-thought-fixture'
    client,_,_=setup_client(tmp_path,Stream(f'<think>{private}</think><answer>finish(message="private-finish-fixture")</answer>'))
    assert client.decide({},frame(),[],Mock()).action.kind=='Finish'
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text()
    assert private not in log and 'private-finish-fixture' not in log


@pytest.mark.parametrize('observation',[frame(image_width=1080), {**frame(),'width':True}, {**frame(),'screenshot_base64':'not-a-png'}])
def test_missing_mismatched_or_invalid_screenshot_never_reaches_api(tmp_path,observation):
    client,create,_=setup_client(tmp_path)
    with pytest.raises(WorkflowError):client.decide({},observation,[],Mock())
    create.assert_not_called()


def test_client_constructor_has_no_hidden_api_retries(tmp_path,monkeypatch):
    create=Mock(return_value=Stream());constructor=Mock(return_value=NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(openai,'OpenAI',constructor)
    AutoGLMClient(Config(api_key='offline-test-fixture'),tmp_path).decide({},frame(),[],Mock())
    assert constructor.call_args.kwargs['max_retries']==0
    assert constructor.call_args.kwargs['timeout']==35


def test_conversation_uses_only_actual_executed_commands_and_latest_image(tmp_path):
    client,_,_=setup_client(tmp_path)
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市','preference':'fewer_transfers'}}
    history=[
        {'action':'Tap','params':{'element':[500,300]},'result':'executed','observation_id':'after-focus','screen_changed':False,'raw_thought':'never-forward'},
        {'action':'Type','params':{'text':'广州市体育西路地铁站'},'result':'executed','observation_id':'after-input','screen_changed':True},
    ]
    messages=client.messages(task,frame(),history)
    assert [x['role'] for x in messages]==['system','user','assistant','user','assistant','user']
    assert messages[1]['content'].startswith('请在高德地图查询公交或地铁路线：在广州市，从体育西路地铁站前往广州塔地铁站')
    for content in (messages[1]['content'], messages[-1]['content'][1]['text']):
        assert '分别核对起点和终点主地点详情中的名称、城市和地址' in content
        assert '地址按已确认要求核对' in content
        assert '不能用搜索词或默认位置替代地点来源' in content
        assert '再按本轮偏好选择真实方案，并读全路线分段' in content
    assert messages[2]['content']=='do(action="Tap", element=[500, 300])'
    assert messages[4]['content']=='do(action="Type", text="广州市体育西路地铁站")'
    assert 'after-focus' in messages[3]['content']
    assert 'after-input' in messages[-1]['content'][1]['text']
    assert 'never-forward' not in json.dumps(messages,ensure_ascii=False)
    images=[part for message in messages if isinstance(message['content'],list) for part in message['content'] if part['type']=='image_url']
    assert len(images)==1 and images[0] is messages[-1]['content'][0]


def test_incomplete_or_unconfirmed_history_never_becomes_a_fabricated_command(tmp_path):
    client,_,_=setup_client(tmp_path)
    history=[{'action':'Wait','result':'executed'},
             {'action':'Tap','params':{'element':[500,300]},'result':'rejected'},
             {'action':'Finish','params':{'message':'done'},'result':'executed'},
             {'action':'Launch','params':{'app':'其他应用'},'result':'executed'}]
    messages=client.messages({'request':{}},frame(),history)
    assert [message['role'] for message in messages]==['system','user']
    assert sent_payload(messages[-1]['content'])['actual_execution_history']==[]


def test_focused_field_hint_and_actual_history_are_visible_without_repeated_focus_advice(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info']['unique_focused_input']=True
    messages=client.messages({'request':{'origin':'体育西路','destination':'广州塔'}},shot,[
        {'action':'Tap','params':{'element':[500,300]},'result':'executed','screen_changed':False}])
    assert '已有唯一聚焦输入框' in messages[-1]['content'][1]['text']
    assert '不要重复点击同一输入框' in messages[-1]['content'][1]['text']
    assert len([m for m in messages if m['role']=='assistant'])==1


def test_current_goal_and_input_correction_follow_observed_facts_not_a_click_sequence(tmp_path):
    client,_,_=setup_client(tmp_path)
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'missing_fields':[]},'destination':{'missing_fields':['city']}},
        focused_input={'role':'origin','current_text':'体育西路地铁站','allowed_texts':['体育西路地铁站'],'already_matches':True},
        input_correction={'reason_code':'input_value_mismatch','executed':False})
    text=client.messages(task,shot,[])[-1]['content'][1]['text']
    assert text.startswith('当前目标是核对目的地“广州塔地铁站”')
    assert '尚缺字段：city' in text and '暂不计算或选择路线' in text
    assert '不从其他地点点击路线或公交入口' in text
    assert '当前真实聚焦框是【出发地】' in text and '无需重复改写' in text
    assert '上次提案没有执行' in text
    assert 'Tap' not in text and 'element=' not in text


def test_foreign_host_launch_constraint_precedes_the_poi_goal(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['current_app']='com.example.demo'
    shot['screen_info']['place_status']={'origin':{'missing_fields':['name','city']}}
    text=client.messages({'request':{'origin':'体育西路地铁站'}},shot,[])[-1]['content'][1]['text']
    assert text.startswith('请先打开高德地图。')
    assert '不能点击、输入或返回当前应用' in text
    assert '当前目标是核对出发地' in text


def test_observed_poi_completion_changes_goal_without_substituting_a_gui_action(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info'].update(place_status={r:{'missing_fields':[]} for r in ['origin','destination']},
        applied_filter=None,selected_route=False)
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','preference':'fewer_transfers'}}
    text=client.messages(task,shot,[])[-1]['content'][1]['text']
    assert text.startswith('两端地点已从本轮真实页面核对')
    assert '从“体育西路地铁站”前往“广州塔地铁站”' in text
    assert '换乘少' in text and '尚未确认' in text and '不能采用默认我的位置' in text
    shot['screen_info']['selected_route']=True
    text=client.messages(task,shot,[])[-1]['content'][1]['text']
    assert text.startswith('本轮路线方案已经实际选中') and '查看完整路线详情' in text
    assert 'element=' not in text


def test_authorized_old_query_text_does_not_discourage_searching_the_other_endpoint(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'missing_fields':[]},'destination':{'missing_fields':['city','name']}},
        focused_input={'role':'query','current_text':'体育西路地铁站','allowed_texts':['体育西路地铁站','广州塔地铁站'],'already_matches':True})
    text=client.messages({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,[])[-1]['content'][1]['text']
    assert '当前目标是核对目的地' in text
    assert '是否需要改为另一个地点，请依当前目标判断' in text
    assert '无需重复改写' not in text


@pytest.mark.parametrize('age,expected',[(2,'请用 Wait'),(16,'请用 Back')])
def test_loading_modal_takes_priority_over_underlying_page_goal(tmp_path,age,expected):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'missing_fields':['name','city']}},loading={'visible':True,'age_seconds':age})
    text=client.messages({'request':{'origin':'体育西路地铁站'}},shot,[])[-1]['content'][1]['text']
    assert text.startswith('高德当前有“正在加载”弹窗覆盖页面')
    assert expected in text
    assert ('不要点底页' if age<15 else '不要继续点底页或无限等待') in text


def test_loading_recovery_does_not_repeat_the_failed_history_navigation(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info']['loading_recovery']={'cancelled':True}
    text=client.messages({'request':{'origin':'体育西路地铁站'}},shot,[])[-1]['content'][1]['text']
    assert '重新搜索本轮地点' in text and '同一个未能打开的历史入口' in text


def test_single_current_text_preserves_missing_destination_goal_and_rejected_route_feedback(tmp_path):
    client,create,_=setup_client(tmp_path)
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'name':'体育西路地铁站','city':'广州市','missing_fields':[]},
                      'destination':{'missing_fields':['name','city']}},
        evidence_correction={'missing_roles':['destination'],'missing_fields':{'destination':['name','city']}},
        native_control_hints=[{'text':'体育西路地铁站','role':'query','bounds':[185,154,951,313]}])
    history=[{'action':'Tap','params':{'element':[844,959]},'result':'rejected'},
             {'action':'Type','params':{'text':'体育西路地铁站'},'result':'executed','observation_id':'actual-type'}]
    task={'task_id':'current-task','query_id':'current-query',
          'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    client.decide(task,shot,history,Mock())
    messages=create.call_args.kwargs['messages']
    parts=messages[-1]['content'];payload=sent_payload(parts)
    text=parts[1]['text'];goal=payload['current_goal']
    assert goal.startswith('当前目标是核对目的地“广州塔地铁站”')
    assert '暂不计算或选择路线' in goal
    assert '上次打开公交路线入口的提案没有执行' in goal
    assert '上一步实际执行结果' in goal and 'actual-type' in goal
    assert '当前目标是核对目的地“广州塔地铁站”' in text.split(FACTS_DELIMITER)[0]
    assert payload['screen_info']['evidence_correction']['missing_roles']==['destination']
    assert payload['actual_execution_history']==[history[1]]
    assert [m['content'] for m in messages if m['role']=='assistant']==['do(action="Type", text="体育西路地铁站")']


def route_goal(client,**updates):
    shot=frame();shot['screen_info'].update(
        place_status={r:{'missing_fields':[]} for r in ('origin','destination')},
        on_route_page=True,observed_public_transit=True,
        route_inputs={'origin':'体育西路地铁站','destination':'广州塔地铁站'},
        route_inputs_match={'origin':True,'destination':True},
        applied_filter='换乘少',selected_route=False)
    shot['screen_info'].update(updates)
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','preference':'fewer_transfers'}}
    parts=client.messages(task,shot,[])[-1]['content']
    return sent_payload(parts)['current_goal']


def test_default_origin_mismatch_takes_priority_over_public_mode_and_route_selection(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,route_inputs={'origin':'我的位置','destination':'广州塔地铁站'},
                    route_inputs_match={'origin':False,'destination':True},observed_public_transit=False)
    assert goal.startswith('当前起点显示“我的位置”')
    assert '请把起点改为“体育西路地铁站”，暂不选方案' in goal
    assert 'element=' not in goal and 'do(action=' not in goal
    assert '请进入公共交通模式' not in goal


def test_actual_destination_mismatch_is_the_next_goal_after_origin_matches(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,route_inputs={'origin':'体育西路地铁站','destination':'其他地点'},
                    route_inputs_match={'origin':True,'destination':False})
    assert goal.startswith('当前终点显示“其他地点”')
    assert '请把终点改为“广州塔地铁站”，暂不选方案' in goal


def test_mode_precedes_preference_when_actual_endpoint_fields_match(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,observed_public_transit=False,applied_filter=None)
    assert goal.startswith('当前路线页尚未确认公共交通模式')
    assert '请进入公共交通模式，暂不选方案' in goal
    assert '请实际应用' not in goal


@pytest.mark.parametrize('applied',[None,'高德推荐','步行少'])
def test_requested_preference_must_be_actually_applied_before_choosing_a_card(tmp_path,applied):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,applied_filter=applied)
    assert goal.startswith('当前起终点和公共交通模式已核对')
    assert '请实际应用“换乘少”，暂不选方案' in goal
    assert f'已实际应用的筛选为“{applied or "尚未确认"}”' in goal
    if applied is None:
        assert '即使界面看起来默认选中' in goal
        assert '请先实际点击一次“换乘少”，再选择路线方案' in goal


def test_route_card_goal_requires_matching_endpoints_mode_and_preference(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client)
    assert goal.startswith('当前起终点、公共交通模式及“换乘少”偏好均已核对')
    assert '请选择符合本轮要求的公共交通方案' in goal and '查看真实路线详情' in goal


def destination_poi_route_context():
    return {'place_status':{'origin':{'name':'体育西路(地铁站)','city':'广州市','missing_fields':[]},
                            'destination':{'name':'广州塔(地铁站)','city':'广州市','missing_fields':[]}},
            'page_kind':'place_detail','current_place':{'role':'destination','name':'广州塔(地铁站)','city':'广州市'},
            'native_control_hints':[{'role':'route','text':'','normalized_bounds':[748,940,944,989]}],
            'on_route_page':False,'selected_route':False}


def test_verified_destination_poi_can_expose_its_grounded_route_entry_as_fact():
    info=destination_poi_route_context()
    authority={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市','preference':'recommended'}}
    goal=_evidence_goal(authority,info,current_app='com.autonavi.minimap')
    assert goal.startswith('当前是已核对的目的地“广州塔(地铁站)”详情')
    assert '[748,940]至[944,989]' in goal and '自行决定' in goal and '不表示动作已经执行' in goal
    assert 'do(action=' not in goal and 'element=' not in goal
    assert '无需再次搜索地点' in goal


def test_verified_destination_route_progress_is_at_end_of_actual_message(tmp_path):
    client,_,_=setup_client(tmp_path);shot=frame();shot['screen_info']=destination_poi_route_context()
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    text=client.messages(task,shot,[])[-1]['content'][1]['text']
    suffix=text.rsplit('\n当前进度：',1)[1]
    assert '无需再次搜索' in suffix and '[748,940]至[944,989]' in suffix
    assert '自主决定下一步' in suffix and suffix.endswith('此事实不代表已执行任何动作。')
    assert 'do(action=' not in suffix and 'element=' not in suffix


@pytest.mark.parametrize('mutation',['foreign','unknown_page','origin_page','name_changed','city_changed',
                                     'city_missing','partial_origin','partial_destination','no_hint','two_hints',
                                     'wrong_role','wrong_label','bad_rect','bool_rect'])
def test_destination_entry_fact_is_never_inferred_from_ambiguous_or_stale_context(mutation):
    info=destination_poi_route_context();app='com.autonavi.minimap'
    if mutation=='foreign':app='other.app'
    elif mutation=='unknown_page':info['page_kind']='unknown'
    elif mutation=='origin_page':info['current_place']['role']='origin'
    elif mutation=='name_changed':info['current_place']['name']='其他地点'
    elif mutation=='city_changed':info['current_place']['city']='深圳市'
    elif mutation=='city_missing':info['current_place']['city']=None
    elif mutation=='partial_origin':info['place_status']['origin']['missing_fields']=['city']
    elif mutation=='partial_destination':info['place_status']['destination']['missing_fields']=['city']
    elif mutation=='no_hint':info['native_control_hints']=[]
    elif mutation=='two_hints':info['native_control_hints']*=2
    elif mutation=='wrong_role':info['native_control_hints'][0]['role']='query'
    elif mutation=='wrong_label':info['native_control_hints'][0]['text']='导航'
    elif mutation=='bad_rect':info['native_control_hints'][0]['normalized_bounds']=[748,940,1000,989]
    elif mutation=='bool_rect':info['native_control_hints'][0]['normalized_bounds']=[True,940,944,989]
    authority={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=_evidence_goal(authority,info,current_app=app)
    assert '此页面唯一可核对的路线入口' not in goal
    assert _verified_destination_route(info,authority['request'],app) is None


def test_default_recommendation_still_requires_this_querys_actual_tap(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame(); shot['screen_info'].update(
        place_status={r:{'missing_fields':[]} for r in ('origin','destination')},
        on_route_page=True,observed_public_transit=True,
        route_inputs={'origin':'体育西路地铁站','destination':'广州塔地铁站'},
        route_inputs_match={'origin':True,'destination':True},
        applied_filter=None,selected_route=False)
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','preference':'recommended'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert '即使界面看起来默认选中' in goal
    assert '请先实际点击一次“高德推荐”，再选择路线方案' in goal
    assert 'do(action=' not in goal and 'element=' not in goal


def test_preference_goal_explains_current_native_normalized_region_without_relocating_actions(tmp_path):
    client,_,_=setup_client(tmp_path)
    hint={'text':'换乘少','role':'preference','bounds':[1103,590,1200,670],
          'normalized_bounds':[920,224,999,253]}
    goal=route_goal(client,applied_filter=None,page_kind='route_list',native_control_hints=[hint])
    assert '[920,224]至[999,253]' in goal and '不代表已执行点击' in goal
    assert 'do(action=' not in goal and 'element=' not in goal


def test_preference_goal_exposes_only_the_verified_horizontal_strip_as_a_conditional_option(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,applied_filter=None,page_kind='route_list',
                    native_control_hints=[{'text':'换乘少','role':'preference','normalized_bounds':[950,228,999,249]}],
                    horizontal_scroll_regions=[{'role':'preference_scroll','axis':'horizontal',
                                                'normalized_bounds':[305,207,999,271],
                                                'safe_start_normalized_bounds':[332,216,973,262],
                                                'safe_end_normalized_bounds':[27,216,973,262],
                                                'minimum_leftward_normalized_distance':106}])
    assert '起点内部范围为[332,216]至[973,262]' in goal
    assert '向左水平滑动' in goal and '滑动后依据新截图重新定位' in goal
    assert '不要把相邻标签当作目标' in goal
    assert '终点比起点向左至少106个归一化单位' in goal
    assert '两点保持相同 y' in goal and '不保证控件未被遮挡' in goal
    assert 'do(action=' not in goal and 'start=' not in goal
    assert goal.startswith('当前目标是让屏幕最右侧的“换乘少”标签完整显示')


@pytest.mark.parametrize('mutation',['none','foreign','no_strip','duplicate','not_clipped','no_safe','wrong_page',
                                     'wrong_target','already_applied','missing_poi','mismatch','loading','selected'])
def test_clipped_preference_message_ends_with_dynamic_normalized_ranges_only_when_verified(tmp_path,mutation):
    client,_,_=setup_client(tmp_path);shot=frame();info=shot['screen_info']
    info.update(place_status={r:{'missing_fields':[]} for r in ('origin','destination')},
                on_route_page=True,page_kind='route_list',observed_public_transit=True,
                route_inputs_match={'origin':True,'destination':True},selected_route=False,applied_filter=None,
                native_control_hints=[{'text':'换乘少','role':'preference','normalized_bounds':[950,228,999,249]}],
                horizontal_scroll_regions=[{'role':'preference_scroll','axis':'horizontal',
                    'safe_start_normalized_bounds':[332,216,973,262],
                    'safe_end_normalized_bounds':[27,216,973,262], 'minimum_leftward_normalized_distance':106}])
    if mutation=='foreign':shot['current_app']='other.app'
    elif mutation=='no_strip':info['horizontal_scroll_regions']=[]
    elif mutation=='duplicate':info['horizontal_scroll_regions']*=2
    elif mutation=='not_clipped':info['native_control_hints'][0]['normalized_bounds']=[600,228,900,249]
    elif mutation=='no_safe':info['horizontal_scroll_regions'][0].pop('safe_end_normalized_bounds')
    elif mutation=='wrong_page':info['page_kind']='route_input'
    elif mutation=='wrong_target':info['native_control_hints'][0]['text']='步行少'
    elif mutation=='already_applied':info['applied_filter']='换乘少'
    elif mutation=='missing_poi':info['place_status']['origin']['missing_fields']=['city']
    elif mutation=='mismatch':info['route_inputs_match']['origin']=False
    elif mutation=='loading':info['loading']={'visible':True}
    elif mutation=='selected':info['selected_route']=True
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','preference':'fewer_transfers'}}
    message=client.messages(task,shot,[])[-1]['content'][1]['text']
    has_suffix='\n坐标提醒：' in message
    assert has_suffix == (mutation=='none')
    if has_suffix:
        suffix=message.rsplit('\n坐标提醒：',1)[1]
        assert '不要使用 JSON 中的 bounds 截图像素值' in suffix
        assert '[332,216]–[973,262]' in suffix and '[27,216]–[973,262]' in suffix
        assert '至少 106 个归一化单位' in suffix and suffix.endswith('此提示不代表手势已执行。')
        assert 'do(action=' not in suffix


def test_preference_outer_bounds_alone_are_never_described_as_safe_swipe_range(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,applied_filter=None,page_kind='route_list',
        native_control_hints=[{'text':'换乘少','role':'preference','normalized_bounds':[950,228,999,249]}],
        horizontal_scroll_regions=[{'role':'preference_scroll','axis':'horizontal',
                                    'normalized_bounds':[305,207,999,271]}])
    assert '起点内部范围' not in goal and '终点内部范围' not in goal
    assert '起点范围为[305,207]' not in goal


@pytest.mark.parametrize('mutation',['fully_visible','no_strip','duplicate_strip','wrong_axis','wrong_page','wrong_hint'])
def test_left_scroll_guidance_requires_clipped_target_and_unique_verified_strip(tmp_path,mutation):
    client,_,_=setup_client(tmp_path)
    hint={'text':'换乘少','role':'preference','normalized_bounds':[950,228,999,249]}
    strip={'role':'preference_scroll','axis':'horizontal','normalized_bounds':[305,207,999,271],
           'safe_start_normalized_bounds':[332,216,973,262], 'safe_end_normalized_bounds':[27,216,973,262],
           'minimum_leftward_normalized_distance':106}
    hints=[hint];strips=[strip];page='route_list'
    if mutation=='fully_visible':hint['normalized_bounds']=[800,228,920,249]
    elif mutation=='no_strip':strips=[]
    elif mutation=='duplicate_strip':strips*=2
    elif mutation=='wrong_axis':strip['axis']='vertical'
    elif mutation=='wrong_page':page='route_input'
    elif mutation=='wrong_hint':hint['text']='步行少'
    goal=route_goal(client,applied_filter=None,page_kind=page,native_control_hints=hints,horizontal_scroll_regions=strips)
    assert '可在当前筛选栏内向左水平滑动' not in goal


def test_current_subgoal_never_rewrites_the_task_that_executed_history_belongs_to(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame()
    shot['screen_info'].update(place_status={r:{'missing_fields':[]} for r in ('origin','destination')},
                              on_route_page=True,observed_public_transit=True,selected_route=False,
                              route_inputs={'origin':'体育西路地铁站','destination':'广州塔地铁站'},
                              route_inputs_match={'origin':True,'destination':True},applied_filter=None)
    history=[{'action':'Tap','params':{'element':[223,288]},'result':'executed','observation_id':'actual-origin-selection'}]
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','preference':'fewer_transfers'}}
    messages=client.messages(task,shot,history)
    assert messages[1]['content'].startswith('请在高德地图查询公交或地铁路线：')
    assert '请实际应用' not in messages[1]['content']
    assert '请实际应用“换乘少”' in sent_payload(messages[-1]['content'])['current_goal']
    assert sent_payload(messages[-1]['content'])['actual_execution_history']==history


@pytest.mark.parametrize('mutation',[None,'gate','other_city','duplicate','unknown_page'])
def test_missing_place_goal_uses_only_one_current_task_matched_native_click_region(tmp_path,mutation):
    client,_,_=setup_client(tmp_path)
    shot=frame()
    hint={'text':'广州塔(地铁站)','role':'place','normalized_bounds':[110,144,365,168]}
    hints=[hint]
    if mutation=='gate':hint['text']='广州塔地铁站B口'
    elif mutation=='other_city':hint['text']='北京市广州塔(地铁站)'
    elif mutation=='duplicate':hints.append(dict(hint))
    shot['screen_info'].update(page_kind='unknown' if mutation=='unknown_page' else 'search',native_control_hints=hints,
                             place_status={'origin':{'missing_fields':[]},'destination':{'missing_fields':['name','city']}})
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    if mutation is None:
        assert goal.startswith('当前要打开“广州塔(地铁站)”的主地点详情')
        assert '归一化[110,144]至[365,168]' in goal and '自行确定位置' in goal
        assert 'do(action=' not in goal and 'element=' not in goal
    else:
        assert '当前要打开' not in goal


@pytest.mark.parametrize('mutation',['wrong_text','wrong_role','duplicate','out_of_range','reversed','unknown_page'])
def test_preference_goal_does_not_turn_unproved_or_ambiguous_regions_into_guidance(tmp_path,mutation):
    client,_,_=setup_client(tmp_path)
    hint={'text':'换乘少','role':'preference','normalized_bounds':[920,224,999,253]}
    hints=[hint]
    if mutation=='wrong_text':hint['text']='步行少'
    elif mutation=='wrong_role':hint['role']='route'
    elif mutation=='duplicate':hints.append(dict(hint))
    elif mutation=='out_of_range':hint['normalized_bounds'][2]=1000
    elif mutation=='reversed':hint['normalized_bounds']=[999,224,920,253]
    goal=route_goal(client,applied_filter=None,page_kind='unknown' if mutation=='unknown_page' else 'route_list',native_control_hints=hints)
    assert '归一化可见范围' not in goal


def test_absent_route_page_and_selected_route_details_get_distinct_goals(tmp_path):
    client,_,_=setup_client(tmp_path)
    goal=route_goal(client,on_route_page=False,route_inputs={},route_inputs_match={},observed_public_transit=False)
    assert '当前尚未看到路线页面' in goal and '进入高德公共交通查询' in goal
    goal=route_goal(client,on_route_page=False,route_inputs={},route_inputs_match={},
                    observed_public_transit=False,selected_route=True)
    assert goal.startswith('本轮路线方案已经实际选中')
    assert '查看完整路线详情' in goal and '请把起点' not in goal


def test_route_rejection_feedback_remains_unexecuted_and_keeps_the_current_endpoint_goal(tmp_path):
    client,create,_=setup_client(tmp_path)
    shot=frame();shot['screen_info'].update(
        place_status={r:{'missing_fields':[]} for r in ('origin','destination')},
        on_route_page=True,observed_public_transit=True,selected_route=False,
        route_inputs={'origin':'我的位置','destination':'广州塔地铁站'},
        route_inputs_match={'origin':False,'destination':True},
        route_correction={'reason_code':'route_endpoint_mismatch','executed':False})
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}}
    client.decide(task,shot,[{'action':'Tap','params':{'element':[500,350]},'result':'rejected'}],Mock())
    messages=create.call_args.kwargs['messages'];payload=sent_payload(messages[-1]['content'])
    assert payload['current_goal'].startswith('当前起点显示“我的位置”')
    assert '上次路线提案没有执行' in payload['current_goal']
    assert '不要把被拒绝的方案当作已选中' in payload['current_goal']
    assert payload['actual_execution_history']==[]
    assert not any(m['role']=='assistant' for m in messages)


def test_unrecognized_target_feedback_keeps_the_old_point_unexecuted_and_current_hints_factual(tmp_path):
    client,create,_=setup_client(tmp_path,Stream('do(action="Type", text="体育西路地铁站")'))
    shot=frame()
    hints=[{'text':'体育西路地铁站','role':'place','bounds':[60,700,400,780]},
           {'text':'搜索地点','role':'query','bounds':[185,154,951,313]}]
    correction={'reason_code':'unrecognized_target','executed':False,'proposed_point':[185,265]}
    shot['screen_info'].update(
        place_status={'origin':{'missing_fields':['name','city']},'destination':{'missing_fields':['name','city']}},
        native_control_hints=hints,target_correction=correction,
        focused_input={'role':'query','current_text':'','allowed_texts':['体育西路地铁站'],'already_matches':False})
    client.decide({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,[],Mock())
    messages=create.call_args.kwargs['messages'];payload=sent_payload(messages[-1]['content'])
    goal=payload['current_goal']
    assert goal.startswith('当前目标是核对出发地“体育西路地铁站”')
    assert '上次坐标[185, 265]未通过当前原生控件核对，动作没有执行' in goal
    assert '不要重复该未验证点' in goal and '不要虚构点击已经完成' in goal
    assert 'native_control_hints' in goal and '可用 Type 输入允许的文字' in goal
    assert 'Take_over 请求协助' in goal and '执行前仍会独立复核' in goal
    assert payload['screen_info']['target_correction']==correction
    assert payload['screen_info']['native_control_hints']==hints
    assert payload['actual_execution_history']==[]
    assert [m['role'] for m in messages]==['system','user']


def test_length_termination_is_typed_logged_safely_and_never_parsed_or_retried(tmp_path,monkeypatch):
    from autoglm.client import IncompleteResponse
    parse=Mock(side_effect=AssertionError('truncated output must not reach parser'))
    monkeypatch.setattr('autoglm.client.parse_action',parse)
    client,create,stream=setup_client(tmp_path,Stream('<think>private-truncated-thought</think>do(action="Back")','length'))
    with pytest.raises(IncompleteResponse):client.decide({},frame(),[],Mock())
    assert stream.closed and create.call_count==1
    parse.assert_not_called()
    record=json.loads((tmp_path/'autoglm-model-calls.jsonl').read_text())
    assert record['finish_reason']=='length' and record['error_type']=='IncompleteResponse'
    assert record['ok'] is False and 'action_kind' not in record
    assert 'private-truncated-thought' not in json.dumps(record)


@pytest.mark.parametrize('reason',['content_filter','tool_calls','private-finish-reason',{'private':'reason'}])
def test_other_provider_terminations_remain_hard_errors_with_sanitized_finish_metadata(tmp_path,reason):
    from autoglm.client import IncompleteResponse
    client,_,_=setup_client(tmp_path,Stream('do(action="Back")',reason))
    with pytest.raises(WorkflowError) as caught:client.decide({},frame(),[],Mock())
    assert not isinstance(caught.value,IncompleteResponse)
    record=json.loads((tmp_path/'autoglm-model-calls.jsonl').read_text())
    assert record['finish_reason']==(reason if reason in ('content_filter','tool_calls') else 'unknown')
    assert 'private' not in json.dumps(record)


@pytest.mark.parametrize('second_reason',['stop','length'])
def test_duplicate_finish_after_a_stop_is_never_a_retryable_incomplete_response(tmp_path,second_reason):
    from autoglm.client import IncompleteResponse
    stream=Stream()
    stream.parts.append(NS(choices=[NS(delta=NS(content=None),finish_reason=second_reason)]))
    client,create,_=setup_client(tmp_path,stream)
    with pytest.raises(WorkflowError) as caught:client.decide({},frame(),[],Mock())
    assert not isinstance(caught.value,IncompleteResponse)
    assert create.call_count==1 and stream.closed


def test_model_correction_preserves_one_body_and_never_creates_executed_history(tmp_path):
    client,create,_=setup_client(tmp_path)
    shot=frame();shot['screen_info']['model_correction']={'reason_code':'length','executed':False}
    client.decide({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,[],Mock())
    messages=create.call_args.kwargs['messages'];payload=sent_payload(messages[-1]['content'])
    assert '上次回复不完整、未执行，请只输出一个完整简短协议动作' in payload['current_goal']
    assert payload['actual_execution_history']==[] and not any(m['role']=='assistant' for m in messages)
    assert payload['screen_info']['model_correction']=={'reason_code':'length','executed':False}


@pytest.mark.parametrize('finish_unterminated', [True, False])
def test_unterminated_finish_gets_specific_safe_format_feedback(tmp_path,finish_unterminated):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info']['model_correction']={
        'reason_code':'model_protocol_invalid','executed':False,'finish_unterminated':finish_unterminated}
    goal=sent_payload(client.messages({},shot,[])[-1]['content'])['current_goal']
    assert ('finish(message="ready")' in goal) is finish_unterminated
    if finish_unterminated:
        assert '其他动作' in goal and '额外换行' in goal and '不代表路线已验证' in goal


def test_invalid_swipe_feedback_explains_range_without_suggesting_coordinates(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=frame();shot['screen_info']['model_correction']={
        'reason_code':'model_protocol_invalid','executed':False,'invalid_swipe_coordinates':True}
    goal=sent_payload(client.messages({},shot,[])[-1]['content'])['current_goal']
    assert '不能用 1000 或小数' in goal and '请自行决定是否滑动' in goal


def pending_station_shot():
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'name':'体育西路(地铁站)','missing_fields':[]},
                      'destination':{'name':'广州塔(地铁站)','missing_fields':[]}},
        on_route_page=True,selected_route=False,route_inputs={},observed_public_transit=False,
        focused_input={'role':'origin','current_text':'体育西路地铁站',
                       'allowed_texts':['体育西路地铁站'],'already_matches':True},
        native_control_hints=[{'role':'place','text':'体育西路(地铁站)','bounds':[40,700,1100,820]}])
    return shot


def test_typed_route_endpoint_waits_for_the_unique_verified_main_station_candidate(tmp_path):
    client,_,_=setup_client(tmp_path)
    shot=pending_station_shot()
    parts=client.messages({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,[])[-1]['content']
    goal=sent_payload(parts)['current_goal']
    assert goal.startswith('当前起点文字已经正确')
    assert '选择已核对的主站点“体育西路(地铁站)”确认起点' in goal
    assert '不要选择站口' in goal and '候选确认完成前暂不选路线方案' in goal
    assert 'element=' not in goal


@pytest.mark.parametrize('mutation',['wrong_role','text_not_ready','no_hint','station_exit',
                                     'duplicate_hint','poi_incomplete','summary_inputs_present','not_route_page'])
def test_pending_station_goal_requires_all_current_native_and_place_facts(tmp_path,mutation):
    client,_,_=setup_client(tmp_path);shot=pending_station_shot();info=shot['screen_info']
    if mutation=='wrong_role':info['focused_input']['role']='query'
    elif mutation=='text_not_ready':info['focused_input']['already_matches']=False
    elif mutation=='no_hint':info['native_control_hints']=[]
    elif mutation=='station_exit':info['native_control_hints'][0]['text']='体育西路(地铁站)B口'
    elif mutation=='duplicate_hint':info['native_control_hints']*=2
    elif mutation=='poi_incomplete':info['place_status']['destination']['missing_fields']=['city']
    elif mutation=='summary_inputs_present':info['route_inputs']={'origin':'我的位置'}
    else:info['on_route_page']=False
    payload=sent_payload(client.messages({'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站'}},shot,[])[-1]['content'])
    assert '选择已核对的主站点' not in payload['current_goal']


def verified_other_place_shot():
    shot=frame();shot['screen_info'].update(
        place_status={'origin':{'name':'体育西路(地铁站)','city':'广州市','missing_fields':[]},
                      'destination':{'missing_fields':['name','city']}},
        current_place={'role':'origin','name':'体育西路(地铁站)'},
        page_kind='place_detail',
        native_control_hints=[],available_navigation_actions=['Back'])
    return shot


def test_verified_current_poi_with_no_unique_coordinate_target_can_use_native_back(tmp_path):
    client,create,_=setup_client(tmp_path)
    shot=verified_other_place_shot()
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    client.decide(task,shot,[],Mock())
    messages=create.call_args.kwargs['messages'];payload=sent_payload(messages[-1]['content'])
    goal=payload['current_goal']
    assert goal.startswith('已核对当前出发地“体育西路(地铁站)”详情，下一目标是目的地“广州塔地铁站”')
    assert '请使用非坐标 Back 返回' in goal
    assert '根据返回后的新截图查找目标地点' in goal
    assert '不要猜测工具栏按钮或沿用旧坐标' in goal
    assert 'element=' not in goal and 'do(action=' not in goal
    assert payload['screen_info']['current_place']==shot['screen_info']['current_place']
    assert payload['actual_execution_history']==[]
    assert [m['role'] for m in messages]==['system','user']


def verified_current_search_hint():
    return {'role':'query','text':'搜索框，体育西路(地铁站)',
            'bounds':[194,154,894,313],'normalized_bounds':[162,59,744,118]}


def test_verified_other_place_exposes_unique_current_search_control_as_fact(tmp_path):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot()
    shot['screen_info']['native_control_hints']=[
        {'role':'back','text':'返回','normalized_bounds':[55,74,120,103]}, verified_current_search_hint()]
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    payload=sent_payload(client.messages(task,shot,[])[-1]['content'])
    goal=payload['current_goal']
    assert '当前已核对的搜索框显示“体育西路(地铁站)”' in goal
    assert '[162,59]至[744,118]' in goal and '自行决定动作和位置' in goal
    assert '暂不查询路线' in goal and '请使用非坐标 Back 返回' not in goal
    assert 'do(action=' not in goal and 'element=' not in goal
    assert payload['actual_execution_history']==[]


def test_rejected_route_feedback_is_bound_to_the_current_verified_search_control(tmp_path):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot();info=shot['screen_info']
    info['native_control_hints']=[verified_current_search_hint()]
    info['evidence_correction']={'missing_roles':['destination'],'missing_fields':{'destination':['name','city']}}
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert '上次路线入口提案未执行' in goal
    assert '目的地“广州塔地铁站”仍缺少本轮详情核对' in goal
    assert '不要再次点击路线入口' in goal
    assert '搜索框范围[162,59]至[744,118]自行确定位置' in goal
    assert '其他安全操作或请求协助' in goal
    assert 'do(action=' not in goal and 'element=' not in goal


@pytest.mark.parametrize('mutation',['no_correction','other_role','no_search','duplicate_search','foreign_app','wrong_page'])
def test_rejected_route_search_feedback_requires_matching_current_evidence(tmp_path,mutation):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot();info=shot['screen_info']
    info['native_control_hints']=[verified_current_search_hint()]
    info['evidence_correction']={'missing_roles':['destination'],'missing_fields':{'destination':['name','city']}}
    if mutation=='no_correction':info.pop('evidence_correction')
    elif mutation=='other_role':info['evidence_correction']['missing_roles']=['origin']
    elif mutation=='no_search':info['native_control_hints']=[]
    elif mutation=='duplicate_search':info['native_control_hints']*=2
    elif mutation=='foreign_app':shot['current_app']='other.app'
    elif mutation=='wrong_page':info['page_kind']='route_list'
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert '上次路线入口提案未执行，目的地' not in goal


@pytest.mark.parametrize('mutation',['missing','duplicate','wrong_role','wrong_name','missing_prefix',
                                     'bad_bounds','foreign_app','wrong_page','different_poi',
                                     'incomplete_poi','no_back'])
def test_search_control_fact_requires_unique_current_trusted_context(tmp_path,mutation):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot();info=shot['screen_info']
    hint=verified_current_search_hint();info['native_control_hints']=[hint]
    if mutation=='missing': info['native_control_hints']=None
    elif mutation=='duplicate': info['native_control_hints'].append(dict(hint))
    elif mutation=='wrong_role':hint['role']='place'
    elif mutation=='wrong_name':hint['text']='搜索框，广州塔(地铁站)'
    elif mutation=='missing_prefix':hint['text']='体育西路(地铁站)'
    elif mutation=='bad_bounds':hint['normalized_bounds']=[162,59,1000,118]
    elif mutation=='foreign_app':shot['current_app']='other.app'
    elif mutation=='wrong_page':info['page_kind']='route_list'
    elif mutation=='different_poi':info['current_place']['name']='其他地点'
    elif mutation=='incomplete_poi':info['place_status']['origin']['missing_fields']=['city']
    elif mutation=='no_back':info['available_navigation_actions']=[]
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert '当前已核对的搜索框显示' not in goal


@pytest.mark.parametrize('mutation',['missing_city','wrong_city','mismatched_name','same_role',
                                     'unknown_role','current_incomplete','foreign_app','unknown_page',
                                     'missing_page_kind','wrong_page_kind'])
def test_poi_transition_does_not_claim_false_or_unknown_current_place_facts(tmp_path,mutation):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot();info=shot['screen_info']
    if mutation=='missing_city':info['place_status']['origin']['city']=None
    elif mutation=='wrong_city':info['place_status']['origin']['city']='深圳市'
    elif mutation=='mismatched_name':info['current_place']['name']='其他地点'
    elif mutation=='same_role':info['current_place']['role']='destination'
    elif mutation=='unknown_role':info['current_place']['role']='unknown'
    elif mutation=='current_incomplete':info['place_status']['origin']['missing_fields']=['city']
    elif mutation=='foreign_app':shot['current_app']='com.example.other'
    elif mutation=='missing_page_kind':info.pop('page_kind')
    elif mutation=='wrong_page_kind':info['page_kind']='route_list'
    elif mutation=='unknown_page':info['page_kind']='unknown'
    else:info['available_navigation_actions']=[]
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert '已核对当前' not in goal and '请使用非坐标 Back 返回' not in goal


@pytest.mark.parametrize('hints',[
    None,[{'role':'back','text':'返回','bounds':[10,150,80,220]}]*2,
    [{'role':'query','text':'搜索','bounds':[100,150,1000,260]}],
])
def test_back_navigation_advice_is_only_for_an_explicitly_empty_current_hint_list(tmp_path,hints):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot()
    shot['screen_info']['native_control_hints']=hints
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert goal.startswith('已核对当前出发地')
    assert '请使用非坐标 Back 返回' not in goal


def test_no_back_capability_preserves_normal_missing_place_goal(tmp_path):
    client,_,_=setup_client(tmp_path);shot=verified_other_place_shot()
    shot['screen_info']['available_navigation_actions']=[]
    task={'request':{'origin':'体育西路地铁站','destination':'广州塔地铁站','city':'广州市'}}
    goal=sent_payload(client.messages(task,shot,[])[-1]['content'])['current_goal']
    assert goal.startswith('当前目标是核对目的地“广州塔地铁站”')
    assert 'Back' not in goal


@pytest.mark.parametrize('raw,code',[
    ('<think>private-model-thought</think><answer>do(action="Type", text="sk-private-value\\n")</answer>',
     'invalid_text'),
    ('do(action="Back", sk_private_keyword="sk-private-value")','invalid_arguments'),
    ('do(action="Tap", element=[1000,20])','invalid_coordinates'),
])
def test_rejected_protocol_diagnostics_are_fixed_metadata_and_never_raw_response(tmp_path,raw,code):
    client,create,stream=setup_client(tmp_path,Stream(raw))
    with pytest.raises(ProtocolError):client.decide({},frame(),[],Mock())
    assert create.call_count==1 and stream.closed
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text();record=json.loads(log)
    assert record['protocol_error_code']==code and record['error_type']=='ProtocolError'
    assert record['ok'] is False and record['finish_reason']=='stop' and 'action_kind' not in record
    assert 'private' not in log and 'sk_private_keyword' not in log and 'sk-' not in log


def test_custom_protocol_error_names_and_unknown_codes_are_not_logged(tmp_path,monkeypatch):
    custom_type=type('sk_private_custom_error',(ProtocolError,),{})
    error=custom_type('sk-private-message')
    error.reason_code='sk-private-unapproved-code'
    monkeypatch.setattr('autoglm.client.parse_action',Mock(side_effect=error))
    client,create,_=setup_client(tmp_path)
    with pytest.raises(ProtocolError):client.decide({},frame(),[],Mock())
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text();record=json.loads(log)
    assert record['error_type']=='ProtocolError' and 'protocol_error_code' not in record
    assert 'private' not in log and 'sk-' not in log and create.call_count==1


@pytest.mark.parametrize('raw,category,action',[
    ('<think>private-thought</think><answer>do(action="Tap", element=[10,20]</answer>', 'unclosed_delimiter', 'Tap'),
    ('do(action="Type", text="private-secret)', 'unterminated_string', 'Type'),
    ('finish(message="private-secret"))', 'unmatched_delimiter', 'Finish'),
    ('do(action="private-secret", unexpected=)', 'other', 'unknown'),
])
def test_invalid_syntax_logs_only_fixed_categories_without_thought_or_arguments(tmp_path,raw,category,action):
    client,_,_=setup_client(tmp_path,Stream(raw))
    with pytest.raises(ProtocolError):client.decide({},frame(),[],Mock())
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text();record=json.loads(log)
    assert record['protocol_syntax_category']==category and record['protocol_action_hint']==action
    assert 'private' not in log and 'secret' not in log and 'unexpected' not in log


def test_arbitrary_syntax_diagnostic_values_are_never_logged(tmp_path,monkeypatch):
    error=ProtocolError('invalid_syntax')
    error.syntax_category='private-secret';error.syntax_action='private-secret'
    monkeypatch.setattr('autoglm.client.parse_action',Mock(side_effect=error))
    client,_,_=setup_client(tmp_path)
    with pytest.raises(ProtocolError):client.decide({},frame(),[],Mock())
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text()
    assert 'private' not in log and 'secret' not in log


@pytest.mark.parametrize('raw,ending',[
    ('finish(message="secret-one\nsecret-two")', 'linebreak'),
    ('finish(message="secret-one', 'end_of_response'),
])
def test_unterminated_finish_logs_only_fixed_line_end_metadata(tmp_path,raw,ending):
    client,_,_=setup_client(tmp_path,Stream(raw))
    with pytest.raises(ProtocolError):client.decide({},frame(),[],Mock())
    log=(tmp_path/'autoglm-model-calls.jsonl').read_text();record=json.loads(log)
    assert record['protocol_syntax_category']=='unterminated_string'
    assert record['protocol_action_hint']=='Finish' and record['protocol_string_end']==ending
    assert 'secret' not in log


def test_finish_system_instruction_uses_a_short_marker_and_independent_verification(tmp_path):
    client,_,_=setup_client(tmp_path)
    system=client.messages({},frame(),[])[0]['content']
    assert 'message 不要包含换行或站名' in system
    assert 'finish(message="ready")' in system
    assert '如果你根据截图判断需要继续查看，就继续选择其他动作' in system
    assert '系统从本次真实页面独立采集和核对' in system


def test_protocol_failure_hard_stops_the_offline_executor_without_dispatch_or_retry(tmp_path):
    # Exercise the real executor with local screen/device doubles; no phone or
    # external API is accessed, and this is not live acceptance evidence.
    from test_autoglm_executor import create as create_executor, REQUEST
    client,provider,_=setup_client(tmp_path,Stream('do(action="Back", sk_private_keyword="private-value")'))
    executor,phone=create_executor(tmp_path,stage=1)
    original_observe=executor._agent_observe
    def observe(checkpoint):
        shot=original_observe(checkpoint)
        shot['screenshot_base64']=frame(width=shot['width'],height=shot['height'])['screenshot_base64']
        return shot
    executor._agent_observe=observe
    executor.client=client
    with pytest.raises(ProtocolError):executor.query(REQUEST,'offline-task','offline-invalid-protocol',lambda:None)
    assert provider.call_count==1 and phone.effects==[] and executor.history==[]
    actions=executor.folder/'autoglm-actions.jsonl'
    assert not actions.exists() or all(json.loads(row)['status'] not in {'dispatching','executed'}
                                      for row in actions.read_text().splitlines())
    record=json.loads((executor.folder/'autoglm-model-calls.jsonl').read_text())
    assert record['protocol_error_code']=='invalid_arguments'
