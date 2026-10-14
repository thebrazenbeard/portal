from __future__ import annotations

import queue
import threading

from portal.desktop_app import PortalDesktopApp


def test_worker_ui_updates_are_dispatched_only_from_tk_main_thread() -> None:
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app._ui_queue=queue.SimpleQueue()
    invoked=[]
    errors=[]
    main=threading.current_thread()

    class Root:
        def winfo_exists(self): return True
        def after(self, delay, callback):
            assert threading.current_thread() is main
            invoked.append(("timer",delay))
    app.root=Root()

    def worker():
        try:
            app._post_to_ui(lambda: invoked.append(("ui","executed")))
        except Exception as exc:
            errors.append(exc)
    t=threading.Thread(target=worker)
    t.start();t.join(timeout=2)
    assert not errors
    assert not t.is_alive()
    assert invoked == []
    app._drain_ui_queue()
    assert invoked == [("ui","executed"),("timer",80)]


def test_multiple_queued_callbacks_preserve_order_and_bounded_drain() -> None:
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app._ui_queue=queue.SimpleQueue()
    order=[]
    class Root:
        def winfo_exists(self): return True
        def after(self,delay,callback):pass
    app.root=Root()
    for i in range(30):
        app._post_to_ui(lambda i=i: order.append(i))
    app._drain_ui_queue()
    assert order == list(range(20))
    app._drain_ui_queue()
    assert order == list(range(30))
