"""Capture a disposable window by native ID; verify pixels, resize and lease cleanup.
No user window is recorded. Run from repo root with .venv/bin/python.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSWorkspace, NSBitmapImageRep
import ScreenCaptureKit as SC
from ApplicationServices import AXUIElementSetAttributeValue, AXValueCreate, kAXSizeAttribute, kAXValueCGSizeType
from agent.macos.capture import WindowCapture, CaptureObserver, resolve_capture
from agent.macos.shared_window import on_main, target_info, bind_ax_window
from manual_shared_window_smoke import pump
from concurrent.futures import Future
import threading
import time
import logging


def wait_async(awaitable):
    result = Future()
    def run():
        try: result.set_result(asyncio.run(awaitable))
        except Exception as error: result.set_exception(error)
    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + 45
    while not result.done() and time.monotonic() < deadline:
        pump(0.05)
    return result.result(timeout=1)


async def check(window_id):
    loop = asyncio.get_running_loop()
    content_future = loop.create_future()
    def received(content, error):
        loop.call_soon_threadsafe(lambda: content_future.set_exception(RuntimeError(str(error))) if error else content_future.set_result(content))
    SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, received)
    content = await asyncio.wait_for(content_future, 10)
    window = next(w for w in content.windows() if int(w.windowID()) == window_id)
    capture = WindowCapture()
    try:
        if "--present" in sys.argv:
            await on_main(capture.present)
            await asyncio.sleep(0.5)
        def select():
            capture.observer = CaptureObserver.alloc().init()
            capture.observer.owner = capture
            capture.choose(SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window))
        await on_main(select)
        surface = await asyncio.wait_for(capture.selected, 10)
        assert surface['id'] == window_id
        assert resolve_capture(capture.token) is capture
        frame = await asyncio.wait_for(capture.frames.get(), 10)
        assert isinstance(frame, bytes), frame
        image = NSBitmapImageRep.imageRepWithData_(frame)
        assert image and image.pixelsWide() > 100 and image.pixelsHigh() > 100
        Path('/tmp/remoteagent-unified-fixture.jpg').write_bytes(frame)
        print(f'PASS capture: selected ID {window_id}, JPEG {image.pixelsWide()}x{image.pixelsHigh()}, live lease')
        capture.consumed()
        def resize():
            app, ax_window = bind_ax_window(target_info(window_id))
            assert AXUIElementSetAttributeValue(ax_window, kAXSizeAttribute, AXValueCreate(kAXValueCGSizeType, (700, 420))) == 0
        await on_main(resize)
        await asyncio.sleep(0.3)
        bounds = target_info(window_id)['kCGWindowBounds']
        await capture.refresh_geometry(bounds)
        for _ in range(20):
            frame = await asyncio.wait_for(capture.frames.get(), 3)
            assert isinstance(frame, bytes), frame
            image = NSBitmapImageRep.imageRepWithData_(frame)
            capture.consumed()
            if abs(image.pixelsWide()/image.pixelsHigh() - bounds['Width']/bounds['Height']) < 0.02:
                break
        else:
            raise AssertionError('capture aspect did not follow resize')
        assert capture.window_id == window_id
        print('PASS resize: video aspect follows window; native identity unchanged')
        stopped = []
        async def control_stopped(): stopped.append(True)
        capture.stop_callbacks.add(control_stopped)
        await capture.close()
        assert resolve_capture(capture.token) is None and stopped == [True]
        await capture.close()
        assert stopped == [True]
        print('PASS stop: lease revoked and control notified exactly once')
        await transport_check(window)

    finally:
        await capture.close()


async def transport_check(window):
    from unittest.mock import patch
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    from agent import server
    from agent.macos import capture as native_capture
    def pick(selected):
        selected.observer = CaptureObserver.alloc().init()
        selected.observer.owner = selected
        selected.choose(SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window))
    with patch.object(WindowCapture, 'present', pick):
        async with serve(server.handle_client, '127.0.0.1', 0) as listener:
            address = f'ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}'
            async with connect(address + '/capture', origin='https://squidly.com.au', max_size=4*1024*1024) as video:
                print('CHECK transport handshake',flush=True)
                assert json.loads(await video.recv())['unifiedWindowCapture']
                await video.send(json.dumps(dict(type='capture.start')))
                selected = json.loads(await asyncio.wait_for(video.recv(), 5))
                assert selected['id'] == int(window.windowID()), selected
                print('CHECK transport selected native ID', selected['id'],flush=True)
                frame = await asyncio.wait_for(video.recv(), 5)
                assert isinstance(frame, bytes) and frame[:2] == b'\xff\xd8'
                async with connect(address) as control:
                    await control.recv()  # screen.info
                    request = dict(type='surface.match',surface='window',captureId=selected['captureId'],width=0,height=0,foregroundControl=True)
                    for _ in range(20):
                        await control.send(json.dumps(request))
                        reply = json.loads(await control.recv())
                        if reply.get('controlAllowed'):
                            break
                        await asyncio.sleep(0.1)
                    assert reply.get('controlAllowed') and reply['id'] == selected['id'], reply
                    await control.send(json.dumps(dict(type='mouse.click',surfaceId=selected['id'],nx=0.5,ny=0.5,x=0,y=0)))
                    reply = json.loads(await control.recv())
                    assert reply.get('ok'), reply
                    await video.close()
                    reply = json.loads(await asyncio.wait_for(control.recv(), 5))
                    assert reply.get('pauseReason') == 'capture_unavailable', reply
                    assert native_capture.resolve_capture(selected['captureId']) is None
                    await control.send(json.dumps(dict(type='surface.activate',surfaceId=selected['id'])))
                    assert json.loads(await control.recv()).get('pauseReason') == 'capture_unavailable'
                    print('PASS WebSocket capture → exact-ID control → guarded native click → capture close revokes Resume')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    previous = NSWorkspace.sharedWorkspace().frontmostApplication()
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('manual_shared_window_smoke.py')), '--fixture'], stdout=subprocess.PIPE, text=True)
    try:
        first, _ = json.loads(child.stdout.readline())
        pump(0.3)
        wait_async(check(first))
        import select
        assert select.select([child.stdout], [], [], 2)[0], 'fixture did not receive native click'
        assert json.loads(child.stdout.readline()) == {'clicked':1}
        print('PASS disposable button received exactly the expected native click')
    finally:
        child.terminate()
        child.wait(timeout=5)
        if previous: previous.activateWithOptions_(0)
        pump(0.2)
