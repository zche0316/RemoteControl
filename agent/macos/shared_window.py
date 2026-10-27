"""Explicit foreground ownership and a non-activating frame for one shared window.

All AppKit and Accessibility work runs on the Cocoa main thread. Polling and
the frame timer never raise windows: only start/resume may change focus.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import logging
import threading

from AppKit import (
    NSApplication, NSColor, NSPanel, NSRunningApplication, NSWorkspace,
    NSApplicationActivateIgnoringOtherApps, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowSharingNone,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
)
from ApplicationServices import (
    AXIsProcessTrusted, AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementPerformAction, AXUIElementSetAttributeValue, AXUIElementSetMessagingTimeout,
    AXUIElementIsAttributeSettable,
    AXValueGetValue, kAXWindowsAttribute, kAXTitleAttribute, kAXPositionAttribute,
    kAXSizeAttribute, kAXValueCGPointType, kAXValueCGSizeType, kAXRaiseAction,
    kAXMinimizedAttribute, kAXMainAttribute, kAXFocusedWindowAttribute, kAXFocusedAttribute,
    kAXErrorCannotComplete,
)
from CoreFoundation import CFEqual
from Foundation import NSTimer
from PyObjCTools.AppHelper import callAfter
from Quartz import (
    CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionAll,
    kCGWindowListOptionOnScreenOnly, kCGWindowListExcludeDesktopElements,
)

from agent.macos import mouse

logger = logging.getLogger(__name__)


async def on_main(callback):
    if threading.current_thread() is threading.main_thread():
        return callback()
    result = Future()

    def run():
        if result.cancelled():
            return
        try:
            value = callback()
            if not result.done():
                result.set_result(value)
        except Exception as exc:
            if not result.done():
                result.set_exception(exc)

    callAfter(run)
    return await asyncio.wait_for(asyncio.wrap_future(result), timeout=4)


def target_info(window_id, include_hidden=False):
    option = kCGWindowListOptionAll if include_hidden else kCGWindowListOptionOnScreenOnly
    windows = CGWindowListCopyWindowInfo(option | kCGWindowListExcludeDesktopElements, kCGNullWindowID) or []
    return next((w for w in windows if int(w.get("kCGWindowNumber") or 0) == window_id), None)


def attribute(element, name):
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    return None if error else value


def ax_bounds(window):
    position = attribute(window, kAXPositionAttribute)
    size = attribute(window, kAXSizeAttribute)
    if position is None or size is None:
        return None
    position_ok, point = AXValueGetValue(position, kAXValueCGPointType, None)
    size_ok, dimensions = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not position_ok or not size_ok:
        return None
    return dict(X=float(point.x), Y=float(point.y), Width=float(dimensions.width), Height=float(dimensions.height))


def bind_ax_window(info):
    """Bind uniquely within the owning app, then verify focus and OS hits by ID.

    Public AX does not expose CGWindowID. Never choose the first app window or
    accept PID alone; identical candidates remain unresolved.
    """
    app = AXUIElementCreateApplication(int(info["kCGWindowOwnerPID"]))
    AXUIElementSetMessagingTimeout(app, 0.4)
    windows = attribute(app, kAXWindowsAttribute) or []
    bounds = info["kCGWindowBounds"]
    candidates = []
    for window in windows:
        AXUIElementSetMessagingTimeout(window, 0.4)
        rect = ax_bounds(window)
        if rect and all(abs(rect[key] - float(bounds[key])) <= 1 for key in ("X", "Y", "Width", "Height")):
            candidates.append(window)
    if len(candidates) > 1 and info.get("kCGWindowName"):
        candidates = [w for w in candidates if attribute(w, kAXTitleAttribute) == info["kCGWindowName"]]
    logger.info("shared-window AX binding id=%s candidates=%s", info.get("kCGWindowNumber"), len(candidates))
    return (app, candidates[0]) if len(candidates) == 1 else (None, None)


class BorderPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class GreenFrame:
    def __init__(self):
        self.owner = None
        self.panels = []

    def show(self, owner, bounds):
        if self.owner is not owner:
            return
        if not self.panels:
            NSApplication.sharedApplication()
            for _ in range(4):
                panel = BorderPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    ((0, 0), (1, 1)), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
                    NSBackingStoreBuffered, False)
                panel.setReleasedWhenClosed_(False)
                panel.setHidesOnDeactivate_(False)
                panel.setOpaque_(False)
                panel.setHasShadow_(False)
                panel.setIgnoresMouseEvents_(True)
                panel.setLevel_(NSFloatingWindowLevel)
                panel.setSharingType_(NSWindowSharingNone)
                panel.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.8, 0.35, 1))
                panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
                self.panels.append(panel)
            mouse.set_input_transparent_windows(panel.windowNumber() for panel in self.panels)
        x, y = float(bounds["X"]), float(bounds["Y"])
        width, height = float(bounds["Width"]), float(bounds["Height"])
        bottom = mouse.main_display_bounds()["height"] - y - height
        t = min(3.0, width / 2, height / 2)
        # Inset strips stay visible even for maximized/fullscreen windows. The
        # hit-test excludes precisely these registered click-through panels.
        frames = [((x, bottom), (t, height)), ((x+width-t, bottom), (t, height)),
                  ((x+t, bottom), (max(0, width-2*t), t)),
                  ((x+t, bottom+height-t), (max(0, width-2*t), t))]
        for panel, frame in zip(self.panels, frames):
            panel.setFrame_display_(frame, True)
            panel.orderFrontRegardless()

    def hide(self, owner):
        if self.owner is owner:
            for panel in self.panels:
                panel.orderOut_(None)


frame = GreenFrame()


class WindowPresentation:
    def __init__(self):
        self.window_id = None
        self.pid = None
        self.app = None
        self.window = None
        self.paused_reason = "checking"
        self.timer = None

    async def activate(self, window_id):
        try:
            reason = await on_main(lambda: self._activate(window_id))
            if reason == "ready":
                # Activation is asynchronous. Give WindowServer time to switch
                # apps or finish the restore animation before latching a pause.
                ready_samples = 0
                for _ in range(12):
                    await asyncio.sleep(0.15)
                    reason = await on_main(lambda: self._refresh(latch=False))
                    if reason == "ready":
                        ready_samples += 1
                        if ready_samples >= 3:
                            return await on_main(self._refresh)
                        continue
                    ready_samples = 0
                    # An AXRaise during a minimize/restore animation can return
                    # CannotComplete. Retry only inside this explicit action.
                    await on_main(self._raise_target)
            await self.pause(reason)
            logger.info("shared-window activation id=%s paused=%s", window_id, reason)
            return reason
        except Exception:
            logger.exception("could not activate the shared window")
            await self.pause("window_focus_unavailable")
            return "window_focus_unavailable"

    def _activate(self, window_id):
        if frame.owner is not None and frame.owner is not self:
            return "window_in_use"
        if not AXIsProcessTrusted():
            return "window_focus_unavailable"
        info = target_info(window_id, include_hidden=True)
        if info is None:
            return "window_unavailable"
        if self.window is not None and self.window_id == window_id and self.pid == int(info["kCGWindowOwnerPID"]):
            app, window = self.app, self.window
        else:
            app, window = bind_ax_window(info)
        if window is None:
            return "window_binding_unavailable"
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.window_id, self.pid = window_id, int(info["kCGWindowOwnerPID"])
        self.app, self.window = app, window
        frame.owner = self
        self.paused_reason = None
        AXUIElementSetAttributeValue(window, kAXMinimizedAttribute, False)
        native_app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if native_app is None:
            self.paused_reason = "window_not_frontmost"
            return self.paused_reason
        accepted = native_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        logger.info("shared-window activation requested id=%s accepted=%s", window_id, accepted)
        raise_error = self._raise_target()
        if raise_error not in (0, kAXErrorCannotComplete):
            logger.info("shared-window raise id=%s AXError=%s", window_id, raise_error)
            self.paused_reason = "window_focus_unavailable"
            return self.paused_reason
        return "ready"

    def _raise_target(self):
        AXUIElementSetAttributeValue(self.window, kAXMainAttribute, True)
        error = AXUIElementPerformAction(self.window, kAXRaiseAction)
        # Main-window status does not itself grant keyboard focus. Use the
        # public focus attributes when the target application makes them writable.
        for element, name, value in ((self.window, kAXFocusedAttribute, True),
                                     (self.app, kAXFocusedWindowAttribute, self.window)):
            err, writable = AXUIElementIsAttributeSettable(element, name, None)
            if not err and writable:
                AXUIElementSetAttributeValue(element, name, value)
        return error

    def _refresh(self, latch=True):
        if self.window is None:
            return self.paused_reason or "window_focus_unavailable"
        info = target_info(self.window_id)
        minimized = self.window is not None and attribute(self.window, kAXMinimizedAttribute)
        if info is None or int(info.get("kCGWindowOwnerPID") or 0) != self.pid or minimized:
            reason = "window_unavailable"
        elif self.paused_reason:
            reason = self.paused_reason
        else:
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            focused = attribute(self.app, kAXFocusedWindowAttribute)
            reason = ("ready" if front is not None and int(front.processIdentifier()) == self.pid
                      and focused is not None and CFEqual(focused, self.window) else "window_not_frontmost")
            if reason == "ready" and not latch:
                # Focus can change before a restore animation finishes.
                point = mouse.window_relative_point(info["kCGWindowBounds"], 0.5, 0.5)
                if mouse.window_hit_at(*point) != self.window_id:
                    reason = "window_obscured"
        if reason == "ready" and latch:
            frame.show(self, info["kCGWindowBounds"])
            if self.timer is None:
                self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, True, self._tick)
        elif reason != "ready":
            if latch:
                self.paused_reason = reason
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            frame.hide(self)
        return reason

    def _tick(self, _timer):
        if self.timer is None or _timer != self.timer:
            return
        try:
            self._refresh()
        except Exception:
            self.paused_reason = "window_focus_unavailable"
            frame.hide(self)
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None

    async def refresh(self):
        return await on_main(self._refresh)

    async def pause(self, reason="paused_by_sharer"):
        def apply():
            self.paused_reason = reason
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            frame.hide(self)
        await on_main(apply)
        return reason

    async def close(self):
        def apply():
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            frame.hide(self)
            if frame.owner is self:
                frame.owner = None
        await on_main(apply)
