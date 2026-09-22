import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from agent.macos import mouse


def window(window_id, x, y, width, height, pid=100):
    return {
        "kCGWindowNumber": window_id,
        "kCGWindowLayer": 0,
        "kCGWindowAlpha": 1,
        "kCGWindowOwnerPID": pid,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": width, "Height": height},
    }


class WindowSurfaceTests(unittest.TestCase):
    def test_known_window_survives_resize_without_matching_old_video_size(self):
        resized = {"id": 42, "x": 80, "y": 40, "width": 600, "height": 400}
        with patch.object(mouse, "list_windows", return_value=[resized]):
            result = mouse.match_captured_surface("window", 1000, 700, 42)
        self.assertEqual(result["width"], 600)
        self.assertEqual(result["id"], 42)

    def test_other_window_receiving_click_pauses_the_shared_window(self):
        target = window(42, 0, 0, 400, 400)
        cover = window(43, 0, 0, 200, 400, pid=200)
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover, target]),
              patch.object(mouse, "window_hit_at", return_value=43)):
            self.assertIsNone(mouse.window_point(42, 0.25, 0.5))
            self.assertIsNone(mouse.window_point(42, 0.75, 0.5))
            self.assertEqual(mouse.window_control_state(42)[0], "window_obscured")
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[target, cover]),
              patch.object(mouse, "window_hit_at", return_value=42)):
            self.assertEqual(mouse.window_point(42, 0.75, 0.5), (300, 200))
        with patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover]):
            self.assertIsNone(mouse.window_point(42, 0.75, 0.5))
            self.assertEqual(mouse.window_control_state(42)[0], "window_unavailable")

    def test_front_window_elsewhere_does_not_pause_pointer_control(self):
        target = window(42, 0, 0, 400, 400)
        elsewhere = window(43, 500, 0, 300, 300, pid=200)
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[elsewhere, target]),
              patch.object(mouse, "window_hit_at", return_value=42)):
            self.assertEqual(mouse.window_control_state(42)[0], "ready")
            self.assertEqual(mouse.window_point(42, 0.5, 0.5), (200, 200))

    def test_system_layer_does_not_pause_the_shared_window(self):
        target = window(42, 0, 0, 400, 400)
        cover = window(43, 0, 0, 200, 200, pid=200)
        cover["kCGWindowLayer"] = 3
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover, target]),
              patch.object(mouse, "window_hit_at", return_value=42)):
            self.assertEqual(mouse.window_control_state(42)[0], "ready")

    def test_same_app_helper_window_does_not_pause_the_shared_window(self):
        target = window(42, 0, 0, 400, 400, pid=100)
        helper = window(43, 10, 10, 66, 20, pid=100)
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[helper, target]),
              patch.object(mouse, "window_hit_at", return_value=42)):
            self.assertEqual(mouse.window_control_state(42)[0], "ready")

    def test_same_app_normal_window_cover_pauses_the_shared_window(self):
        target = window(42, 0, 0, 400, 400, pid=100)
        cover = window(43, 20, 20, 300, 300, pid=100)
        with (patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[cover, target]),
              patch.object(mouse, "window_hit_at", return_value=43)):
            self.assertEqual(mouse.window_control_state(42)[0], "window_obscured")
            self.assertIsNone(mouse.window_point(42, 0.5, 0.5))

    def test_hit_test_converts_quartz_to_appkit_coordinates(self):
        display = SimpleNamespace(size=SimpleNamespace(height=956))
        app = Mock()
        window_class = Mock()
        window_class.windowNumberAtPoint_belowWindowWithWindowNumber_.return_value = 42
        with (patch.object(mouse, "NSApplication", app),
              patch.object(mouse, "CGDisplayBounds", return_value=display),
              patch.object(mouse, "NSWindow", window_class)):
            self.assertEqual(mouse.window_hit_at(120, 200), 42)
        window_class.windowNumberAtPoint_belowWindowWithWindowNumber_.assert_called_once_with((120.0, 756.0), 0)

    def test_click_rechecks_hit_before_mouse_down(self):
        with (patch.object(mouse, "window_hit_at", side_effect=[42, 43]),
              patch.object(mouse, "_post"),
              patch.object(mouse, "_post_button") as post):
            with self.assertRaises(mouse.WindowHitMismatch):
                mouse.click(100, 100, window_id=42, pause_s=0)
        post.assert_not_called()

    def test_keyboard_rejects_another_focused_window_in_same_app(self):
        target = window(42, 0, 0, 400, 400, pid=100)
        workspace = Mock()
        workspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 100
        with (patch.object(mouse, "window_control_state", return_value=("ready", target["kCGWindowBounds"])),
              patch.object(mouse, "CGWindowListCopyWindowInfo", return_value=[target]),
              patch.object(mouse, "NSWorkspace", workspace),
              patch.object(mouse, "AXUIElementCreateSystemWide", return_value="system"),
              patch.object(mouse, "AXUIElementCreateApplication", return_value="app"),
              patch.object(mouse, "AXUIElementCopyElementAtPosition", return_value=(0, "element")),
              patch.object(mouse, "AXUIElementCopyAttributeValue", side_effect=[(0, "target-window"), (0, "other-window")]),
              patch.object(mouse, "CFEqual", return_value=False)):
            self.assertEqual(mouse.window_keyboard_focus_state(42, 0.5, 0.5), "window_not_frontmost")


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

    async def test_same_app_cover_cannot_receive_remote_click(self):
        import json
        from agent import server

        class Socket:
            remote_address = ('127.0.0.1', 1234)
            sent = []

            async def send(self, value):
                self.sent.append(json.loads(value))

            async def __aiter__(self):
                yield json.dumps({'type': 'surface.match', 'surface': 'window', 'width': 400, 'height': 400})
                yield json.dumps({'type': 'mouse.click', 'surfaceId': 42, 'nx': 0.25, 'ny': 0.5,
                                  'x': 100, 'y': 200})

        socket = Socket()
        target = window(42, 0, 0, 400, 400, pid=100)
        cover = window(43, 0, 0, 150, 400, pid=100)
        matched = {'id': 42, 'kind': 'window', 'x': 0, 'y': 0, 'width': 400, 'height': 400}
        with (patch.object(server._move_pump, 'start'),
              patch.object(server.mouse, 'match_captured_surface', return_value=matched),
              patch.object(server.mouse, 'CGWindowListCopyWindowInfo', return_value=[cover, target]),
              patch.object(server.mouse, 'window_hit_at', side_effect=lambda x, y: 43 if x < 150 else 42),
              patch.object(server.mouse, 'click') as click,
              patch.object(server.keyboard, 'release_all', return_value=0)):
            await server.handle_client(socket)
        click.assert_not_called()
        self.assertEqual(socket.sent[-1]['pauseReason'], 'window_obscured')
