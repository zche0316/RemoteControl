import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent.macos import shared_window as native


def info(window_id=42):
    return {"kCGWindowNumber": window_id, "kCGWindowOwnerPID": 100,
            "kCGWindowName": "Shared document", "kCGWindowBounds": {"X": 10, "Y": 20, "Width": 400, "Height": 300}}


class PresentationTests(unittest.TestCase):
    def test_old_timer_callback_cannot_pause_a_new_activation(self):
        presentation = native.WindowPresentation()
        presentation.timer = Mock()
        with patch.object(presentation, "_refresh") as refresh:
            presentation._tick(Mock())
            refresh.assert_not_called()

    def test_binding_refuses_two_identical_windows_in_the_same_app(self):
        target = info()
        with (patch.object(native, "AXUIElementCreateApplication", return_value="app"),
              patch.object(native, "AXUIElementSetMessagingTimeout"),
              patch.object(native, "attribute", side_effect=lambda el, key: ["a", "b"] if el == "app" else "Shared document"),
              patch.object(native, "ax_bounds", return_value=target["kCGWindowBounds"])):
            self.assertEqual(native.bind_ax_window(target), (None, None))

    def test_same_app_other_window_pauses_and_does_not_auto_resume(self):
        presentation = native.WindowPresentation()
        presentation.pid, presentation.window_id = 100, 42
        presentation.app, presentation.window = "app", "original-window"
        presentation.paused_reason = None
        workspace = Mock()
        workspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 100
        with (patch.object(native, "target_info", return_value=info()),
              patch.object(native, "NSWorkspace", workspace),
              patch.object(native, "attribute", side_effect=lambda app, key: False if key == native.kAXMinimizedAttribute else "other-window"),
              patch.object(native, "CFEqual", return_value=False),
              patch.object(native, "frame") as frame,
              patch.object(native, "AXUIElementPerformAction") as raise_window):
            self.assertEqual(presentation._refresh(), "window_not_frontmost")
            # Merely putting the original window back must not undo the pause.
            with patch.object(native, "CFEqual", return_value=True):
                self.assertEqual(presentation._refresh(), "window_not_frontmost")
            raise_window.assert_not_called()
            frame.show.assert_not_called()

    def test_frame_keeps_four_non_key_strips_inside_visible_edges_on_negative_display(self):
        frame = native.GreenFrame()
        owner = object()
        frame.owner = owner
        frame.panels = [Mock() for _ in range(4)]
        bounds = {"X": -800, "Y": 100, "Width": 600, "Height": 400}
        with patch.object(native.mouse, "main_display_bounds", return_value={"height": 900}):
            frame.show(owner, bounds)
        expected = [((-800.0, 400.0), (3.0, 400.0)), ((-203.0, 400.0), (3.0, 400.0)),
                    ((-797.0, 400.0), (594.0, 3.0)), ((-797.0, 797.0), (594.0, 3.0))]
        for panel, rectangle in zip(frame.panels, expected):
            panel.setFrame_display_.assert_called_once_with(rectangle, True)
            panel.makeKeyAndOrderFront_.assert_not_called()

    def test_hit_test_skips_only_our_registered_click_through_frame(self):
        window_class = Mock()
        window_class.windowNumberAtPoint_belowWindowWithWindowNumber_.side_effect = [700, 42]
        with (patch.object(native.mouse, "_input_transparent_windows", frozenset({700})),
              patch.object(native.mouse, "NSApplication"),
              patch.object(native.mouse, "CGDisplayBounds", return_value=Mock(size=Mock(height=900))),
              patch.object(native.mouse, "NSWindow", window_class)):
            self.assertEqual(native.mouse.window_hit_at(10, 20), 42)
        self.assertEqual([call.args[1] for call in window_class.windowNumberAtPoint_belowWindowWithWindowNumber_.call_args_list], [0, 700])


class ForegroundProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_resume_waits_for_restore_and_retries_raise(self):
        presentation = native.WindowPresentation()
        with (patch.object(presentation, "_activate", return_value="ready"),
              patch.object(presentation, "_refresh", side_effect=["window_obscured", "ready", "ready", "ready", "ready"]),
              patch.object(presentation, "_raise_target") as raise_target,
              patch.object(native.asyncio, "sleep", new=AsyncMock()),
              patch.object(presentation, "pause", new=AsyncMock()) as pause):
            self.assertEqual(await presentation.activate(42), "ready")
            raise_target.assert_called_once()
            pause.assert_not_awaited()

    async def test_only_start_and_explicit_resume_raise_the_bound_window(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        messages = [
            dict(type="surface.match", surface="window", width=400, height=300, foregroundControl=True),
            dict(type="surface.pause", surfaceId=42),
            dict(type="mouse.click", surfaceId=42, x=210, y=170, nx=0.5, ny=0.5),
            dict(type="surface.activate", surfaceId=99),
            dict(type="surface.match", surface="window", width=400, height=300, id=42, foregroundControl=True),
            dict(type="surface.activate", surfaceId=42),
        ]

        class Socket:
            remote_address = ("127.0.0.1", 1234)

            def __init__(self):
                self.sent = []

            async def send(self, raw):
                self.sent.append(json.loads(raw))

            async def __aiter__(self):
                for message in messages:
                    yield json.dumps(message)

        socket = Socket()
        presentation = Mock()
        presentation.activate = AsyncMock(return_value="ready")
        presentation.pause = AsyncMock(return_value="paused_by_sharer")
        presentation.refresh = AsyncMock(return_value="paused_by_sharer")
        presentation.close = AsyncMock()
        with (patch.object(server, "WindowPresentation", return_value=presentation),
              patch.object(server._move_pump, "start"),
              patch.object(server.mouse, "match_captured_surface", return_value=matched),
              patch.object(server.mouse, "window_control_state", return_value=("ready", info()["kCGWindowBounds"])),
              patch.object(server.mouse, "click") as click,
              patch.object(server.keyboard, "release_all", return_value=0)):
            await server.handle_client(socket)
        self.assertEqual(presentation.activate.await_count, 2)
        self.assertEqual([call.args for call in presentation.activate.await_args_list], [(42,), (42,)])
        presentation.close.assert_awaited_once()
        self.assertFalse(socket.sent[-2]["controlAllowed"])
        self.assertEqual(socket.sent[-2]["pauseReason"], "paused_by_sharer")
        self.assertTrue(socket.sent[-1]["controlAllowed"])
        click.assert_not_called()
        self.assertTrue(any(reply.get("type") == "surface.activate" and not reply["ok"] for reply in socket.sent))

    async def test_inactive_session_cannot_hide_another_sessions_frame(self):
        old, current = native.WindowPresentation(), native.WindowPresentation()
        with patch.object(native, "frame") as frame:
            frame.owner = current
            await old.close()
            self.assertIs(frame.owner, current)
