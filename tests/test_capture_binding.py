import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from agent import server
from agent.macos import capture


class CaptureBindingTests(unittest.IsolatedAsyncioTestCase):
    async def run_messages(self, messages, lease):
        class Socket:
            remote_address = ('127.0.0.1', 1234)
            sent = []
            async def send(self, raw): self.sent.append(json.loads(raw))
            async def __aiter__(self):
                for message in messages:
                    if message == 'END':
                        lease.closed = True
                        continue
                    yield json.dumps(message)
        socket = Socket()
        matched = dict(id=42,kind='window',x=10,y=20,width=400,height=300)
        presentation = Mock(owns_control=True)
        for name in ('activate','refresh','focus_for_input','recover'):
            setattr(presentation,name,AsyncMock(return_value='ready'))
        presentation.recover = AsyncMock(side_effect=lambda reason:reason)
        presentation.pause = AsyncMock()
        presentation.close = AsyncMock()
        with (patch.object(capture, 'leases', {'lease':lease}),
              patch.object(server, 'WindowPresentation', return_value=presentation),
              patch.object(server._move_pump, 'start'),
              patch.object(server, '_pause_active_input'),
              patch.object(server.mouse, 'match_captured_surface', return_value=matched) as match,
              patch.object(server.mouse, 'window_control_state', return_value=('ready',dict(X=10,Y=20,Width=400,Height=300))),
              patch.object(server.mouse, 'click') as click):
            await server.handle_client(socket)
        return socket, presentation, match, click

    async def test_live_capture_supplies_exact_id_without_a_second_choice(self):
        lease = SimpleNamespace(closed=False,ended=False,window_id=42,stop_callbacks=set())
        socket,presentation,match,click = await self.run_messages([
            dict(type='surface.match',surface='window',captureId='lease',width=0,height=0,foregroundControl=True),
            dict(type='mouse.click',surfaceId=42,x=0,y=0,nx=0.5,ny=0.5),
        ],lease)
        self.assertEqual(match.call_args.args[-1],42)
        presentation.activate.assert_awaited_once_with(42)
        click.assert_called_once_with(210.0,170.0,button='left',window_id=42,clicks=1)
        self.assertTrue(socket.sent[1]['controlAllowed'])
        self.assertEqual(lease.stop_callbacks,set())

    async def test_stopped_capture_blocks_input_and_resume_even_if_window_still_exists(self):
        lease = SimpleNamespace(closed=False,ended=False,window_id=42,stop_callbacks=set())
        socket,presentation,match,click = await self.run_messages([
            dict(type='surface.match',surface='window',captureId='lease',width=0,height=0,foregroundControl=True),
            'END',
            dict(type='mouse.click',surfaceId=42,x=0,y=0,nx=0.5,ny=0.5),
            dict(type='surface.activate',surfaceId=42),
        ],lease)
        click.assert_not_called()
        presentation.activate.assert_awaited_once()
        self.assertEqual([r.get('pauseReason') for r in socket.sent[-2:]],['capture_unavailable']*2)

    async def test_unknown_lease_or_wrong_id_never_activates_a_window(self):
        for token,window_id in [('missing',42),('lease',43)]:
            lease = SimpleNamespace(closed=False,ended=False,window_id=42,stop_callbacks=set())
            socket,presentation,match,click = await self.run_messages([
                dict(type='surface.match',surface='window',captureId=token,id=window_id,width=0,height=0,foregroundControl=True),
            ],lease)
            presentation.activate.assert_not_awaited()
            match.assert_not_called()
            click.assert_not_called()
            self.assertFalse(socket.sent[-1]['ok'])
