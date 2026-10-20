"""A lightweight, non-blocking branded launch panel on the Cocoa main thread."""
from pathlib import Path

from AppKit import (
    NSBackingStoreBuffered, NSColor, NSFont, NSImage, NSImageView,
    NSImageScaleProportionallyUpOrDown, NSPanel, NSTextField, NSView,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSObject, NSTimer, NSURL
from CoreText import CTFontManagerRegisterFontsForURL, kCTFontManagerScopeProcess

ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def label(parent, text, frame, size, color, bold=False):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(frame)
    font = NSFont.fontWithName_size_('Poppins-Bold' if bold else 'Poppins-Regular', size)
    if font is None:
        raise RuntimeError('Bundled Poppins font could not be loaded')
    field.setFont_(font)
    field.setTextColor_(color)
    parent.addSubview_(field)
    return field


def picture(parent, name, frame):
    view = NSImageView.alloc().initWithFrame_(frame)
    view.setImage_(NSImage.alloc().initWithContentsOfFile_(str(ASSETS / name)))
    view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    parent.addSubview_(view)


class LaunchSplash(NSObject):
    def show(self):
        for name in ('Poppins-Regular.ttf', 'Poppins-Bold.ttf'):
            CTFontManagerRegisterFontsForURL(
                NSURL.fileURLWithPath_(str(ASSETS / 'fonts' / name)),
                kCTFontManagerScopeProcess, None)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (900, 560)), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setHidesOnDeactivate_(False)
        # A transparent, non-opaque window lets the clipped corners show
        # the desktop instead of the window's rectangular white backing.
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setMovableByWindowBackground_(True)
        root = NSView.alloc().initWithFrame_(((0, 0), (900, 560)))
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        root.layer().setCornerRadius_(12)
        root.layer().setMasksToBounds_(True)
        self.panel.setContentView_(root)
        ink = NSColor.colorWithCalibratedWhite_alpha_(.12, 1)
        muted = NSColor.colorWithCalibratedWhite_alpha_(.43, 1)
        picture(root, 'splash-photo.jpg', ((380, 0), (520, 560)))
        # Brand symbol and wordmark share a row; the product name aligns
        # with the symbol below, matching the reference's stacked lockup.
        picture(root, 'logo.svg', ((34, 452), (60, 60)))
        picture(root, 'logo-text.svg', ((94, 452), (146.49, 60)))
        label(root, 'Remote Agent', ((40, 402), (310, 52)), 32, ink, True)
        # Reference hierarchy: a short description, copyright/details,
        # then the dynamic launch message, all on the same left edge.
        label(root, 'Remote control for Squidly sessions.', ((40, 310), (310, 18)), 11, ink)
        label(root, '© 2026 Squidly. All rights reserved.', ((40, 276), (310, 17)), 11, ink)
        label(root, 'Connect through your Squidly session', ((40, 259), (310, 17)), 11, ink)
        label(root, 'to get started with remote control.', ((40, 242), (310, 17)), 11, ink)
        label(root, 'Starting Remote Agent…', ((40, 207), (310, 18)), 11, ink)
        self.panel.center()
        self.panel.orderFrontRegardless()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            3.0, self, 'dismiss:', None, False)
        return self

    def dismiss_(self, _timer):
        self.panel.orderOut_(None)
        self.timer = None
