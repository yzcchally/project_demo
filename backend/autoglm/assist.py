"""Optional bounded, independently grounded AutoGLM help for one Amap step.

The main rule workflow owns the query, device lease, result collection and
validation. This module never launches a PhoneAgent or follows a model loop.
"""
from __future__ import annotations

import base64
import json
import math
import os
import time
import threading
import xml.etree.ElementTree as ET
from dataclasses import replace

from domain import WorkflowError, now
from device.amap import PACKAGE, bounds
from device.huawei import ScreenContextChanged
from autoglm.client import AutoGLMClient
from autoglm.config import Config
from autoglm.executor import AutoGLMTransitDevice, png_size, proposal_is_current
from autoglm.hints import native_control_hints
from autoglm.safety import resolve_action


# One outstanding assistance network request per process. A timed-out daemon
# can never dispatch phone actions and must finish before another may start.
_REQUEST_SLOT = threading.Lock()


class AssistDeadlineExceeded(WorkflowError):
    code = 'assist_timeout'


def _bounded_decide(client, task, observation, checkpoint, deadline):
    if not _REQUEST_SLOT.acquire(blocking=False):
        raise WorkflowError('上一次 AutoGLM 辅助请求仍在结束，请稍后重试')
    done = threading.Event()
    result = {}
    def run():
        try:
            result['decision'] = client.decide(task, observation, [], lambda: None)
        except BaseException as exc:
            result['error'] = exc
        finally:
            _REQUEST_SLOT.release()
            done.set()
    threading.Thread(target=run, name='silverguide-assist-request', daemon=True).start()
    try:
        while True:
            checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssistDeadlineExceeded('AutoGLM 辅助请求超时，未执行手机操作')
            if done.wait(min(.05, remaining)):
                checkpoint()
                if time.monotonic() >= deadline:
                    raise AssistDeadlineExceeded('AutoGLM 辅助请求超时，未执行手机操作')
                if 'error' in result:
                    raise result['error']
                return result['decision']
    finally:
        # On cancellation or deadline the provider may still be blocked in an
        # I/O read. Close the client if available; any late result is discarded.
        if not done.is_set():
            connection = getattr(client, '_client', None)
            if connection is not None:
                def close_connection():
                    try:
                        connection.close()
                    except Exception:
                        pass
                threading.Thread(target=close_connection, name='silverguide-assist-close', daemon=True).start()


class AutoGLMAssist:
    """A single query's strictly bounded assistance; only transit Tap is supported."""
    def __init__(self, adapter, *, client_factory=None):
        self.adapter = adapter
        self.client_factory = client_factory or AutoGLMClient
        try:
            self.max_calls = int(os.getenv('SILVERGUIDE_AUTOGLM_MAX_CALLS', '2'))
        except ValueError:
            raise WorkflowError('AutoGLM 辅助调用次数配置无效') from None
        if not 1 <= self.max_calls <= 10:
            raise WorkflowError('AutoGLM 辅助调用次数配置无效')
        self.task_id, self.query_id = adapter.task_id, adapter.query_id
        self.calls = self._load_call_count()

    def _load_call_count(self):
        """Recreated adapters cannot reset a query's persistent call budget."""
        path = self.adapter.folder / 'autoglm-assist.jsonl'
        if not path.exists():
            return 0
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise WorkflowError('AutoGLM 辅助调用记录无效，已停止')
        count = 0
        try:
            with path.open(encoding='utf-8') as f:
                for line in f:
                    item = json.loads(line)
                    if (not isinstance(item, dict) or item.get('task_id') != self.task_id
                            or item.get('query_id') != self.query_id or item.get('executor') != 'rules_assist'):
                        raise ValueError()
                    if item.get('status') == 'requested':
                        count += 1
                        if type(item.get('call_number')) is not int or item['call_number'] != count:
                            raise ValueError()
        except (OSError, ValueError, TypeError):
            raise WorkflowError('AutoGLM 辅助调用记录无效，已停止') from None
        return count

    def _record(self, **item):
        adapter = self.adapter
        item.update(time=now(), task_id=self.task_id, query_id=self.query_id,
                    executor='rules_assist', trigger=getattr(adapter, '_assist_trigger', 'expected_control_not_found'))
        with (adapter.folder / 'autoglm-assist.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(item, ensure_ascii=False) + '\n')

    def _observe(self, checkpoint):
        adapter = self.adapter
        adapter._check(checkpoint)
        epoch = adapter.base.context_epoch
        foreground = AutoGLMTransitDevice._foreground(adapter)
        geometry = AutoGLMTransitDevice._geometry(adapter)
        if foreground != PACKAGE:
            raise WorkflowError('高德不在前台，不能使用 AutoGLM 辅助')
        shot = adapter._observe(checkpoint, 'route_input', screenshot=True)
        # The rule adapter exposes only Amap nodes, but independent safety
        # grounding must see the entire raw tree, including foreign ancestors
        # and overlapping windows. Never reconstruct UI facts from the model.
        try:
            shot['nodes'] = list(ET.fromstring(shot['xml']).iter('node'))
        except (ET.ParseError, KeyError, TypeError):
            raise WorkflowError('高德界面原始节点无法核对，未调用辅助') from None
        png = (adapter.folder / (shot['evidence']['observation_id'] + '.png')).read_bytes()
        width, height, rotation = geometry
        if (png_size(png) != (width, height) or shot['epoch'] != epoch
                or adapter.base.context_epoch != epoch or AutoGLMTransitDevice._foreground(adapter) != foreground
                or AutoGLMTransitDevice._geometry(adapter) != geometry):
            raise ScreenContextChanged('高德屏幕或任务上下文已变化，辅助动作已废弃')
        shot.update(width=width, height=height, screen_width=width, screen_height=height, rotation=rotation, current_app=foreground,
                    current_package=foreground, observation_id=shot['evidence']['observation_id'],
                    screenshot_base64=base64.b64encode(png).decode())
        # Only Android's independently observed, visible status-bar inset may
        # be exempted; unknown/ambiguous insets leave freshness strict.
        from autoglm.freshness import status_bar_frame
        adapter._check(checkpoint)
        try:
            insets = adapter.base.shell('dumpsys', 'window', 'displays', timeout=8)
        except (ScreenContextChanged,):
            raise
        except Exception as error:
            from domain import Cancelled
            if isinstance(error, Cancelled):
                raise
            insets = ''
        adapter._check(checkpoint)
        if adapter.base.context_epoch != epoch:
            raise ScreenContextChanged('高德页面上下文已变化，辅助动作已废弃')
        shot['status_bar_frame'] = status_bar_frame(insets, width, height)
        # The rule workflow owns the page preconditions; validate on both
        # the model capture and the fresh pre-dispatch capture.
        adapter._assist_page_safe(shot, checkpoint)
        return shot

    def tap(self, step, checkpoint, postcondition, *, precondition=None):
        adapter = self.adapter
        if step != 'transit':
            raise WorkflowError('此步骤不支持 AutoGLM 辅助')
        if self.calls >= self.max_calls:
            raise WorkflowError('本轮 AutoGLM 辅助调用次数已用完')
        adapter._check(checkpoint)
        task_id, query_id = adapter.task_id, adapter.query_id
        if not task_id or not query_id or task_id != self.task_id or query_id != self.query_id:
            raise WorkflowError('AutoGLM 辅助缺少当前任务标识')
        def check():
            adapter._check(checkpoint)
            if adapter.task_id != task_id or adapter.query_id != query_id:
                raise ScreenContextChanged('路线任务已更新，旧辅助动作已废弃')
        before = self._observe(check)
        if precondition is not None:
            precondition(before)
            check()
        state = {'adapter': adapter, 'assist_transit_precondition': precondition is not None}
        hints = [h for h in native_control_hints(before, adapter.request, state) if h['role'] == 'transit']
        if not hints:
            # Without a defensible candidate, a model coordinate can never be
            # safely authorized; do not transmit the screenshot needlessly.
            self._record(step=step, status='failed', reason_code='no_verified_candidate',
                         before_observation_id=before['observation_id'])
            raise WorkflowError('当前页面没有可独立核对的公共交通入口，未调用 AutoGLM 辅助')
        # Never send unrelated UI text or the user's whole trip request.
        remaining = adapter.total_seconds - (time.monotonic() - adapter.started)
        if not math.isfinite(remaining) or remaining < 1:
            raise WorkflowError('高德查询剩余时间不足，未调用 AutoGLM 辅助')
        env_timeout = os.getenv('SILVERGUIDE_AUTOGLM_TIMEOUT', '20')
        try:
            timeout = min(float(env_timeout), remaining)
        except ValueError:
            raise WorkflowError('AutoGLM 辅助超时配置无效') from None
        if not math.isfinite(timeout) or timeout < 1 or timeout > 70:
            raise WorkflowError('AutoGLM 辅助超时配置无效')
        config = replace(Config.from_env(), timeout_seconds=timeout, history_limit=1)
        config.validate()
        client = self.client_factory(config, directory=adapter.folder)
        def messages(task, observation, history):
            # Only actual independently grounded targets become hints. They
            # narrow the model's choice, never grant authorization: both the
            # proposal and fresh frame are still independently grounded.
            regions = [h['normalized_bounds'] for h in hints]
            goal = ('目标：点按顶部交通方式栏的公共交通，不要选择下面的路线卡片。'
                    '坐标以 0 到 999 归一化；已独立核对的目标范围：'
                    + json.dumps(regions, ensure_ascii=False)
                    + '。仅在该范围内根据截图自行选择坐标；若不能确定，请求协助。'
                      '此范围不表示动作已执行，且执行前仍会独立检查。')
            return [{'role':'system','content':'你只协助定位当前高德公共交通入口。可输出一个 do(action="Tap", element=[x,y])；不确定时请求协助。不能输入、滑动、返回或启动应用。'},
                    {'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,' + observation['screenshot_base64']}},
                                              {'type':'text','text':goal}]}]
        client.messages = messages
        self.calls += 1
        self._record(step=step, status='requested', call_number=self.calls, model=config.model,
                     before_observation_id=before['observation_id'])
        phase, action, suggested = 'model', None, None
        try:
            deadline = min(adapter.started + adapter.total_seconds, time.monotonic() + timeout)
            decision = _bounded_decide(client, {'task_id':task_id,'query_id':query_id}, before, check, deadline)
            check()
            action = decision.action
            if action.kind != 'Tap':
                phase = 'unsupported_action'
                raise WorkflowError('AutoGLM 辅助只允许点击公共交通入口')
            suggested = action.params.get('element')
            phase = 'grounding'
            first = resolve_action(action, before, adapter.request, state)
            if first.role != 'transit':
                phase = 'wrong_role'
                raise WorkflowError('AutoGLM 候选不是当前公共交通入口')
            phase = 'freshness'
            fresh = self._observe(check)
            if precondition is not None:
                precondition(fresh)
                check()
            if not proposal_is_current(action, before, fresh, request=adapter.request, state=state):
                raise ScreenContextChanged('高德页面已变化，旧辅助点击已废弃')
            grounded = resolve_action(action, fresh, adapter.request, state)
            if grounded.role != 'transit' or grounded.point != first.point:
                raise WorkflowError('公共交通入口已变化，辅助点击未执行')
            check()
            adapter._action(check)
            if adapter.base.context_epoch != fresh['epoch']:
                raise ScreenContextChanged('暂停恢复后旧辅助点击已废弃')
            self._record(step=step, status='dispatching', call_number=self.calls, model=config.model,
                         action='Tap', proposed_point=suggested, executed_point=grounded.point,
                         candidate_role=grounded.role, candidate_bounds=bounds(grounded.node),
                         before_observation_id=before['observation_id'], fresh_observation_id=fresh['observation_id'],
                         latency_ms=decision.latency_ms)
            phase = 'dispatch'
            if adapter.base.transport:
                adapter.base.transport.tap(*grounded.point)
            else:
                adapter.base.shell('input', 'tap', *(str(v) for v in grounded.point))
            phase = 'postcondition'
            check()
            self._record(step=step, status='executed', call_number=self.calls, model=config.model,
                         action='Tap', proposed_point=suggested, executed_point=grounded.point,
                         candidate_role=grounded.role, candidate_bounds=bounds(grounded.node),
                         before_observation_id=before['observation_id'], fresh_observation_id=fresh['observation_id'])
            after, _ = adapter._wait(postcondition, check, 'route_input', attempts=8)
            check()
            self._record(step=step, status='verified', call_number=self.calls, model=config.model,
                         action='Tap', proposed_point=suggested, executed_point=grounded.point,
                         candidate_role=grounded.role, candidate_bounds=bounds(grounded.node),
                         before_observation_id=before['observation_id'], after_observation_id=after['evidence']['observation_id'])
            return after
        except Exception as exc:
            # Never persist remote responses or exception messages.
            reason = getattr(exc, 'code', None)
            allowed_reasons = {'grounding_rejected','unrecognized_target','loading_overlay_active',
                               'route_prerequisite_missing','input_value_mismatch','assist_timeout'}
            if reason not in allowed_reasons:
                reason = phase
            kind = getattr(action, 'kind', None)
            if kind not in {'Tap','Swipe','Type','Back','Launch','Wait','Finish','Help'}:
                kind = None
            safe_point = suggested if (isinstance(suggested, list) and len(suggested) == 2
                                       and all(type(v) is int and 0 <= v < 1000 for v in suggested)) else None
            self._record(step=step, status='failed', call_number=self.calls, model=config.model,
                         action=kind, proposed_point=safe_point, reason_code=reason,
                         error_type=type(exc).__name__, before_observation_id=before['observation_id'])
            raise
