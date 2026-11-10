"""One native selection owns both window capture and control identity.

SCContentSharingPicker grants access to a single window. SCStream captures that
filter even when covered; no desktop rectangle, title, or size is used to bind it.
Only the newest JPEG is retained while the local browser is busy.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading

import objc
from Foundation import NSObject
from AppKit import NSBitmapImageRep, NSBitmapImageFileTypeJPEG, NSImageCompressionFactor
from Quartz import CIContext, CIImage, CGRectMake
from CoreMedia import CMSampleBufferGetImageBuffer, CMSampleBufferIsValid, CMTimeMake
import ScreenCaptureKit as SC
from agent.macos.shared_window import on_main, target_info

log = logging.getLogger(__name__)
leases = {}  # Accessed only by the WebSocket asyncio loop.
_picker_owner = None  # Accessed only on the Cocoa main thread.
_native_captures = set()


def resolve_capture(token):
    capture = leases.get(token) if isinstance(token, str) else None
    return capture if capture and not capture.closed and not capture.ended and capture.window_id else None


class CaptureError(Exception):
    pass


class CaptureObserver(NSObject, protocols=[objc.protocolNamed('SCContentSharingPickerObserver'),
                                           objc.protocolNamed('SCStreamOutput'),
                                           objc.protocolNamed('SCStreamDelegate')]):
    def contentSharingPicker_didUpdateWithFilter_forStream_(self, picker, content_filter, stream):
        owner = self.owner
        if owner is None:
            return
        if stream is not None:
            if stream == owner.stream:
                owner.finish(CaptureError('The shared content changed. Share the window again.'))
            return
        if _picker_owner is owner and not owner.closed:
            owner.choose(content_filter)

    def contentSharingPicker_didCancelForStream_(self, picker, stream):
        if self.owner is not None and _picker_owner is self.owner and stream is None:
            self.owner.finish(CaptureError('cancelled'))

    def contentSharingPickerStartDidFailWithError_(self, error):
        if self.owner is not None and _picker_owner is self.owner:
            self.owner.finish(CaptureError(str(error)))

    def stream_didStopWithError_(self, stream, error):
        if self.owner is not None:
            self.owner.finish(CaptureError('Window sharing stopped. Share the window again.'))

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, output_type):
        owner = self.owner
        if owner is None or owner.closed or owner.ended or output_type != SC.SCStreamOutputTypeScreen or not CMSampleBufferIsValid(sample):
            return
        pixel = CMSampleBufferGetImageBuffer(sample)
        if pixel is None:
            return
        # Avoid accumulating encoded frames or callbacks on a slow browser.
        with owner.frame_lock:
            if owner.frame_pending:
                return
            owner.frame_pending = True
        try:
            image = CIImage.imageWithCVPixelBuffer_(pixel)
            cg_image = owner.context.createCGImage_fromRect_(image, image.extent())
            rep = NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
            jpeg = rep.representationUsingType_properties_(NSBitmapImageFileTypeJPEG,
                                                          {NSImageCompressionFactor: 0.8})
            owner.loop.call_soon_threadsafe(owner.deliver, bytes(jpeg))
        except Exception:
            with owner.frame_lock:
                owner.frame_pending = False
            log.exception('could not encode shared window')


class WindowCapture:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.selected = self.loop.create_future()
        self.frames = asyncio.Queue(maxsize=1)
        self.token = secrets.token_urlsafe(32)
        self.window_id = None
        self.surface = None
        self.closed = False
        self.ended = False
        self.stream = None
        self.observer = None
        self.context = None
        self.frame_lock = threading.Lock()
        self.frame_pending = False
        self.stop_callbacks = set()

    def finish(self, error):
        self.loop.call_soon_threadsafe(self._finish, error)

    def _finish(self, error):
        self.ended = True
        leases.pop(self.token, None)
        if not self.selected.done():
            self.selected.set_exception(error)
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(error)

    def deliver(self, frame):
        if self.closed:
            with self.frame_lock:
                self.frame_pending = False
            return
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    def consumed(self):
        with self.frame_lock:
            self.frame_pending = False

    def present(self):
        global _picker_owner
        if _picker_owner is not None:
            raise CaptureError('Another window picker is open. Finish or cancel it first.')
        if not hasattr(SC.SCContentFilter, 'includedWindows'):
            raise CaptureError('Window sharing needs macOS 15.2 or later.')
        _native_captures.add(self)
        self.observer = CaptureObserver.alloc().init()
        self.observer.owner = self
        picker = SC.SCContentSharingPicker.sharedPicker()
        config = SC.SCContentSharingPickerConfiguration.alloc().init()
        config.setAllowedPickerModes_(SC.SCContentSharingPickerModeSingleWindow)
        config.setAllowsChangingSelectedContent_(False)
        picker.setDefaultConfiguration_(config)
        picker.setMaximumStreamCount_(4)
        picker.addObserver_(self.observer)
        picker.setActive_(True)
        _picker_owner = self
        picker.presentPickerUsingContentStyle_(SC.SCShareableContentStyleWindow)

    def choose(self, content_filter):
        global _picker_owner
        if self.closed or self.ended or self.stream is not None:
            return
        windows = list(content_filter.includedWindows() or [])
        if content_filter.style() != SC.SCShareableContentStyleWindow or len(windows) != 1:
            self.finish(CaptureError('Choose one window to share.'))
            return
        _picker_owner = None
        _native_captures.add(self)
        window = windows[0]
        self.window_id = int(window.windowID())
        info = target_info(self.window_id, include_hidden=True)
        if info is None:
            self.finish(CaptureError('The selected window was closed. Share another window.'))
            return
        bounds = info['kCGWindowBounds']
        if bounds['Width'] <= 0 or bounds['Height'] <= 0:
            self.finish(CaptureError('Show the selected window, then share it again.'))
            return
        frame = CGRectMake(bounds['X'], bounds['Y'], bounds['Width'], bounds['Height'])
        app = window.owningApplication()
        self.surface = dict(id=self.window_id, kind='window', x=frame.origin.x, y=frame.origin.y,
                            width=frame.size.width, height=frame.size.height,
                            name=window.title() or '', owner=app.applicationName() if app else '')
        config = SC.SCStreamConfiguration.alloc().init()
        scale = min(float(content_filter.pointPixelScale()), 1920 / max(frame.size.width, frame.size.height))
        config.setWidth_(max(2, int(frame.size.width * scale)))
        config.setHeight_(max(2, int(frame.size.height * scale)))
        config.setMinimumFrameInterval_(CMTimeMake(1, 20))
        config.setQueueDepth_(3)
        config.setPixelFormat_(0x42475241)  # kCVPixelFormatType_32BGRA
        config.setShowsCursor_(True)
        config.setCapturesAudio_(False)
        config.setIncludeChildWindows_(False)
        config.setScalesToFit_(True)
        config.setPreservesAspectRatio_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        self.config = config
        self.dimensions = (frame.size.width, frame.size.height)
        self.context = CIContext.contextWithOptions_(None)
        self.stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, self.observer)
        ok, error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(self.observer,
                                        SC.SCStreamOutputTypeScreen, None, None)
        if not ok:
            self.finish(CaptureError(str(error)))
            return
        self.stream.startCaptureWithCompletionHandler_(self.started)

    async def refresh_geometry(self, bounds):
        dimensions = (bounds["Width"], bounds["Height"])
        if dimensions == self.dimensions or self.closed or min(dimensions) <= 0:
            return
        def apply():
            if self.closed:
                return
            self.dimensions = dimensions
            width, height = dimensions
            scale = min(2, 1920 / max(width, height))
            self.config.setWidth_(max(2, int(width * scale)))
            self.config.setHeight_(max(2, int(height * scale)))
            self.stream.updateConfiguration_completionHandler_(self.config,
                lambda error: self.finish(CaptureError(str(error))) if error else None)
        await on_main(apply)

    def started(self, error):
        self.loop.call_soon_threadsafe(self._started, error)

    def _started(self, error):
        if self.closed or self.selected.done():
            return
        if error:
            self.selected.set_exception(CaptureError(str(error)))
        else:
            leases[self.token] = self
            self.selected.set_result(self.surface)

    async def start(self):
        await on_main(self.present)
        return await self.selected

    async def close(self):
        if self.closed:
            return
        self.closed = True
        leases.pop(self.token, None)
        if not self.selected.done():
            self.selected.cancel()
        for callback in list(self.stop_callbacks):
            try:
                await callback()
            except Exception:
                log.exception('could not end shared-window control')
        self.stop_callbacks.clear()
        await on_main(self._close)

    def _close(self):
        global _picker_owner
        picker = SC.SCContentSharingPicker.sharedPicker()
        if self.observer:
            picker.removeObserver_(self.observer)
        if _picker_owner is self:
            _picker_owner = None
        if self.stream:
            self.stream.removeStreamOutput_type_error_(self.observer, SC.SCStreamOutputTypeScreen, None)
            self.stream.stopCaptureWithCompletionHandler_(lambda error: None)
            self.stream = None
        if self.observer:
            self.observer.owner = None
        self.observer = None
        self.context = None
        _native_captures.discard(self)
        if not _native_captures and _picker_owner is None:
            picker.setActive_(False)
