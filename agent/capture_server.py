"""Local capture transport; control must reference a live capture lease."""
import asyncio
import json
import logging
from urllib.parse import urlparse
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)


async def handle_capture(websocket):
    # This endpoint transports pixels. Browser callers are restricted to the
    # product and local development origins; each new stream also needs the OS picker.
    origin = websocket.request.headers.get('Origin')
    parsed = urlparse(origin or '')
    if origin and not (parsed.scheme in ('http', 'https') and
                       parsed.hostname in ('squidly.com.au', 'www.squidly.com.au', 'localhost', '127.0.0.1')):
        await websocket.close(code=1008, reason='Unsupported sharing origin')
        return
    import platform
    version = tuple(int(part) for part in platform.mac_ver()[0].split('.')[:2])
    if version < (15, 2):
        await websocket.send(json.dumps(dict(type='capture.error', error='Native window sharing needs macOS 15.2 or later. Choose browser sharing instead.')))
        await websocket.close()
        return
    from agent.macos.capture import WindowCapture, CaptureError
    from agent.macos.shared_window import target_info
    from agent import connection_status
    capture = WindowCapture()
    loop = asyncio.get_running_loop()
    connection_status.add(websocket, lambda: loop.call_soon_threadsafe(
        lambda: asyncio.create_task(websocket.close(code=4000, reason='Sharing stopped from RemoteAgent'))))
    worker = None
    closed = asyncio.create_task(websocket.wait_closed())
    try:
        await websocket.send(json.dumps(dict(type='capture.info', unifiedWindowCapture=True)))
        raw = await websocket.recv()
        if not isinstance(raw, str) or json.loads(raw).get('type') != 'capture.start':
            raise CaptureError('Invalid sharing request.')
        connection_status.update(websocket, state='Choose a window to share')
        worker = asyncio.create_task(capture.start())
        done, _ = await asyncio.wait([worker, closed], return_when=asyncio.FIRST_COMPLETED)
        if closed in done:
            return
        surface = await worker
        connection_status.update(websocket, state='Sharing · remote control off',
                                 window=connection_status.surface_label(dict(ok=True, **surface)))
        await websocket.send(json.dumps(dict(type='capture.started', captureId=capture.token, **surface)))

        async def frames():
            while not capture.closed:
                try:
                    frame = await asyncio.wait_for(capture.frames.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if target_info(capture.window_id, include_hidden=True) is None:
                        raise CaptureError('The shared window was closed.')
                    continue
                if isinstance(frame, Exception):
                    raise frame
                await websocket.send(frame)
                capture.consumed()

        async def geometry():
            while not capture.closed:
                info = target_info(capture.window_id, include_hidden=True)
                if info is None:
                    raise CaptureError('The shared window was closed.')
                await capture.refresh_geometry(info['kCGWindowBounds'])
                await asyncio.sleep(0.25)

        async def messages():
            async for raw in websocket:
                if not isinstance(raw, str) or json.loads(raw).get('type') == 'capture.stop':
                    return

        worker = asyncio.create_task(frames())
        reader = asyncio.create_task(messages())
        watcher = asyncio.create_task(geometry())
        try:
            done, _ = await asyncio.wait([worker, reader, watcher], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in (reader, watcher):
                task.cancel()
            await asyncio.gather(reader, watcher, return_exceptions=True)
    except ConnectionClosed:
        pass
    except Exception as error:
        log.info('window sharing stopped: %s', error)
        try:
            await websocket.send(json.dumps(dict(type='capture.error', error=str(error), cancelled=str(error) == 'cancelled')))
        except ConnectionClosed:
            pass
    finally:
        # Invalidate the lease before allowing any further remote input.
        await capture.close()
        for task in (worker, closed):
            if task and not task.done():
                task.cancel()
        for task in (worker, closed):
            if task:
                await asyncio.gather(task, return_exceptions=True)
        connection_status.remove(websocket)
        await websocket.close()
