"""Native smoke check using two disposable Cocoa windows, with no mouse injection.

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable, NSWindowStyleMaskResizable,
    NSWindowStyleMaskMiniaturizable,
    NSWorkspace, NSApplicationActivateIgnoringOtherApps,
)
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementPerformAction, AXUIElementSetAttributeValue,
    AXValueCreate, kAXValueCGPointType, kAXValueCGSizeType, kAXRaiseAction,
    kAXMainAttribute, kAXPositionAttribute, kAXSizeAttribute, kAXMinimizedAttribute,
    kAXFocusedWindowAttribute,
)
from Foundation import NSDate, NSRunLoop
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


def fixture():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    windows = []
    for index, x in enumerate((80, 700)):
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, 220), (500, 320)),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False)
        window.setReleasedWhenClosed_(False)
        window.setTitle_(f"Squidly native test {index + 1}")
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
        initial_state = wait_async(presentation.activate(first))
        assert initial_state == "ready", f"could not raise the exact fixture window: {initial_state}"
        pump()
        assert_ready(presentation)
        assert len(native.frame.panels) == 4
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

        _, other_window = native.bind_ax_window(native.target_info(second))
        assert other_window is not None
        AXUIElementSetAttributeValue(other_window, kAXMainAttribute, True)
        AXUIElementPerformAction(other_window, kAXRaiseAction)
        pump()
        assert presentation._refresh() == "window_not_frontmost"
        assert all(not p.isVisible() for p in native.frame.panels)
        AXUIElementSetAttributeValue(presentation.window, kAXMainAttribute, True)
        AXUIElementPerformAction(presentation.window, kAXRaiseAction)
        pump()
        assert presentation._refresh() == "window_not_frontmost", "pause resumed without explicit action"
        assert wait_async(presentation.activate(first)) == "ready"
        pump()
        assert_ready(presentation)
        print("PASS switching to another window of the same app pauses; explicit resume restores original", flush=True)

        assert AXUIElementSetAttributeValue(presentation.window, kAXMinimizedAttribute, True) == 0
        pump()
        minimized_state = presentation._refresh()
        assert minimized_state == "window_unavailable", f"minimized window reported {minimized_state}"
        restored_state = wait_async(presentation.activate(first))
        assert restored_state == "ready", (
            f"restore reported {restored_state}; minimized={native.attribute(presentation.window, kAXMinimizedAttribute)}; "
            f"visible={native.target_info(first) is not None}; foreground="
            f"{NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == presentation.pid}")
        pump()
        assert_ready(presentation)
        print("PASS minimizing pauses; explicit resume restores the same window", flush=True)
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


def existing_window_smoke():
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
        for point in ((0, 0), (1, 1), (0.5, 0.5)):
            assert mouse.window_hit_at(*mouse.window_relative_point(bounds, *point)) == window_id
        print(f"PASS current {front.localizedName()} window id={window_id}: exact AX binding, foreground, green frame, edge hit checks", flush=True)
    finally:
        wait_async(presentation.close())
        print("Real application kept open; green frame cleared", flush=True)


if __name__ == "__main__":
    if "--fixture" in sys.argv:
        fixture()
    elif "--existing-window" in sys.argv:
        existing_window_smoke()
    else:
        smoke()
