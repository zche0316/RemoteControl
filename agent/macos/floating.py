"""Non-activating floating Squidly companion; all Cocoa work stays on the main thread."""
import json
import objc
from pathlib import Path

from AppKit import (
    NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSColor,
    NSPanel, NSScreen, NSEvent, NSFloatingWindowLevel, NSBackingStoreBuffered,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSPasteboard, NSPasteboardTypeString,
)
from Foundation import NSObject, NSTimer, NSURL, NSUserDefaults
from WebKit import WKWebView, WKWebViewConfiguration

from agent import connection_status

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
COLLAPSED_SIZE = (88, 144)  # Vertical 72 x 128 capsule plus the shadow margin.


def fit_frame(x, y, width, height, visible):
    """Keep the complete companion inside the chosen display's usable area."""
    return (max(visible.origin.x, min(x, visible.origin.x + visible.size.width - width)),
            max(visible.origin.y, min(y, visible.origin.y + visible.size.height - height)))


class FloatingAgent(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    def show(self):
        self.expanded = False
        self.ready = False
        self.last_status = None
        self.drag_origin = None
        self.defaults = NSUserDefaults.standardUserDefaults()
        frame = NSScreen.mainScreen().visibleFrame()
        x = self.defaults.doubleForKey_('SquidlyFloatingX') if self.defaults.objectForKey_('SquidlyFloatingX') is not None else frame.origin.x + frame.size.width - COLLAPSED_SIZE[0] - 24
        y = self.defaults.doubleForKey_('SquidlyFloatingY') if self.defaults.objectForKey_('SquidlyFloatingY') is not None else frame.origin.y + frame.size.height - 160
        x, y = fit_frame(x, y, *COLLAPSED_SIZE, frame)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), COLLAPSED_SIZE), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
        config = WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(self, 'agent')
        self.web = WKWebView.alloc().initWithFrame_configuration_(((0, 0), COLLAPSED_SIZE), config)
        self.web.setValue_forKey_(False, 'drawsBackground')
        self.panel.setContentView_(self.web)
        self.web.loadFileURL_allowingReadAccessToURL_(NSURL.fileURLWithPath_(str(ASSETS / 'floating.html')), NSURL.fileURLWithPath_(str(ASSETS)))
        self.panel.orderFrontRegardless()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.35, self, 'refresh:', None, True)
        return self

    def visibleScreen(self):
        return (self.panel.screen() or NSScreen.mainScreen()).visibleFrame()

    def setExpanded_(self, expanded):
        old = self.panel.frame()
        self.expanded = expanded
        visible = self.visibleScreen()
        width, height = (328, min(620, visible.size.height - 24)) if expanded else COLLAPSED_SIZE
        x, y = fit_frame(old.origin.x, old.origin.y + old.size.height - height, width, height, visible)
        self.panel.setFrame_display_(((x, y), (width, height)), True)
        self.web.evaluateJavaScript_completionHandler_('window.setExpanded(' + ('true' if expanded else 'false') + ', ' + str(int(visible.size.height - 40)) + ')', None)
        self.savePosition()

    def savePosition(self):
        frame = self.panel.frame()
        self.defaults.setDouble_forKey_(frame.origin.x, 'SquidlyFloatingX')
        # Persist the collapsed top edge so the next launch doesn't jump down.
        self.defaults.setDouble_forKey_(frame.origin.y + frame.size.height - COLLAPSED_SIZE[1], 'SquidlyFloatingY')

    def refresh_(self, _timer):
        if not self.ready:
            return
        payload = json.dumps(connection_status.snapshot(), ensure_ascii=True)
        if payload != self.last_status:
            self.last_status = payload
            self.web.evaluateJavaScript_completionHandler_('window.renderStatus(' + payload + ')', None)

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        # Only the bundled main-frame document may invoke the small local bridge.
        if (not message.frameInfo().isMainFrame()
                or message.frameInfo().request().URL().path() != str(ASSETS / 'floating.html')):
            return
        body = message.body()
        if not hasattr(body, 'get'):
            return
        action = body.get('action')
        if action == 'ready':
            self.ready = True
            self.refresh_(None)
        elif action in ('expand', 'collapse'):
            self.setExpanded_(action == 'expand')
        elif action == 'resize' and self.expanded:
            height = body.get('height')
            if isinstance(height, (int, float)) and 100 <= height <= 2000:
                frame, visible = self.panel.frame(), self.visibleScreen()
                height = min(height, visible.size.height - 24)
                x, y = fit_frame(frame.origin.x, frame.origin.y + frame.size.height - height, 328, height, visible)
                self.panel.setFrame_display_(((x, y), (328, height)), True)
        elif action == 'copyMeeting':
            index, clients = body.get('index'), connection_status.snapshot()
            if isinstance(index, int) and 0 <= index < len(clients) and clients[index].get('meeting'):
                pasteboard = NSPasteboard.generalPasteboard()
                pasteboard.clearContents()
                pasteboard.setString_forType_(clients[index]['meeting'], NSPasteboardTypeString)
        elif action == 'disconnect':
            connection_status.disconnect_all()
        elif action == 'quit':
            NSApp.terminate_(None)
        elif action == 'dragStart':
            point, frame = NSEvent.mouseLocation(), self.panel.frame()
            self.drag_origin = (point.x, point.y, frame.origin.x, frame.origin.y)
        elif action == 'dragMove' and self.drag_origin:
            point, frame = NSEvent.mouseLocation(), self.panel.frame()
            px, py, x, y = self.drag_origin
            # Allow crossing between displays, then keep within that display.
            screen = next((s for s in NSScreen.screens() if s.frame().origin.x <= point.x <= s.frame().origin.x + s.frame().size.width and s.frame().origin.y <= point.y <= s.frame().origin.y + s.frame().size.height), self.panel.screen() or NSScreen.mainScreen())
            x, y = fit_frame(x + point.x - px, y + point.y - py, frame.size.width, frame.size.height, screen.visibleFrame())
            self.panel.setFrameOrigin_((x, y))
        elif action == 'dragEnd':
            self.drag_origin = None
            self.savePosition()



_retained = []


class FloatingAppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        from agent.macos.splash import LaunchSplash
        _retained.append(LaunchSplash.alloc().init().show())
        self.companion = FloatingAgent.alloc().init().show()
        _retained.append(self.companion)

    def applicationDidChangeScreenParameters_(self, _notification):
        if hasattr(self, 'companion'):
            self.companion.setExpanded_(self.companion.expanded)


def run_floating_app(listen):
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = FloatingAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    _retained.append(delegate)
    app.run()
