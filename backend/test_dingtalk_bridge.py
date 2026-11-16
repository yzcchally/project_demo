import base64
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from domain import WorkflowError
from device.dingtalk_bridge import observe, TransientRead


def response(command, *, package='com.alibaba.android.rimet', xml_nonce=None, response_nonce=None):
    nonce = command[-1]
    xml = (f'<hierarchy nonce="{xml_nonce or nonce}" window-id="1"><node '
           f'package="{package}" bounds="[0,0][100,100]" /></hierarchy>')
    data = base64.urlsafe_b64encode(xml.encode()).decode()
    return SimpleNamespace(returncode=0, stdout=f'Bundle[{{code=1, result=1, reason=ok, nonce={response_nonce or nonce}, data={data}}}]')


def test_observe_validates_and_returns_fresh_current_tree():
    checkpoints = []
    with patch('subprocess.run', side_effect=lambda command, **kw: response(command)):
        xml = observe(lambda: checkpoints.append(True))
    assert 'com.alibaba.android.rimet' in xml
    assert len(checkpoints) >= 3


@pytest.mark.parametrize('kwargs', [dict(package='com.other.app'), dict(xml_nonce='old'), dict(response_nonce='old')])
def test_observe_rejects_wrong_package_or_old_nonce(kwargs):
    with patch('subprocess.run', side_effect=lambda command, **kw: response(command, **kwargs)):
        with pytest.raises(WorkflowError): observe()


def test_timeout_fails_closed():
    with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('adb', 9)):
        with pytest.raises(WorkflowError): observe()


@pytest.mark.parametrize('reason', ['root_unavailable', 'tree_changed'])
def test_only_authenticated_window_transition_reasons_are_transient(reason):
    def reply(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=f'Bundle[{{code=0, result=0, reason={reason}, nonce={command[-1]}, data=null}}]')
    with patch('subprocess.run', side_effect=reply):
        with pytest.raises(TransientRead): observe()


def test_other_or_stale_bridge_failure_is_not_transient():
    for reason, nonce in [('service_unavailable', None), ('tree_changed', 'stale')]:
        def reply(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=f'Bundle[{{code=0, result=0, reason={reason}, nonce={nonce or command[-1]}, data=null}}]')
        with patch('subprocess.run', side_effect=reply):
            with pytest.raises(WorkflowError) as error: observe()
            assert not isinstance(error.value, TransientRead)
