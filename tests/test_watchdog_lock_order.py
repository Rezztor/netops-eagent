"""Registering a watch must not hold the registry's lock while it talks to the observer.

The observer's own thread delivers an event holding the observer's lock and then takes the registry's lock
(`dispatch`). `add` used to schedule under the registry's lock, so a file event that arrived during start-up left
each thread waiting for the other and the web server never started.
"""

import threading

from helpers import watchdog


class _Observer:
    """Stands in for the real observer: one lock, held while an event is delivered, wanted by `schedule`."""

    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.Lock()
        self.delivered = threading.Event()

    def start(self):
        pass

    def is_alive(self):
        return True

    def unschedule_all(self):
        pass

    def stop(self):
        pass

    def join(self):
        pass

    def schedule(self, handler, root, recursive=True):
        # An event arrives now, on the observer's thread, while the caller is still registering.
        def deliver():
            with self.lock:
                handler.dispatch(type("Event", (), {"event_type": "modified", "src_path": root + "/x.py"})())
            self.delivered.set()

        thread = threading.Thread(target=deliver, daemon=True)
        with self.lock:
            thread.start()
            # The delivery now waits for this lock. If the caller holds the registry's lock here, the delivery
            # takes this lock next and then waits for the registry's: the two threads never finish.
            assert not self.registry._lock._is_owned(), "schedule() called with the registry lock held"
        assert self.delivered.wait(5), "the event was never delivered"


def test_adding_a_watch_does_not_deadlock_with_an_event_being_delivered(tmp_path, monkeypatch):
    registry = watchdog._WatchRegistry()
    observer = _Observer(registry)
    monkeypatch.setattr(registry, "_create_observer", lambda: observer)

    done = threading.Event()

    def register():
        registry.add("t", [str(tmp_path)], ["*.py"], None, "all", 0, lambda items: None)
        done.set()

    threading.Thread(target=register, daemon=True).start()
    assert done.wait(10), "add() never returned: the registry and the observer are waiting for each other"
