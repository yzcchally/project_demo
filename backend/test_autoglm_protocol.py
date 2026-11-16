"""The remote action language is parsed as data; it cannot execute Python."""
import pytest

from autoglm.protocol import Action, ProtocolError, parse_action


@pytest.mark.parametrize('raw,expected', [
    ('do(action="Launch", app="高德地图")', Action('Launch', {'app': '高德地图'})),
    ('do(action="Tap", element=[0,999])', Action('Tap', {'element': [0,999]})),
    ('do(action="Type", text="广州市体育西路地铁站")', Action('Type', {'text': '广州市体育西路地铁站'})),
    ('do(action="Swipe", start=[500,800], end=[500,300])', Action('Swipe', {'start':[500,800], 'end':[500,300]})),
    ('do(action="Back")', Action('Back', {})),
    ('do(action="Wait", duration="2 seconds")', Action('Wait', {'duration':2.0})),
    ('do(action="Wait", duration=0.5)', Action('Wait', {'duration':.5})),
    ('finish(message="交由系统核对路线")', Action('Finish', {'message':'交由系统核对路线'})),
    ('do(action="Take_over", message="需要用户登录")', Action('Help', {'message':'需要用户登录'})),
    ('do(action="Interact")', Action('Help', {'message':'需要用户核对或选择地点'})),
    ('<think>当前需返回。不会把这段思考保存到日志。</think><answer>do(action="Back")</answer>', Action('Back', {})),
    ('<answer>do(action="Back")', Action('Back', {})),
    ('当前页面需要返回。\ndo(action="Back")', Action('Back', {})),
])
def test_supported_official_forms_are_typed_single_actions(raw, expected):
    assert parse_action(raw) == expected


@pytest.mark.parametrize('raw', [
    '', 'some explanation only', 'do(action="Back"',
    'do(action="Tap", element=[500])', 'do(action="Tap", element=[0,1000])',
    'do(action="Tap", element=[-1,10])', 'do(action="Tap", element=[True,10])',
    'do(action="Tap", element=[1.0,10])', 'do(action="Tap", element=(1,10))',
    'do(action="Tap", element=[1,2,3])', 'do(action="Tap", element=[1,2], role="route")',
    'do(action="Type", text="a", text="b")', 'do(action="Type", text="")',
    'do(action="Type", text="a\\nb")', 'do(action="Wait", duration="999 seconds")',
    'do(action="Wait", duration=True)', 'do(action="Wait", duration="NaN seconds")',
    'do(action="Swipe", start=[1,2], end=[1,2])',
    'do(action="Launch", app="微信")', 'do(action="Home")',
    'do(action="Call_API", instruction="anything")',
    'finish(message="done", route_verified=True)',
    'do(action="Back"); do(action="Back")',
    '(do(action="Back"), finish(message="done"))',
    '<answer>do(action="Back")</answer><answer>finish(message="done")</answer>',
    'do(action="Back")\nfinish(message="done")',
    'do(action="Back") # trailing unexpected output',
    '<think>truncated thought<answer>do(action="Back")',
    'do(action="Type", text=__import__("os").getcwd())',
    '__import__("os").system("touch /tmp/not-allowed")',
    'do(**{"action":"Back"})',
    'do("Back")',
])
def test_malformed_multiple_unsupported_and_executable_responses_are_rejected(raw):
    with pytest.raises(ProtocolError):
        parse_action(raw)


def test_python_like_input_is_data_or_rejected_never_executed(tmp_path):
    marker = tmp_path / 'untrusted-marker'
    with pytest.raises(ProtocolError):
        parse_action(f'do(action="Type", text=__import__("pathlib").Path({str(marker)!r}).touch())')
    assert not marker.exists()


def test_output_size_and_input_length_are_bounded():
    with pytest.raises(ProtocolError):
        parse_action('do(action="Type", text="' + 'x' * 501 + '")')
    with pytest.raises(ProtocolError):
        parse_action('<think>' + 'x' * 64000 + '</think><answer>do(action="Back")</answer>')


@pytest.mark.parametrize('raw,code',[
    ('<think>private-thought-fixture','invalid_envelope'),
    ('do(action="Type", text="private-fixture"','invalid_syntax'),
    ('do(action="Back") # private-fixture','invalid_call'),
    ('do(action="Back", sk_private_keyword="private-value")','invalid_arguments'),
    ('do(action="Home")','unsupported_action'),
    ('do(action="Tap", element=[1000,20])','invalid_coordinates'),
    ('do(action="Type", text="private-value\\n")','invalid_text'),
])
def test_protocol_rejections_expose_only_fixed_safe_reason_categories(raw,code):
    from autoglm.protocol import PROTOCOL_REASON_CODES
    with pytest.raises(ProtocolError) as caught:parse_action(raw)
    assert caught.value.reason_code==code and code in PROTOCOL_REASON_CODES
    assert 'private' not in str(caught.value) and 'sk_' not in str(caught.value)


@pytest.mark.parametrize('value',['sk-private-error-fixture',{'private':'code'},None])
def test_protocol_error_construction_never_embeds_custom_reason_values(value):
    from autoglm.protocol import PROTOCOL_REASON_CODES
    error=ProtocolError(value)
    assert error.reason_code in PROTOCOL_REASON_CODES
    assert 'private' not in str(error) and 'sk-' not in str(error)
