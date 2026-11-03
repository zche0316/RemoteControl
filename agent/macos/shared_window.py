"""Explicit foreground ownership and a non-activating frame for one shared window.

All AppKit and Accessibility work runs on the Cocoa main thread. The timer
only observes availability and follows the frame; explicit actions may raise.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import logging
import threading
import time

from AppKit import (
    NSApplication, NSColor, NSPanel, NSRunningApplication, NSWorkspace,
    NSApplicationActivateIgnoringOtherApps, NSBackingStoreBuffered,
    NSFloatingWindowLevel,
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

    def on_active_space(self, owner):
        return self.owner is not owner or not self.panels or all(panel.isOnActiveSpace() for panel in self.panels)

    def discard_panels(self):
        for panel in self.panels:
            panel.orderOut_(None)
            panel.close()
        self.panels = []
        mouse.set_input_transparent_windows(())

    def show(self, owner, bounds):
        if self.owner is not owner:
            return
        if not self.on_active_space(owner):
            self.hide(owner)
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
                # The highlight belongs to this desktop, never all desktops.
                panel.setCollectionBehavior_(NSWindowCollectionBehaviorFullScreenAuxiliary)
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
        self.recovering_since = None
        self.waiting_window_id = None

    @property
    def owns_control(self):
        # Reading this Python identity requires no AppKit calls. Mutations of
        # frame ownership remain serialized on the Cocoa main thread.
        return frame.owner is self

    async def activate(self, window_id):
        try:
            reason = await on_main(lambda: self._activate(window_id))
            if reason == "ready":
                # Restoring a minimized window is asynchronous. Wait for it to
                # become available without requiring ownership of local focus.
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
            # Remember this connection's exact target, without taking ownership
            # from the active connection. Periodic refresh retries after release.
            self.waiting_window_id = window_id
            return "window_in_use"
        self.waiting_window_id = None
        if not AXIsProcessTrusted():
            return "window_focus_unavailable"
        info = target_info(window_id, include_hidden=True)
        if info is None:
            return "window_unavailable"
        if frame.owner is self and not frame.on_active_space(self):
            # Returning to the original desktop is the normal Resume path. If
            # the user moved the target to a new desktop, allow reattaching only
            # when a real OS hit confirms that target is visible here now.
            point = mouse.window_relative_point(info["kCGWindowBounds"], 0.5, 0.5)
            if mouse.window_hit_at(*point) != window_id:
                return "window_not_visible"
            frame.discard_panels()
        if self.window is not None and self.window_id == window_id and self.pid == int(info["kCGWindowOwnerPID"]):
            app, window = self.app, self.window
        else:
            app, window = bind_ax_window(info)
        if window is None:
            return "window_binding_unavailable"
        if not attribute(window, kAXMinimizedAttribute) and not info.get("kCGWindowIsOnscreen", True):
            # Resume must not pull the user into a different desktop/Space.
            return "window_not_visible"
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.window_id, self.pid = window_id, int(info["kCGWindowOwnerPID"])
        self.app, self.window = app, window
        if frame.owner is not self:
            frame.discard_panels()
        frame.owner = self
        self.paused_reason = None
        self.recovering_since = None
        AXUIElementSetAttributeValue(window, kAXMinimizedAttribute, False)
        raise_error = self._raise_target()
        if raise_error not in (0, kAXErrorCannotComplete):
            logger.info("shared-window raise id=%s AXError=%s", window_id, raise_error)
            self.paused_reason = "window_focus_unavailable"
            return self.paused_reason
        return "ready"

    def _raise_target(self):
        # Stacking maintenance must never activate an application or set its
        # focused/main window. Some apps make AXRaise change their focused
        # window, so do not raise over another active window in that same app.
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and int(front.processIdentifier()) == self.pid:
            focused = attribute(self.app, kAXFocusedWindowAttribute)
            if focused is None or not CFEqual(focused, self.window):
                return 0
        return AXUIElementPerformAction(self.window, kAXRaiseAction)

    def _has_keyboard_focus(self):
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        focused = attribute(self.app, kAXFocusedWindowAttribute)
        return (front is not None and int(front.processIdentifier()) == self.pid
                and focused is not None and CFEqual(focused, self.window))

    def _focus_target_for_input(self):
        if self._has_keyboard_focus():
            return
        native_app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if native_app is not None:
            native_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        AXUIElementSetAttributeValue(self.window, kAXMainAttribute, True)
        AXUIElementPerformAction(self.window, kAXRaiseAction)
        # Main-window status does not itself grant keyboard focus. Use the
        # public focus attributes when the target application makes them writable.
        for element, name, value in ((self.window, kAXFocusedAttribute, True),
                                     (self.app, kAXFocusedWindowAttribute, self.window)):
            err, writable = AXUIElementIsAttributeSettable(element, name, None)
            if not err and writable:
                AXUIElementSetAttributeValue(element, name, value)

    async def focus_for_input(self):
        # Explicit remote actions may acquire focus. Timers and pointer hover
        # must never call this method.
        def apply():
            if self.owns_control and not self.paused_reason:
                self._focus_target_for_input()
        await on_main(apply)
        # App activation is asynchronous; wait for the explicit action's focus
        # transition before the caller resolves coordinates and checks the hit.
        for _ in range(8):
            state = await on_main(lambda: None if not self.owns_control or self.paused_reason
                                  else self._has_keyboard_focus())
            if state is None or state:
                return
            await asyncio.sleep(0.05)

    def _refresh(self, latch=True):
        if self.window is None:
            return self.paused_reason or "window_focus_unavailable"
        if self.paused_reason:
            return self.paused_reason
        info = target_info(self.window_id, include_hidden=True)
        minimized = self.window is not None and attribute(self.window, kAXMinimizedAttribute)
        if minimized:
            reason = "window_minimized"
        elif not frame.on_active_space(self):
            # CGWindowIsOnscreen can remain true for a window in another Space.
            # Our single-Space panels provide an independent desktop check.
            reason = "window_not_visible"
        elif info is None or int(info.get("kCGWindowOwnerPID") or 0) != self.pid or not info.get("kCGWindowIsOnscreen", True):
            # WindowServer can remove the window before AXMinimized updates.
            # Wait briefly without raising/restoring, so a minimize animation
            # is not permanently mislabeled as a desktop switch or close.
            now = time.monotonic()
            if self.recovering_since is None:
                self.recovering_since = now
            unavailable = "window_unavailable" if info is None or int(info.get("kCGWindowOwnerPID") or 0) != self.pid else "window_not_visible"
            reason = "preparing_window" if now - self.recovering_since < 0.4 else unavailable
        else:
            reason = "ready"
            self.recovering_since = None
        if reason in ("ready", "preparing_window") and latch:
            if reason == "ready":
                frame.show(self, info["kCGWindowBounds"])
            else:
                frame.hide(self)
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
        waiting_id = await on_main(lambda: self.waiting_window_id
            if self.paused_reason == "window_in_use" and frame.owner is None else None)
        if waiting_id is not None:
            return await self.activate(waiting_id)
        return await on_main(self._refresh)

    async def recover(self, reason):
        """Drop an unsafe event, then let foreground maintenance recover it.

        No replay: a click meant for the old state must never land later.
        Explicit pauses remain latched, including during late move callbacks.
        """
        if reason not in ("window_obscured", "window_not_frontmost", "preparing_window"):
            return await self.pause(reason)
        state = await self.refresh()
        return "preparing_window" if state == "ready" else state

    async def pause(self, reason="paused_by_sharer"):
        def apply():
            self.paused_reason = reason
            if reason != "window_in_use":
                self.waiting_window_id = None
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            frame.hide(self)
        await on_main(apply)
        return reason

    async def close(self):
        def apply():
            self.paused_reason = "window_unavailable"
            self.waiting_window_id = None
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            frame.hide(self)
            if frame.owner is self:
                frame.owner = None
        await on_main(apply)
