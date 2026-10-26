import asyncio
import copy
import threading


class EventHub:
    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = {}

    def subscribe(self, task_id):
        queue = asyncio.Queue(maxsize=256)
        with self.lock:
            self.listeners.setdefault(task_id, {})[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, task_id, queue):
        with self.lock:
            group = self.listeners.get(task_id, {})
            group.pop(queue, None)
            if not group:
                self.listeners.pop(task_id, None)

    def publish(self, task, event):
        message = copy.deepcopy({"event_type": "task_progress", "task": task, "event": event, "cursor": event["seq"]})
        with self.lock:
            listeners = list(self.listeners.get(task["id"], {}).items())
        for queue, loop in listeners:
            def deliver(q=queue):
                if q.full():
                    q.get_nowait()
                q.put_nowait(message)
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(deliver)
                except RuntimeError:
                    self.unsubscribe(task["id"], queue)


hub = EventHub()
