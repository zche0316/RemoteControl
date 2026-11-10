import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent.macos import shared_window as native


def info(window_id=42):
    return {"kCGWindowNumber": window_id, "kCGWindowOwnerPID": 100,
            "kCGWindowName": "Shared document", "kCGWindowBounds": {"X": 10, "Y": 20, "Width": 400, "Height": 300}}


class PresentationTests(unittest.TestCase):
    def test_frame_on_inactive_desktop_is_hidden_and_never_ordered_forward(self):
        frame = native.GreenFrame()
        frame.owner = owner = object()
        frame.panels = [Mock() for _ in range(4)]
        for panel in frame.panels:
            panel.isOnActiveSpace.return_value = False
        frame.show(owner, info()["kCGWindowBounds"])
        for panel in frame.panels:
            panel.orderOut_.assert_called_once_with(None)
            panel.orderFrontRegardless.assert_not_called()

    def test_space_switch_pauses_even_if_quartz_still_reports_window_onscreen(self):
        presentation = native.WindowPresentation()
        presentation.window, presentation.pid, presentation.window_id = "window", 100, 42
        presentation.paused_reason = None
        with (patch.object(native, "frame") as frame,
              patch.object(native, "target_info", return_value=info()),
              patch.object(native, "attribute", return_value=False),
              patch.object(presentation, "_raise_target") as raise_target):
            frame.on_active_space.return_value = False
            self.assertEqual(presentation._refresh(), "window_not_visible")
            self.assertEqual(presentation.paused_reason, "window_not_visible")
            frame.hide.assert_called_once_with(presentation)
            frame.on_active_space.return_value = True
            self.assertEqual(presentation._refresh(), "window_not_visible")
            frame.show.assert_not_called()
            raise_target.assert_not_called()

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

    def test_background_shared_window_stays_ready_without_changing_local_focus(self):
        presentation = native.WindowPresentation()
        presentation.pid, presentation.window_id = 100, 42
        presentation.app, presentation.window = "app", "original-window"
        presentation.paused_reason = None
        workspace = Mock()
        workspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 200
        with (patch.object(native, "target_info", return_value=info()),
              patch.object(native, "NSWorkspace", workspace),
              patch.object(native, "attribute", side_effect=lambda app, key: False if key == native.kAXMinimizedAttribute else "other-window"),
              patch.object(native, "CFEqual", return_value=False),
              patch.object(native, "frame") as frame,
              patch.object(presentation, "_raise_target", return_value=0) as raise_window):
            self.assertEqual(presentation._refresh(), "ready")
            self.assertIsNone(presentation.paused_reason)
            self.assertIsNotNone(presentation.timer)
            with patch.object(native, "CFEqual", return_value=True):
                self.assertEqual(presentation._refresh(), "ready")
            raise_window.assert_not_called()
            workspace.sharedWorkspace.assert_not_called()
            presentation.timer.invalidate()

    def test_stacking_raise_never_sets_focus_or_activates_app(self):
        presentation = native.WindowPresentation()
        presentation.window = "target"
        presentation.pid = 100
        with (patch.object(native, "AXUIElementPerformAction", return_value=0) as action,
              patch.object(native, "AXUIElementSetAttributeValue") as attribute,
              patch.object(native, "NSWorkspace") as workspace,
              patch.object(native, "NSRunningApplication") as application):
            workspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 200
            self.assertEqual(presentation._raise_target(), 0)
        action.assert_called_once_with("target", native.kAXRaiseAction)
        attribute.assert_not_called()
        application.runningApplicationWithProcessIdentifier_.assert_not_called()

    def test_raise_skips_another_active_window_of_the_same_app(self):
        presentation = native.WindowPresentation()
        presentation.pid, presentation.app, presentation.window = 100, "app", "shared"
        with (patch.object(native, "NSWorkspace") as workspace,
              patch.object(native, "attribute", return_value="meeting"),
              patch.object(native, "CFEqual", return_value=False),
              patch.object(native, "AXUIElementPerformAction") as action):
            workspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 100
            self.assertEqual(presentation._raise_target(), 0)
            action.assert_not_called()

    def test_manual_pause_never_raises_even_if_a_move_callback_arrives_late(self):
        presentation = native.WindowPresentation()
        presentation.window = "window"
        presentation.paused_reason = "paused_by_sharer"
        with patch.object(presentation, "_raise_target") as raise_window:
            self.assertEqual(presentation._refresh(), "paused_by_sharer")
            raise_window.assert_not_called()

    def test_minimize_latches_pause_without_restoring_or_raising(self):
        presentation = native.WindowPresentation()
        presentation.window, presentation.app = "window", "app"
        presentation.pid, presentation.window_id = 100, 42
        presentation.paused_reason = None
        presentation.recovering_since = 10
        with (patch.object(native, "target_info", return_value=info()),
              patch.object(native, "NSWorkspace"),
              patch.object(native, "NSRunningApplication"),
              patch.object(native, "attribute", return_value=True),
              patch.object(presentation, "_raise_target") as raise_target,
              patch.object(native, "AXUIElementSetAttributeValue") as set_attribute,
              patch.object(native.time, "monotonic", return_value=13),
              patch.object(native, "frame") as frame,
              patch.object(native, "AXIsProcessTrusted", return_value=True)):
            frame.owner = presentation
            self.assertEqual(presentation._refresh(), "window_minimized")
            self.assertEqual(presentation.paused_reason, "window_minimized")
            self.assertIsNone(presentation.timer)
            raise_target.assert_not_called()
            set_attribute.assert_not_called()

    def test_window_on_another_desktop_does_not_raise_and_requires_resume(self):
        presentation = native.WindowPresentation()
        presentation.window, presentation.app = "window", "app"
        presentation.pid, presentation.window_id = 100, 42
        presentation.paused_reason = None
        presentation.recovering_since = 0
        with (patch.object(native, "target_info", return_value=dict(info(), kCGWindowIsOnscreen=False)),
              patch.object(native, "attribute", return_value=False),
              patch.object(presentation, "_raise_target") as raise_target,
              patch.object(native, "frame") as frame,
              patch.object(native.time, "monotonic", return_value=1),
              patch.object(native, "AXIsProcessTrusted", return_value=True)):
            frame.owner = presentation
            self.assertEqual(presentation._refresh(), "window_not_visible")
            raise_target.assert_not_called()
            with patch.object(native, "target_info", return_value=info()):
                self.assertEqual(presentation._refresh(), "window_not_visible")
            self.assertEqual(presentation._activate(42), "window_not_visible")

    def test_minimize_animation_can_disappear_before_ax_minimized_updates(self):
        presentation = native.WindowPresentation()
        presentation.window, presentation.pid, presentation.window_id = "window", 100, 42
        presentation.paused_reason = None
        with (patch.object(native, "target_info", return_value=None),
              patch.object(native, "attribute", side_effect=[False, True]),
              patch.object(native, "frame"), patch.object(native, "NSTimer"),
              patch.object(presentation, "_raise_target") as raise_target,
              patch.object(native, "AXUIElementSetAttributeValue") as restore):
            self.assertEqual(presentation._refresh(), "preparing_window")
            self.assertEqual(presentation._refresh(), "window_minimized")
            raise_target.assert_not_called()
            restore.assert_not_called()

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
    async def test_covered_hover_does_not_pause_and_click_raises_then_uses_current_bounds(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        old = info()["kCGWindowBounds"]
        moved = dict(X=100, Y=200, Width=600, Height=500)
        order = []

        class Socket:
            remote_address = ("127.0.0.1", 1234)

            def __init__(self):
                self.sent = []

            async def send(self, raw):
                self.sent.append(json.loads(raw))

            async def __aiter__(self):
                for message in (dict(type="surface.match", surface="window", id=42, width=400, height=300, foregroundControl=True),
                                dict(type="mouse.move", surfaceId=42, nx=0.5, ny=0.5, x=210, y=170),
                                dict(type="mouse.click", surfaceId=42, nx=0.5, ny=0.5, x=210, y=170)):
                    yield json.dumps(message)

        async def focus():
            order.append("focus")

        def state(*args):
            order.append("hit")
            return ("ready", moved) if "focus" in order else ("window_obscured", old)

        socket, presentation = Socket(), Mock()
        presentation.activate = AsyncMock(return_value="ready")
        presentation.refresh = AsyncMock(return_value="ready")
        presentation.focus_for_input = AsyncMock(side_effect=focus)
        presentation.recover = AsyncMock()
        presentation.close = AsyncMock()
        with (patch.object(server, "WindowPresentation", return_value=presentation),
              patch.object(server._move_pump, "start"),
              patch.object(server.mouse, "match_captured_surface", return_value=matched),
              patch.object(server.mouse, "window_control_state", side_effect=state),
              patch.object(server.mouse, "click") as click,
              patch.object(server.keyboard, "release_all")):
            await server.handle_client(socket)
        self.assertEqual(order, ["hit", "focus", "hit"])
        presentation.focus_for_input.assert_awaited_once()
        presentation.recover.assert_not_awaited()
        click.assert_called_once_with(400.0, 450.0, button="left", window_id=42, clicks=1)
        self.assertTrue(socket.sent[-2]["dropped"])
        self.assertFalse(any(reply.get("pauseReason") for reply in socket.sent))

    async def test_keyboard_waits_for_focus_transition_without_repeated_activation(self):
        presentation = native.WindowPresentation()
        presentation.paused_reason = None
        with (patch.object(native, "frame") as frame,
              patch.object(presentation, "_focus_target_for_input") as focus,
              patch.object(presentation, "_has_keyboard_focus", side_effect=[False, True]),
              patch.object(native.asyncio, "sleep", new=AsyncMock()) as sleep):
            frame.owner = presentation
            await presentation.focus_for_input()
            focus.assert_called_once()
            sleep.assert_awaited_once_with(0.05)

    async def test_keyboard_requests_focus_then_checks_the_final_destination(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        for state in ("ready", "window_obscured"):
            with self.subTest(state=state):
                class Socket:
                    remote_address = ("127.0.0.1", 1234)

                    async def send(self, raw):
                        pass

                    async def __aiter__(self):
                        for message in (dict(type="surface.match", surface="window", id=42, width=400, height=300, foregroundControl=True),
                                        dict(type="key.down", surfaceId=42, code="KeyA", modifiers=[])):
                            yield json.dumps(message)

                presentation = Mock()
                presentation.activate = AsyncMock(return_value="ready")
                presentation.refresh = AsyncMock(return_value="ready")
                presentation.focus_for_input = AsyncMock()
                presentation.recover = AsyncMock(return_value="preparing_window")
                presentation.close = AsyncMock()
                with (patch.object(server, "WindowPresentation", return_value=presentation),
                      patch.object(server._move_pump, "start"),
                      patch.object(server.mouse, "match_captured_surface", return_value=matched),
                      patch.object(server.mouse, "window_control_state", return_value=(state, info()["kCGWindowBounds"])),
                      patch.object(server.mouse, "window_keyboard_focus_state", return_value="ready"),
                      patch.object(server.keyboard, "release_all"),
                      patch.object(server.keyboard, "down") as key_down):
                    await server.handle_client(Socket())
                if state == "ready":
                    presentation.focus_for_input.assert_awaited_once()
                    key_down.assert_called_once_with("KeyA", [])
                else:
                    presentation.focus_for_input.assert_awaited_once()
                    key_down.assert_not_called()

    async def test_ownership_transfers_only_after_old_connection_closes(self):
        old, new = native.WindowPresentation(), native.WindowPresentation()
        with (patch.object(native, "frame") as frame,
              patch.object(native, "AXIsProcessTrusted", return_value=True),
              patch.object(native, "target_info", return_value=info()),
              patch.object(native, "bind_ax_window", return_value=("app", "window")),
              patch.object(native, "AXUIElementSetAttributeValue"),
              patch.object(native, "NSRunningApplication"),
              patch.object(native.WindowPresentation, "_raise_target", return_value=0),
              patch.object(native.WindowPresentation, "_refresh", autospec=True,
                           side_effect=lambda self, **kwargs: self.paused_reason or "ready"),
              patch.object(native.asyncio, "sleep", new=AsyncMock())):
            frame.owner = None
            self.assertEqual(await old.activate(42), "ready")
            self.assertIs(frame.owner, old)
            self.assertEqual(await new.activate(42), "window_in_use")
            self.assertEqual(await new.refresh(), "window_in_use")
            self.assertIs(frame.owner, old)
            await old.close()
            self.assertEqual(await new.refresh(), "ready")
            self.assertIs(frame.owner, new)
            self.assertIsNone(new.waiting_window_id)
            self.assertEqual(new.window_id, 42)
            await old.close()
            self.assertIs(frame.owner, new)
            await new.close()

    async def test_waiting_socket_cannot_release_owners_input_or_replace_its_move_callback(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        match = dict(type="surface.match", surface="window", id=42, width=400, height=300, foregroundControl=True)

        class Socket:
            remote_address = ("127.0.0.1", 1234)

            async def send(self, raw):
                pass

            async def __aiter__(self):
                for message in (match, dict(match, id=42),
                                dict(type="mouse.click", surfaceId=42, nx=0.5, ny=0.5, x=210, y=170),
                                dict(type="key.releaseAll"), dict(type="surface.pause", surfaceId=42)):
                    yield json.dumps(message)

        presentation = Mock()
        presentation.owns_control = False
        presentation.activate = AsyncMock(return_value="window_in_use")
        presentation.refresh = AsyncMock(return_value="window_in_use")
        presentation.recover = AsyncMock(return_value="window_in_use")
        presentation.pause = AsyncMock(return_value="paused_by_sharer")
        presentation.close = AsyncMock()
        with (patch.object(server, "WindowPresentation", return_value=presentation),
              patch.object(server._move_pump, "start"),
              patch.object(server._move_pump, "set_blocked_handler") as handler,
              patch.object(server.mouse, "match_captured_surface", return_value=matched),
              patch.object(server, "_pause_active_input") as release,
              patch.object(server.keyboard, "release_all") as release_keys,
              patch.object(server.mouse, "click") as click):
            await server.handle_client(Socket())
        release.assert_not_called()
        release_keys.assert_not_called()
        handler.assert_not_called()
        click.assert_not_called()

    async def test_waiting_connection_retries_same_window_after_owner_releases(self):
        owner, waiting = native.WindowPresentation(), native.WindowPresentation()
        with patch.object(native, "frame") as frame:
            frame.owner = owner
            self.assertEqual(await waiting.activate(42), "window_in_use")
            self.assertEqual(waiting.waiting_window_id, 42)
            with patch.object(waiting, "_activate", wraps=waiting._activate) as activate:
                self.assertEqual(await waiting.refresh(), "window_in_use")
                activate.assert_not_called()
            self.assertIs(frame.owner, owner)
            await owner.close()
            self.assertIsNone(frame.owner)
            with patch.object(waiting, "activate", new=AsyncMock(return_value="ready")) as activate:
                self.assertEqual(await waiting.refresh(), "ready")
                activate.assert_awaited_once_with(42)

    async def test_manual_pause_or_disconnect_cancels_waiting_for_ownership(self):
        for action in ("pause", "close"):
            with self.subTest(action=action), patch.object(native, "frame") as frame:
                frame.owner = native.WindowPresentation()
                waiting = native.WindowPresentation()
                self.assertEqual(await waiting.activate(42), "window_in_use")
                await getattr(waiting, action)()
                self.assertIsNone(waiting.waiting_window_id)
                frame.owner = None
                with patch.object(waiting, "activate", new=AsyncMock()) as activate:
                    self.assertEqual(await waiting.refresh(), "paused_by_sharer" if action == "pause" else "window_unavailable")
                    activate.assert_not_awaited()

    async def test_unsafe_click_is_dropped_but_periodic_refresh_does_not_latch_occlusion(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        match = dict(type="surface.match", surface="window", id=42, width=400, height=300, foregroundControl=True)

        class Socket:
            remote_address = ("127.0.0.1", 1234)

            def __init__(self):
                self.sent = []

            async def send(self, raw):
                self.sent.append(json.loads(raw))

            async def __aiter__(self):
                for message in (match, dict(type="mouse.click", surfaceId=42, nx=0.5, ny=0.5,
                                           x=210, y=170), dict(match, id=42)):
                    yield json.dumps(message)

        socket = Socket()
        presentation = Mock()
        presentation.activate = AsyncMock(return_value="ready")
        presentation.refresh = AsyncMock(return_value="ready")
        presentation.focus_for_input = AsyncMock()
        presentation.recover = AsyncMock(return_value="preparing_window")
        presentation.pause = AsyncMock()
        presentation.close = AsyncMock()
        with (patch.object(server, "WindowPresentation", return_value=presentation),
              patch.object(server._move_pump, "start"),
              patch.object(server.mouse, "match_captured_surface", return_value=matched),
              patch.object(server.mouse, "window_control_state", return_value=("window_obscured", info()["kCGWindowBounds"])) as hit,
              patch.object(server.mouse, "click") as click,
              patch.object(server.keyboard, "release_all", return_value=0)):
            await server.handle_client(socket)
        click.assert_not_called()
        hit.assert_called_once()  # Input only; neither periodic match sampled it.
        presentation.recover.assert_awaited_once_with("window_obscured")
        presentation.pause.assert_not_awaited()
        self.assertEqual(socket.sent[-2]["pauseReason"], "preparing_window")
        self.assertTrue(socket.sent[-1]["controlAllowed"])

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

    async def test_manual_pause_blocks_input_until_explicit_resume(self):
        from agent import server
        matched = dict(id=42, kind="window", x=10, y=20, width=400, height=300)
        messages = [
            dict(type="surface.match", surface="window", id=42, width=400, height=300, foregroundControl=True),
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
        presentation.recover = AsyncMock(return_value="paused_by_sharer")
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

    async def test_disconnect_cannot_be_reactivated_by_late_input_failure(self):
        presentation = native.WindowPresentation()
        presentation.window = "window"
        presentation.paused_reason = None
        with (patch.object(native, "frame"),
              patch.object(presentation, "_raise_target") as raise_window):
            await presentation.close()
            self.assertEqual(await presentation.recover("window_obscured"), "window_unavailable")
            raise_window.assert_not_called()
            self.assertIsNone(presentation.timer)
