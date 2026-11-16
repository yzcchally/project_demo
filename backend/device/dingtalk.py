"""One-shot DingTalk sharing of a verified route to the current user's file assistant.

No model selects the recipient or the send button. The DingTalk foreground tree is
read through the protected accessibility provider and every click is freshly bound.
"""
import hashlib
import hmac
import json
import math
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from domain import WorkflowError
from device import dingtalk_bridge

PACKAGE = 'com.alibaba.android.rimet'
_SELF = re.compile(r'^我[（(](.+)[）)]$')


def recipient_digest(recipient):
    if not isinstance(recipient, str) or not recipient.strip():
        raise WorkflowError('钉钉本人会话显示名不可核对')
    return hashlib.sha256(('钉钉文件助手\0' + recipient.strip()).encode('utf-8')).hexdigest()


def _self_chat_recipient(root, parents, expected_digest):
    if not isinstance(expected_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_digest):
        raise WorkflowError('缺少发送时的钉钉本人会话显示名摘要，不能自动核验')
    titles = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/tv_title'
              and _visible(n, parents) and _bounds(n)]
    if len(titles) != 1:
        raise WorkflowError('钉钉本人会话标题不可唯一核对')
    match = _SELF.fullmatch(_text(titles[0]))
    if not match or not hmac.compare_digest(recipient_digest(match.group(1)), expected_digest):
        raise WorkflowError('钉钉当前本人会话显示名与发送时不一致')
    return match.group(1).strip()


def _visible(node, parents):
    while node is not None:
        if node.get('visible-to-user') == 'false' or node.get('enabled') == 'false' or node.get('package') not in ('', None, PACKAGE):
            return False
        node = parents.get(node)
    return True


def _bounds(node):
    m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
    if not m: return None
    a, b, c, d = map(int, m.groups())
    return (a, b, c, d) if a < c and b < d else None


def _text(node):
    return (node.get('text') or node.get('content-desc') or '').strip()


def _parse(xml):
    try: root = ET.fromstring(xml)
    except (ET.ParseError, TypeError) as exc: raise WorkflowError('钉钉界面无效，已停止') from exc
    if root.tag != 'hierarchy' or not root.get('window-id'): raise WorkflowError('钉钉窗口不可核对')
    parents = {child: p for p in root.iter('node') for child in p}
    return root, parents


def _unique_label(root, parents, label):
    items = [n for n in root.iter('node') if _text(n) == label and _visible(n, parents) and _bounds(n)]
    if len(items) != 1: raise WorkflowError('钉钉当前页面的目标不唯一，已停止')
    return items[0]


def _self_target(root, parents):
    matches = []
    for n in root.iter('node'):
        match = _SELF.fullmatch(_text(n))
        if not match or not _visible(n, parents) or not _bounds(n): continue
        parent = parents.get(n)
        # Require a common bounded row containing the native file-assistant subtitle.
        for _ in range(4):
            if parent is None: break
            subtitles = [x for x in parent.iter('node') if '文件助手' in _text(x) and _visible(x, parents)]
            other_self = [x for x in parent.iter('node') if _SELF.fullmatch(_text(x)) and _visible(x, parents)]
            sb = _bounds(subtitles[0]) if len(subtitles) == 1 else None
            nb = _bounds(n)
            # The file-assistant caption must actually sit on the same row,
            # not merely elsewhere under a common full-screen ancestor.
            nearby = sb and nb and 0 <= sb[1] - nb[1] <= 3 * (nb[3] - nb[1]) and abs(sb[0] - nb[0]) <= 2 * (nb[3] - nb[1])
            if nearby and len(other_self) == 1 and _bounds(parent):
                matches.append((n, match.group(1).strip())); break
            parent = parents.get(parent)
    if len(matches) != 1 or not matches[0][1]: raise WorkflowError('未找到唯一可确认的钉钉本人文件助手')
    return matches[0]


def _home_self_target(root, parents, recipient):
    """Select only the already-identified self session on DingTalk's home list."""
    matches = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/session_title'
               and _text(n) in {f'我（{recipient}）', f'我({recipient})'}
               and _visible(n, parents) and _bounds(n)]
    if len(matches) != 1: raise WorkflowError('钉钉首页的本人会话不可唯一核对')
    node = matches[0]
    row = parents.get(node)
    for _ in range(4):
        if row is None: break
        if (row.get('resource-id') == PACKAGE + ':id/session_item' and row.get('clickable') == 'true'
                and _visible(row, parents) and _bounds(row)):
            a, b, c, d = _bounds(node); x, y, z, w = _bounds(row)
            if x <= a < c <= z and y <= b < d <= w:
                return node
        row = parents.get(row)
    raise WorkflowError('钉钉本人会话不在可核对的聊天行')


def _home_digest_target(root, parents, expected_digest):
    matches = []
    for n in root.iter('node'):
        if n.get('resource-id') != PACKAGE + ':id/session_title' or not _visible(n, parents) or not _bounds(n):
            continue
        m = _SELF.fullmatch(_text(n))
        if m and hmac.compare_digest(recipient_digest(m.group(1)), expected_digest):
            matches.append(m.group(1).strip())
    if len(matches) != 1:
        raise WorkflowError('钉钉首页缺少唯一的原本人会话')
    return _home_self_target(root, parents, matches[0])


def _confirmation(root, parents, recipient, content):
    panels = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/parentPanel'
              and _visible(n, parents) and _bounds(n)]
    if len(panels) != 1: raise WorkflowError('钉钉发送确认窗口不可唯一核对')
    panel = panels[0]
    _unique_label(panel, parents, '发送给：')
    _unique_label(panel, parents, recipient)
    # Complete text, not a prefix or an ellipsized preview, must be available
    # to the independent native tree. The prebuilt Intent alone is not proof.
    _unique_label(panel, parents, content)
    return _unique_label(panel, parents, '发送')


def _verified_self_message(root, parents, recipient, content, query_id):
    """Prove the exact unique message and its explicit status in this self-chat.

    A visible bubble without a native success label can also be a queued or
    failed send. It is therefore not enough to establish a successful send.
    """
    marker = f'[SilverGuide路线:{query_id}]'
    if marker not in content or content.count(marker) != 1:
        return False
    # A share confirmation overlay must never be mistaken for the actual chat,
    # even on Android versions exposing the covered tree underneath it.
    if any(n.get('resource-id') == PACKAGE + ':id/parentPanel' and _visible(n, parents) and _bounds(n)
           for n in root.iter('node')):
        return False
    titles = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/tv_title'
              and _visible(n, parents) and _bounds(n)]
    if len(titles) != 1 or _text(titles[0]) not in {f'我（{recipient}）', f'我({recipient})'}:
        return False
    msgs = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/chatting_content_tv'
            and _visible(n, parents) and _bounds(n) and _text(n) == content]
    if len(msgs) != 1:
        return False
    parent = parents.get(msgs[0])
    for _ in range(5):
        if parent is None: return False
        if parent.get('resource-id') == PACKAGE + ':id/chatting_content_view_container':
            labels = [n for n in parent.iter('node') if _visible(n, parents) and _text(n)]
            bodies = [n for n in labels if n.get('resource-id') == PACKAGE + ':id/chatting_content_tv']
            status_groups = [n for n in parent.iter('node') if _visible(n, parents)
                             and n.get('resource-id') == PACKAGE + ':id/ll_msg_status']
            if len(bodies) != 1 or bodies[0] is not msgs[0] or len(status_groups) != 1:
                return False
            status = [n for n in status_groups[0].iter('node') if _visible(n, parents)
                      and n.get('resource-id') == PACKAGE + ':id/chatting_unreadcount_tv1' and _text(n)]
            if any(_text(n) in {'发送失败', '发送中', '等待发送'} for n in labels):
                return False
            return len(status) == 1 and _text(status[0]) in {'已读', '已送达'}
        parent = parents.get(parent)
    return False


def _verified_number(value, limit, *, integer=False):
    """Keep only finite numeric facts already present in the verified route."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= limit or not math.isfinite(value) or integer and (not float(value).is_integer() or value == 0):
        return None
    return int(value) if float(value).is_integer() else value


def route_text(task_id, query_id, route, version=1):
    # v1 is intentionally byte-for-byte stable for earlier pending shares.
    if type(version) is not int or version not in (1, 2):
        raise WorkflowError('钉钉路线文本版本无效，不能自动核验或发送')
    if (not isinstance(route, dict) or route.get('task_id') != task_id or route.get('query_id') != query_id
            or route.get('source_app') != '高德地图' or route.get('classification') != 'COMPLETE'
            or route.get('route_verified') is not True or route.get('missing_fields') != []):
        raise WorkflowError('当前路线尚未完整核验，不能分享')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', query_id): raise WorkflowError('路线查询编号无效')
    steps = []
    for segment in route.get('segments') or []:
        if not isinstance(segment, dict): raise WorkflowError('路线分段无效')
        mode = segment.get('mode')
        if mode == 'walk':
            if version == 1:
                steps.append('步行：具体走法请查看高德地图')
            else:
                distance = _verified_number(segment.get('walking_meters'), 200_000)
                duration = _verified_number(segment.get('duration_minutes'), 1_440)
                facts = []
                if distance is not None:
                    facts.append(f'约{distance / 1000:g}公里' if distance >= 1000 else f'约{distance:g}米')
                if duration is not None:
                    facts.append(f'约{duration:g}分钟')
                steps.append('步行' + ('（' + '，'.join(facts) + '）' if facts else '（距离和时间未显示）') + '：具体走法请查看高德地图')
        elif mode in {'subway', 'bus'}:
            values = [segment.get(k) for k in ('line', 'board', 'alight', 'direction')]
            if any(not isinstance(v, str) or not v.strip() or len(v) > 100 or any(ord(c) < 32 for c in v) for v in values):
                raise WorkflowError('路线分段关键信息不完整')
            line, board, alight, direction = (v.strip() for v in values)
            if version == 2:
                direction = re.sub(r'^(?:开往|往)', '', direction).strip()
                if not direction: raise WorkflowError('路线方向不可核对')
            stops = _verified_number(segment.get('stops'), 300, integer=True) if version == 2 else None
            count = f'，乘坐{stops}站' if stops is not None else ''
            steps.append(f'{"地铁" if mode == "subway" else "公交"}{line}：{board}上车，往{direction}{count}，{alight}下车')
        else: raise WorkflowError('路线分段类型无效')
    if not steps or not any(s.get('mode') in {'subway', 'bus'} for s in route['segments']):
        raise WorkflowError('没有可核对的公交地铁乘车段')
    overview = []
    if version == 2:
        duration = _verified_number(route.get('duration_minutes'), 1_440)
        transfers = _verified_number(route.get('transfers'), 30)
        distance = _verified_number(route.get('walking_meters'), 200_000)
        fare = _verified_number(route.get('fare_yuan'), 10_000)
        if duration is not None: overview.append(f'预计{duration:g}分钟')
        if transfers is not None and float(transfers).is_integer(): overview.append(f'换乘{int(transfers)}次')
        if distance is not None: overview.append(f'总步行约{distance / 1000:g}公里' if distance >= 1000 else f'总步行约{distance:g}米')
        if fare is not None: overview.append(f'预计票价{fare:g}元')
    text = '高德地图已核对路线\n' + ('，'.join(overview) + '\n' if overview else '') + '\n'.join(steps) + f'\n[SilverGuide路线:{query_id}]'
    if len(text) > 2000: raise WorkflowError('路线过长，不能安全分享')
    return text


class DingTalkDevice:
    def __init__(self, base): self.base = base

    def _verify_diag(self, task_id, query_id, source, stage, outcome):
        """Best-effort local metadata: fixed enums only, never UI or exceptions."""
        sources = {'send_then_verify', 'verify_only'}
        stages = {'pre_send_revalidation', 'post_send_dispatch', 'post_send_verify',
                  'precondition', 'initial_read', 'launch_home', 'launch_observation',
                  'resumed_chat', 'home_first', 'home_recheck', 'home_click', 'chat_read', 'chat_identity', 'chat_proof'}
        outcomes = {'started', 'not_current', 'verified', 'unknown', 'failed',
                    'transient', 'home_pending', 'not_observed'}
        root = getattr(self.base, 'evidence_root', None)
        if (source not in sources or stage not in stages or outcome not in outcomes
                or not isinstance(root, (str, os.PathLike))
                or not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', task_id)
                or not isinstance(query_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', query_id)):
            return
        try:
            folder = Path(root) / task_id / 'transit' / query_id
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(folder / 'dingtalk-verify.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a', encoding='utf-8') as log:
                log.write(json.dumps({'source': source, 'stage': stage, 'outcome': outcome}, ensure_ascii=True) + '\n')
        except (OSError, ValueError):
            pass

    def _observe(self, checkpoint): return _parse(dingtalk_bridge.observe(checkpoint))

    def _click(self, node, checkpoint):
        box = _bounds(node)
        if box is None: raise WorkflowError('钉钉按钮坐标无效')
        checkpoint()
        self.base.tap(node, checkpoint)

    def _launch(self, text, checkpoint):
        # Never use HuaweiDevice.shell: it persists shell argv in actions.jsonl.
        import gmail_demo
        cmd = ['am', 'start', '-a', 'android.intent.action.SEND', '-t', 'text/plain',
               '-p', PACKAGE, '--es', 'android.intent.extra.TEXT', text]
        checkpoint()
        try:
            r = subprocess.run([gmail_demo._adb(), 'shell', shlex.join(cmd)], capture_output=True,
                               text=True, encoding='utf-8', errors='replace', timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc: raise WorkflowError('无法打开钉钉分享') from exc
        checkpoint()
        if r.returncode or 'Error:' in r.stdout: raise WorkflowError('无法打开钉钉分享')

    def _launch_home(self, checkpoint):
        import gmail_demo
        # Huawei may resolve the launcher successfully but reject a package-only
        # `am start`. Check that there is exactly one actual launcher and that
        # resolve-activity returns that same component before starting it.
        intent = ['-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', '-p', PACKAGE]
        component_re = re.compile(re.escape(PACKAGE) + r'/(?:\.[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*|'
                                  + re.escape(PACKAGE) + r'(?:\.[A-Za-z_$][\w$]*)+)')

        def run(args):
            checkpoint()
            result = subprocess.run([gmail_demo._adb(), 'shell', shlex.join(args)], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=15)
            checkpoint()
            if result.returncode or 'Error:' in result.stdout:
                raise WorkflowError('无法打开钉钉首页核验')
            return result.stdout

        try:
            listing = run(['cmd', 'package', 'query-activities', '--brief', *intent])
            count = re.findall(r'(?m)^\s*(\d+)\s+activities? found:\s*$', listing)
            candidates = [line.strip() for line in listing.splitlines()
                          if re.fullmatch(r'[A-Za-z][\w.]*/[A-Za-z_.$][\w.$]*', line.strip())]
            if (count != ['1'] or len(candidates) != 1 or not component_re.fullmatch(candidates[0])
                    or candidates[0].rsplit('.', 1)[-1] in {'ResolverActivity', 'ChooserActivity'}):
                raise WorkflowError('钉钉首页入口不唯一，已停止核验')
            resolved = run(['cmd', 'package', 'resolve-activity', '--brief', *intent])
            matches = [line.strip() for line in resolved.splitlines()
                       if re.fullmatch(r'[A-Za-z][\w.]*/[A-Za-z_.$][\w.$]*', line.strip())]
            if len(matches) != 1 or matches[0] != candidates[0]:
                raise WorkflowError('钉钉首页入口无法核对，已停止核验')
            run(['am', 'start', '-n', matches[0]])
        except (OSError, subprocess.TimeoutExpired) as exc: raise WorkflowError('无法打开钉钉首页核验') from exc

    def _wait(self, resolve, checkpoint):
        last = None
        for _ in range(6):
            checkpoint()
            # A device/bridge failure is not an absent control and must not
            # silently become six long provider retries.
            root, parents = self._observe(checkpoint)
            try:
                return root, parents, resolve(root, parents)
            except WorkflowError as exc: last = exc
            checkpoint(); time.sleep(.3)
        raise last or WorkflowError('钉钉页面未就绪')

    def share_self(self, task_id, query_id, route, checkpoint, before_send, text_version=2):
        text = route_text(task_id, query_id, route, version=text_version)
        epoch = self.base.context_epoch
        def check():
            checkpoint()
            if self.base.context_epoch != epoch: raise WorkflowError('任务暂停或页面上下文变化，已停止钉钉分享')
        self._launch(text, check)
        def find_page(root, parents):
            try: return ('self', _self_target(root, parents))
            except WorkflowError:
                return ('chooser', _unique_label(root, parents, '钉钉好友'))
        _, _, (kind, target) = self._wait(find_page, check)
        if kind == 'chooser':
            _, _, (again, target) = self._wait(find_page, check)
            if again != 'chooser': raise WorkflowError('钉钉分享页面已变化')
            self._click(target, check)
            _, _, target = self._wait(_self_target, check)
        node, recipient = target
        _, _, fresh = self._wait(_self_target, check)
        if recipient != fresh[1] or _bounds(node) != _bounds(fresh[0]): raise WorkflowError('钉钉本人会话已变化')
        self._click(fresh[0], check)
        resolve = lambda root, parents: _confirmation(root, parents, recipient, text)
        _, _, send = self._wait(resolve, check)
        _, _, fresh_send = self._wait(resolve, check)
        if _bounds(send) != _bounds(fresh_send): raise WorkflowError('钉钉确认窗口已变化')
        check(); before_send(recipient_digest(recipient)); check()
        try:
            # Persisting the attempt can take time. Never dispatch the old
            # button if the page, account or content changed during that time.
            _, _, final_send = self._wait(resolve, check)
            if _bounds(fresh_send) != _bounds(final_send):
                self._verify_diag(task_id, query_id, 'send_then_verify', 'pre_send_revalidation', 'unknown')
                return {'status': 'unknown', 'message': '发送前页面已变化，请核查钉钉本人会话；不会自动重发'}
            self._click(final_send, check)
        except Exception:
            self._verify_diag(task_id, query_id, 'send_then_verify', 'post_send_dispatch', 'unknown')
            return {'status': 'unknown', 'message': '已登记发送尝试，但无法确认钉钉结果，请检查本人会话；不会自动重发'}
        try:
            return self.verify_self(task_id, query_id, route, check, recipient_digest(recipient), text_version=text_version,
                                    diagnostic_source='send_then_verify')
        except Exception:
            self._verify_diag(task_id, query_id, 'send_then_verify', 'post_send_verify', 'unknown')
            return {'status': 'unknown', 'message': '已尝试发送，请检查钉钉本人文件助手；不会自动重发'}

    def verify_self(self, task_id, query_id, route, checkpoint, expected_recipient_digest, text_version=1,
                    diagnostic_source='verify_only'):
        """Navigate to the bound self chat and read it; never send or resend."""
        text = route_text(task_id, query_id, route, version=text_version)
        # A missing identity binding is not fixable by waiting or guessing.
        if not isinstance(expected_recipient_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_recipient_digest):
            self._verify_diag(task_id, query_id, diagnostic_source, 'precondition', 'failed')
            return {'status': 'unknown', 'message': '缺少发送时的本人会话显示名摘要，请手动核查；不会自动重发'}
        deadline = time.monotonic() + 20
        phase = 'initial_read'
        self._verify_diag(task_id, query_id, diagnostic_source, phase, 'started')
        def bounded_check():
            checkpoint()
            if time.monotonic() >= deadline:
                raise WorkflowError('钉钉只读核验超时')
        def confirmed():
            root, parents = self._observe(bounded_check)
            name = _self_chat_recipient(root, parents, expected_recipient_digest)
            return _verified_self_message(root, parents, name, text, query_id)
        try:
            bounded_check()
            try:
                if confirmed():
                    self._verify_diag(task_id, query_id, diagnostic_source, phase, 'verified')
                    return {'status': 'sent', 'message': '已在显示名匹配的钉钉本人文件助手核对到本次完整路线消息'}
            except WorkflowError:
                pass
            self._verify_diag(task_id, query_id, diagnostic_source, phase, 'not_current')
            phase = 'launch_home'
            self._launch_home(bounded_check)
            def read_after_launch():
                # The launcher can resume the already-open self chat instead
                # of the home list. A transient bridge response is retried,
                # but a wrong or ambiguous identity immediately fails closed.
                for attempt in range(5):
                    bounded_check()
                    try:
                        return self._observe(bounded_check)
                    except dingtalk_bridge.TransientRead:
                        self._verify_diag(task_id, query_id, diagnostic_source, phase, 'transient')
                        bounded_check()
                        if attempt == 4: raise
                        time.sleep(.5)
                raise WorkflowError('钉钉窗口尚未就绪')

            phase = 'launch_observation'
            root, parents = read_after_launch()
            titles = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/tv_title'
                      and _visible(n, parents) and _bounds(n)]
            selected_home_bounds = None
            selected_home_window = None
            if titles:
                # The identity check applies before any proof; a different or
                # duplicated title cannot be treated as a loading transition.
                phase = 'resumed_chat'
                _self_chat_recipient(root, parents, expected_recipient_digest)
            else:
                phase = 'home_first'
                first = _home_digest_target(root, parents, expected_recipient_digest)
                first_window = root.get('window-id')
                phase = 'home_recheck'
                root, parents = read_after_launch()
                fresh_home = _home_digest_target(root, parents, expected_recipient_digest)
                if first_window != root.get('window-id') or _bounds(first) != _bounds(fresh_home):
                    raise WorkflowError('钉钉本人会话已变化')
                selected_home_bounds = _bounds(fresh_home)
                selected_home_window = first_window
                phase = 'home_click'
                self._click(fresh_home, bounded_check)
            transition_retries = 4
            while time.monotonic() < deadline:
                bounded_check()
                phase = 'chat_read'
                try:
                    root, parents = self._observe(bounded_check)
                except dingtalk_bridge.TransientRead:
                    self._verify_diag(task_id, query_id, diagnostic_source, phase, 'transient')
                    bounded_check()
                    if transition_retries <= 0: break
                    transition_retries -= 1
                    time.sleep(.5)
                    continue
                titles = [n for n in root.iter('node') if n.get('resource-id') == PACKAGE + ':id/tv_title'
                          and _visible(n, parents) and _bounds(n)]
                # The old home tree may briefly persist after the tap. Retry
                # only the absent-title transition, never a conflicting or
                # ambiguous identity and never any click or send operation.
                if not titles:
                    self._verify_diag(task_id, query_id, diagnostic_source, phase, 'home_pending')
                    # Only the same independently identified home row can
                    # qualify as a still-in-progress transition after a tap.
                    if selected_home_bounds is None:
                        raise WorkflowError('钉钉本人会话标题不可核对')
                    if root.get('window-id') != selected_home_window:
                        raise WorkflowError('钉钉本人会话窗口已变化')
                    unchanged_home = _home_digest_target(root, parents, expected_recipient_digest)
                    if _bounds(unchanged_home) != selected_home_bounds:
                        raise WorkflowError('钉钉本人会话已变化')
                    bounded_check()
                    if transition_retries <= 0: break
                    transition_retries -= 1
                    time.sleep(.5)
                    continue
                phase = 'chat_identity'
                name = _self_chat_recipient(root, parents, expected_recipient_digest)
                phase = 'chat_proof'
                if _verified_self_message(root, parents, name, text, query_id):
                    self._verify_diag(task_id, query_id, diagnostic_source, phase, 'verified')
                    return {'status': 'sent', 'message': '已在显示名匹配的钉钉本人文件助手核对到本次完整路线消息'}
                self._verify_diag(task_id, query_id, diagnostic_source, phase, 'not_observed')
                bounded_check()
                time.sleep(.5)
        except WorkflowError:
            self._verify_diag(task_id, query_id, diagnostic_source, phase, 'failed')
            checkpoint()
        except Exception:
            self._verify_diag(task_id, query_id, diagnostic_source, phase, 'failed')
            raise
        self._verify_diag(task_id, query_id, diagnostic_source, phase, 'unknown')
        return {'status': 'unknown', 'message': '尚无法独立核对当前本人会话的本次消息，请检查钉钉；不会自动重发'}
