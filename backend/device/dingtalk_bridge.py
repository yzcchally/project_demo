"""DUMP-protected, read-only current DingTalk accessibility window."""
import base64
import os
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET

from device.android_app import android_package
from domain import WorkflowError

PACKAGE = 'com.alibaba.android.rimet'


class TransientRead(WorkflowError):
    """A foreground window is changing; safe only for bounded read retry."""


def observe(checkpoint=lambda: None):
    """Return a fresh protected foreground tree; never use cached XML or UiAutomation."""
    import gmail_demo
    checkpoint()
    nonce = uuid.uuid4().hex
    command = [gmail_demo._adb()]
    if os.environ.get('ANDROID_SERIAL'):
        command += ['-s', os.environ['ANDROID_SERIAL']]
    command += ['shell', 'content', 'call', '--uri',
                f'content://{android_package()}.dingtalk', '--method', 'dump_dingtalk_ui', '--arg', nonce]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=9)
    except (OSError, subprocess.TimeoutExpired) as exc:
        checkpoint()
        raise WorkflowError('钉钉界面读取不可用，请检查手机连接及无障碍服务') from exc
    checkpoint()
    matches = re.findall(r'Bundle\[\{([^\r\n]*)\}\]', result.stdout or '')
    if result.returncode or len(matches) != 1:
        raise WorkflowError('钉钉界面读取失败，已停止')
    fields = {}
    for entry in matches[0].split(', '):
        key, sep, value = entry.partition('=')
        if not sep or key in fields or key not in {'code', 'result', 'reason', 'nonce', 'data'}:
            raise WorkflowError('钉钉界面响应格式无效')
        fields[key] = value
    if (fields.get('nonce') == nonce and fields.get('result') == '0' and fields.get('code') == '0'
            and fields.get('reason') in {'root_unavailable', 'tree_changed'}):
        raise TransientRead('钉钉窗口正在变化，暂无法读取')
    if fields.get('nonce') != nonce or fields.get('result') != '1' or fields.get('code') != '1' or fields.get('reason') != 'ok':
        raise WorkflowError('钉钉未处于可安全读取的前台页面，或无障碍读取失败')
    try:
        raw = base64.b64decode(fields['data'], altchars=b'-_', validate=True)
        if len(raw) > 350_000:
            raise ValueError('oversized')
        xml = raw.decode('utf-8')
        root = ET.fromstring(xml)
    except (ValueError, KeyError, UnicodeError, ET.ParseError) as exc:
        raise WorkflowError('钉钉界面响应无效') from exc
    nodes = list(root.iter('node'))
    if (root.tag != 'hierarchy' or root.get('nonce') != nonce or not root.get('window-id')
            or not nodes or len(nodes) > 2500
            or nodes[0].get('package') != PACKAGE
            or any(n.get('package') not in ('', None, PACKAGE) for n in nodes)):
        raise WorkflowError('钉钉界面来源或窗口无效')
    checkpoint()
    return xml
