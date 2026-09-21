import unittest
from unittest.mock import patch

from agent.macos import mouse


def window(window_id, x, y, width, height):
    return {
        "kCGWindowNumber": window_id,
        "kCGWindowLayer": 0,
        "kCGWindowAlpha": 1,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": width, "Height": height},
    }


class WindowSurfaceTests(unittest.TestCase):
    def test_known_window_survives_resize_without_matching_old_video_size(self):
        resized = {"id": 42, "x": 80, "y": 40, "width": 600, "height": 400}
        with patch.object(mouse, "list_windows", return_value=[resized]):
            result = mouse.match_captured_surface("window", 1000, 700, 42)
        self.assertEqual(result["width"], 600)
        self.assertEqual(result["id"], 42)

    def test_only_an_overlapping_front_window_pauses_the_shared_window(self):
        target = window(42, 0, 0, 400, 400)
        cover = window(43, 0, 0, 200, 400)
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover, target]):
            self.assertIsNone(mouse.window_point(42, 0.25, 0.5))
            self.assertIsNone(mouse.window_point(42, 0.75, 0.5))
            self.assertEqual(mouse.window_control_state(42)[0], "window_obscured")
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[target, cover]):
            self.assertEqual(mouse.window_point(42, 0.75, 0.5), (300, 200))
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover]):
            self.assertIsNone(mouse.window_point(42, 0.75, 0.5))
            self.assertEqual(mouse.window_control_state(42)[0], "window_unavailable")

    def test_front_window_elsewhere_does_not_pause_pointer_control(self):
        target = window(42, 0, 0, 400, 400)
        elsewhere = window(43, 500, 0, 300, 300)
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[elsewhere, target]):
            self.assertEqual(mouse.window_control_state(42)[0], "ready")
            self.assertEqual(mouse.window_point(42, 0.5, 0.5), (200, 200))

    def test_system_layer_does_not_pause_the_shared_window(self):
        target = window(42, 0, 0, 400, 400)
        cover = window(43, 0, 0, 200, 200)
        cover["kCGWindowLayer"] = 3
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover, target]):
            self.assertEqual(mouse.window_control_state(42)[0], "ready")


if __name__ == "__main__":
    unittest.main()

class AgentInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_covered_window_rejects_click_before_os_injection(self):
        import json
        from agent import server

        class Socket:
            remote_address = ('127.0.0.1', 1234)
            sent = []

            async def send(self, value):
                self.sent.append(json.loads(value))

            async def __aiter__(self):
                yield json.dumps({'type': 'surface.match', 'surface': 'window', 'width': 400, 'height': 400})
                yield json.dumps({'type': 'mouse.click', 'surfaceId': 42, 'nx': 0.5, 'ny': 0.5, 'x': 200, 'y': 200})

        socket = Socket()
        matched = {'id': 42, 'kind': 'window', 'x': 0, 'y': 0, 'width': 400, 'height': 400}
        with (patch.object(server._move_pump, 'start'),
              patch.object(server.mouse, 'match_captured_surface', return_value=matched),
              patch.object(server.mouse, 'window_control_state', return_value=('window_obscured', window(42, 0, 0, 400, 400)['kCGWindowBounds'])),
              patch.object(server.mouse, 'click') as click,
              patch.object(server.keyboard, 'release_all', return_value=0)):
            await server.handle_client(socket)
        click.assert_not_called()
        self.assertEqual(socket.sent[-1]['ok'], False)
        self.assertEqual(socket.sent[-1]['pauseReason'], 'window_obscured')
