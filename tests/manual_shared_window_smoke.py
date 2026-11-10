"""Native smoke check using two disposable Cocoa windows and one guarded click.

Run from the repository root: .venv/bin/python tests/manual_shared_window_smoke.py
Restores the previously active app and removes all fixture windows on exit.
"""
import asyncio
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import Future
import time
import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable, NSWindowStyleMaskResizable,
    NSWindowStyleMaskMiniaturizable, NSButton,
    NSWorkspace, NSApplicationActivateIgnoringOtherApps,
)
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementPerformAction, AXUIElementSetAttributeValue,
    AXValueCreate, kAXValueCGPointType, kAXValueCGSizeType, kAXRaiseAction,
    kAXMainAttribute, kAXPositionAttribute, kAXSizeAttribute, kAXMinimizedAttribute,
    kAXFocusedWindowAttribute,
)
from Foundation import NSDate, NSRunLoop, NSObject
from agent.macos import mouse, shared_window as native


def pump(seconds=0.25):
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def wait_async(awaitable):
    result = Future()

    def run():
        try:
            result.set_result(asyncio.run(awaitable))
        except Exception as exc:
            result.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + 8
    while not result.done() and time.monotonic() < deadline:
        pump(0.05)
    return result.result(timeout=1)


def assert_ready(presentation):
    state = presentation._refresh()
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    focused = native.attribute(presentation.app, kAXFocusedWindowAttribute)
    assert state == "ready", (
        f"state={state}; front={front.localizedName() if front else None}; "
        f"same_app={front is not None and front.processIdentifier() == presentation.pid}; "
        f"focused_fixture_window={native.attribute(focused, native.kAXTitleAttribute) if focused else None}")


class FixtureButtonTarget(NSObject):
    def clicked_(self, sender):
        print(json.dumps({"clicked": self.index}), flush=True)


def fixture():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    windows = []
    delegates = []
    for index, x in enumerate((80, 700)):
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, 220), (500, 320)),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False)
        window.setReleasedWhenClosed_(False)
        window.setTitle_(f"Squidly native test {index + 1}")
        delegate = FixtureButtonTarget.alloc().init()
        delegate.index = index + 1
        button = NSButton.alloc().initWithFrame_(((50, 40), (400, 240)))
        button.setTitle_(f"Disposable test button {index + 1}")
        button.setTarget_(delegate)
        button.setAction_("clicked:")
        window.contentView().addSubview_(button)
        delegates.append(delegate)
        window.makeKeyAndOrderFront_(None)
        windows.append(window)
    print(json.dumps([int(w.windowNumber()) for w in windows]), flush=True)
    app.run()


def smoke():
    logging.basicConfig(level=logging.INFO)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    previous = NSWorkspace.sharedWorkspace().frontmostApplication()
    previous_ax = AXUIElementCreateApplication(previous.processIdentifier()) if previous else None
    previous_window = native.attribute(previous_ax, kAXFocusedWindowAttribute) if previous_ax else None
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--fixture"], stdout=subprocess.PIPE, text=True)
    presentation = native.WindowPresentation()
    try:
        first, second = json.loads(child.stdout.readline())
        pump(0.5)
        # Start the fixture target focused for edge hit checks. Presentation
        # itself must never acquire focus just to maintain the shared window.
        _, initial_window = native.bind_ax_window(native.target_info(first))
        native.NSRunningApplication.runningApplicationWithProcessIdentifier_(child.pid).activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        AXUIElementPerformAction(initial_window, kAXRaiseAction)
        pump(0.3)
        initial_state = wait_async(presentation.activate(first))
        assert initial_state == "ready", f"could not raise the exact fixture window: {initial_state}"
        pump()
        assert_ready(presentation)
        assert len(native.frame.panels) == 4
        from AppKit import NSWindowCollectionBehaviorCanJoinAllSpaces
        assert all(not (p.collectionBehavior() & NSWindowCollectionBehaviorCanJoinAllSpaces) for p in native.frame.panels)
        assert native.frame.on_active_space(presentation)
        assert all(p.isVisible() and not p.isKeyWindow() and not p.canBecomeKeyWindow() for p in native.frame.panels)
        bounds = native.target_info(first)["kCGWindowBounds"]
        for nx, ny in ((0, 0), (1, 1), (0.5, 0.5)):
            assert mouse.window_hit_at(*mouse.window_relative_point(bounds, nx, ny)) == first, "frame intercepted a target point"
        print("PASS exact window raised; 4 green strips visible; frame does not take keyboard/mouse focus", flush=True)

        assert AXUIElementSetAttributeValue(presentation.window, kAXPositionAttribute, AXValueCreate(kAXValueCGPointType, (110, 150))) == 0
        assert AXUIElementSetAttributeValue(presentation.window, kAXSizeAttribute, AXValueCreate(kAXValueCGSizeType, (540, 360))) == 0
        pump()
        assert_ready(presentation)
        bounds = native.target_info(first)["kCGWindowBounds"]
        assert abs(native.frame.panels[0].frame().origin.x - float(bounds["X"])) < 1
        print("PASS green frame follows actual moved/resized window", flush=True)

        assert presentation.timer.timeInterval() <= 1 / 60 + 0.001
        # Observe the timer itself: no explicit refresh may mask a slow frame.
        for x in range(120, 220, 10):
            assert AXUIElementSetAttributeValue(presentation.window, kAXPositionAttribute,
                AXValueCreate(kAXValueCGPointType, (x, 150))) == 0
            pump(0.04)
            current = native.target_info(first)["kCGWindowBounds"]
            assert abs(native.frame.panels[0].frame().origin.x - float(current["X"])) < 1
        AXUIElementSetAttributeValue(presentation.window, kAXPositionAttribute, AXValueCreate(kAXValueCGPointType, (110, 150)))
        pump(0.04)
        print("PASS green frame follows 10 consecutive moves within a 40ms observation interval", flush=True)

        _, other_window = native.bind_ax_window(native.target_info(second))
        assert other_window is not None
        for name, kind, value in ((kAXPositionAttribute, kAXValueCGPointType, (110, 150)),
                                  (kAXSizeAttribute, kAXValueCGSizeType, (540, 360))):
            assert AXUIElementSetAttributeValue(other_window, name, AXValueCreate(kind, value)) == 0
        AXUIElementSetAttributeValue(other_window, kAXMainAttribute, True)
        AXUIElementPerformAction(other_window, kAXRaiseAction)
        pump()
        pump(0.8)
        assert_ready(presentation)
        bounds = native.target_info(first)["kCGWindowBounds"]
        focused = native.attribute(presentation.app, kAXFocusedWindowAttribute)
        assert focused is not None and native.CFEqual(focused, other_window)
        assert mouse.window_control_state(first)[0] == "window_obscured"
        print("PASS same-app meeting window keeps focus; unsafe remote destination stays blocked", flush=True)

        wait_async(presentation.focus_for_input())
        bounds = native.target_info(first)["kCGWindowBounds"]
        point = mouse.window_relative_point(bounds, 0.5, 0.5)
        assert mouse.window_hit_at(*point) == first
        mouse.click(*point, window_id=first)
        assert select.select([child.stdout], [], [], 3)[0], "fixture did not receive the click"
        assert json.loads(child.stdout.readline()) == {"clicked": 1}, "covering window received the click"
        print("PASS explicit remote action raises the exact shared window and clicks its disposable button, not the cover", flush=True)

        if previous and previous.processIdentifier() != presentation.pid:
            previous.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            pump(0.8)
            assert_ready(presentation)
            assert NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == previous.processIdentifier()
            print("PASS switching to another application keeps local application focus", flush=True)

        assert wait_async(presentation.pause()) == "paused_by_sharer"
        AXUIElementSetAttributeValue(other_window, kAXMainAttribute, True)
        AXUIElementPerformAction(other_window, kAXRaiseAction)
        pump()
        assert presentation._refresh() == "paused_by_sharer"
        assert all(not p.isVisible() for p in native.frame.panels)
        assert wait_async(presentation.recover("window_obscured")) == "paused_by_sharer"
        assert wait_async(presentation.activate(first)) == "ready"
        print("PASS manual pause stops raising, including late blocked-input callbacks; resume restores original", flush=True)

        assert AXUIElementSetAttributeValue(presentation.window, kAXMinimizedAttribute, True) == 0
        pump(1.5)
        minimized_state = presentation._refresh()
        assert minimized_state == "window_minimized", f"state={minimized_state}; AXMinimized={native.attribute(presentation.window, kAXMinimizedAttribute)}; info={native.target_info(first, include_hidden=True) is not None}"
        assert native.attribute(presentation.window, kAXMinimizedAttribute)
        assert all(not p.isVisible() for p in native.frame.panels)
        assert wait_async(presentation.activate(first)) == "ready"
        assert_ready(presentation)
        print("PASS minimizing stays paused/minimized; only explicit Resume restores the same window", flush=True)

        successor = native.WindowPresentation()
        assert wait_async(successor.activate(first)) == "window_in_use"
        assert wait_async(successor.refresh()) == "window_in_use"
        assert native.frame.owner is presentation
        previous_owner = presentation
        wait_async(previous_owner.close())
        presentation = successor
        assert wait_async(presentation.refresh()) == "ready"
        assert native.frame.owner is presentation
        # Late cleanup from the old connection cannot hide the new frame.
        wait_async(previous_owner.close())
        assert_ready(presentation)
        assert all(p.isVisible() for p in native.frame.panels)
        print("PASS overlapping connections wait without takeover; owner disconnect automatically activates exact bound window", flush=True)
    finally:
        wait_async(presentation.close())
        assert all(not p.isVisible() for p in native.frame.panels)
        child.terminate()
        child.wait(timeout=5)
        if previous:
            previous.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            if previous_window is not None:
                AXUIElementPerformAction(previous_window, kAXRaiseAction)
        print("Fixture closed, green frame cleared, previous app restored", flush=True)


def existing_window_smoke(handoff_only=False):
    """Raise/frame the already-frontmost real application without editing it."""
    logging.basicConfig(level=logging.INFO)
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    windows = native.CGWindowListCopyWindowInfo(
        native.kCGWindowListOptionOnScreenOnly | native.kCGWindowListExcludeDesktopElements, 0) or []
    target = next(w for w in windows if int(w.get("kCGWindowLayer", 1)) == 0
                  and int(w.get("kCGWindowOwnerPID", 0)) == int(front.processIdentifier())
                  and float(w.get("kCGWindowBounds", {}).get("Height", 0)) > 100)
    presentation = native.WindowPresentation()
    window_id = int(target["kCGWindowNumber"])
    try:
        state = wait_async(presentation.activate(window_id))
        assert state == "ready", f"real application window {window_id}: {state}"
        pump()
        assert_ready(presentation)
        bounds = native.target_info(window_id)["kCGWindowBounds"]
        # A running Agent may also have its own edge frame on this window.
        # Handoff-only mode checks the center without modifying that instance.
        for point in (((0.5, 0.5),) if handoff_only else ((0, 0), (1, 1), (0.5, 0.5))):
            assert mouse.window_hit_at(*mouse.window_relative_point(bounds, *point)) == window_id
        print(f"PASS current {front.localizedName()} window id={window_id}: exact AX binding, foreground, green frame, hit checks", flush=True)
        if handoff_only:
            waiting = native.WindowPresentation()
            assert wait_async(waiting.activate(window_id)) == "window_in_use"
            assert wait_async(waiting.refresh()) == "window_in_use"
            assert native.frame.owner is presentation
            assert wait_async(waiting.pause()) == "paused_by_sharer"
            assert wait_async(waiting.refresh()) == "paused_by_sharer"
            assert wait_async(waiting.activate(window_id)) == "window_in_use"
            previous_owner = presentation
            wait_async(previous_owner.close())
            presentation = waiting
            assert wait_async(presentation.refresh()) == "ready"
            assert native.frame.owner is presentation
            wait_async(previous_owner.close())
            assert_ready(presentation)
            assert all(p.isVisible() for p in native.frame.panels)
            assert presentation.window_id == window_id
            print("PASS same-window connection handoff: wait, cancel, rejoin, owner exit, automatic recovery, late cleanup isolation", flush=True)
    finally:
        wait_async(presentation.close())
        print("Real application kept open; green frame cleared", flush=True)


if __name__ == "__main__":
    if "--fixture" in sys.argv:
        fixture()
    elif "--handoff-only" in sys.argv:
        existing_window_smoke(handoff_only=True)
    elif "--existing-window" in sys.argv:
        existing_window_smoke()
    else:
        smoke()
