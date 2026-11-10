"""Localhost WebSocket server that dispatches mouse commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from agent.macos import keyboard, mouse
from agent.macos.shared_window import WindowPresentation
from agent import connection_status
from agent.protocol import (
    KeyDown,
    KeyReleaseAll,
    KeyUp,
    MouseClick,
    MouseDown,
    MouseMove,
    MouseScroll,
    MouseUp,
    Ping,
    ProtocolError,
    ScreenInfo,
    SurfaceMatch,
    error_response,
    ok_response,
    parse_message,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_MOVE_HZ = 90.0

_PAUSE_MESSAGES = {
    "surface_selection_required": "Select the window or screen you are sharing to allow remote control.",
    "window_minimized": "The shared window was minimized. Select Resume control to restore it.",
    "window_not_visible": "The shared window is not visible on this desktop. Return to its desktop or show it, then select Resume control.",
    "window_unavailable": "Shared window is unavailable. Restore it to resume control.",
    "window_not_frontmost": "Remote control paused. Bring the shared window to the front to resume.",
    "window_obscured": "Remote control paused. Move the covering window away to resume.",
    "window_focus_unavailable": "Keyboard control paused. Allow Accessibility access for RemoteAgent to resume.",
    "surface_unmatched": "Remote control paused. The shared window could not be identified.",
    "paused_by_sharer": "Remote control paused by the sharer. Select Resume control when ready.",
    "window_in_use": "Waiting for another control connection to release the shared window. Control will continue automatically.",
    "checking": "Preparing the shared window for remote control.",
    "preparing_window": "Bringing the shared window back to the front. Control will continue automatically.",
    "window_binding_unavailable": "This window could not be prepared for control. Select the shared window again.",
}


class WindowControlPaused(ProtocolError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(_PAUSE_MESSAGES[reason])


def _control_status(reason: str) -> str:
    if reason == "surface_selection_required":
        return "Waiting · confirm the shared window in your meeting"
    if reason == "ready":
        return "Connected · ready"
    if reason == "preparing_window":
        return "Preparing · bringing shared window to front"
    if reason == "window_in_use":
        return "Waiting · another control connection is active"
    return "Paused · " + reason


def _pause_response(reason: str, msg_type: str) -> dict[str, Any]:
    return {**error_response(_PAUSE_MESSAGES[reason], msg_type), "controlAllowed": False, "pauseReason": reason}


class MovePump:
    """Latest-wins cursor target; injects interpolated CGEvent moves off the WS read path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target: tuple[float, float] | None = None
        self._current: tuple[float, float] | None = None
        self._held_button: str | None = None
        self._window_id: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._blocked_handler: Callable[[str], Awaitable[None]] | None = None

    def set_target(self, x: float, y: float, window_id: int | None = None) -> None:
        with self._lock:
            self._target = (float(x), float(y))
            self._window_id = window_id

    def set_held_button(self, button: str | None) -> None:
        with self._lock:
            self._held_button = button

    def set_blocked_handler(self, handler: Callable[[str], Awaitable[None]] | None) -> None:
        self._blocked_handler = handler

    def clear_blocked_handler(self, handler: Callable[[str], Awaitable[None]]) -> None:
        if self._blocked_handler is handler:
            self._blocked_handler = None

    def note_position(self, x: float, y: float) -> None:
        with self._lock:
            self._current = (float(x), float(y))
            self._target = None

    def cancel_pending(self) -> None:
        with self._lock:
            self._target = None

    def release_held(self) -> None:
        with self._lock:
            self._target = None
            button = self._held_button
            self._held_button = None
        if button is not None:
            mouse.release_button(button)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="mouse-move-pump")

    async def _run(self) -> None:
        interval = 1.0 / _MOVE_HZ
        while True:
            await asyncio.sleep(interval)
            with self._lock:
                target = self._target
                current = self._current
                held = self._held_button
                window_id = self._window_id
                self._target = None
            if target is None:
                continue
            try:
                if window_id is not None and held is None:
                    # A free cursor can enter the window from anywhere; only
                    # the destination must be safe. Drag steps remain guarded.
                    mouse.move(target[0], target[1], window_id=window_id)
                    self._current = target
                else:
                    self._current = mouse.move_interpolated(
                        target[0], target[1], origin=current,
                        drag_button=held, window_id=window_id,
                    )
            except mouse.WindowHitMismatch:
                self.release_held()
                handler = self._blocked_handler
                if handler is not None:
                    try:
                        await handler("window_obscured")
                    except Exception:
                        logger.exception("failed to publish move pause")
            except Exception:
                logger.exception("move pump failed")


_move_pump = MovePump()


def screen_info_response() -> dict[str, Any]:
    return ok_response("screen.info", explicitSurfaceBinding=True, **mouse.main_display_bounds())


def _pause_active_input() -> None:
    _move_pump.release_held()
    keyboard.release_all()


def dispatch(command: Any, window_id: int | None = None,
             match_point: tuple[float, float] = (0.5, 0.5),
             check_keyboard_focus: bool = False,
             foreground_maintenance: bool = False) -> dict[str, Any] | None:
    if isinstance(command, Ping):
        return ok_response("ping")

    if isinstance(command, ScreenInfo):
        return screen_info_response()

    if isinstance(command, SurfaceMatch):
        if command.id is None:
            items = mouse.list_displays() if command.surface == "monitor" else mouse.list_windows()
            # Selection is local to the sharer. Dimensions never authorize a
            # window; even an exact, unique size match requires confirmation.
            candidates = [{key: value for key, value in item.items() if key in
                           {"id", "name", "owner", "x", "y", "width", "height"}} for item in items]
            return {**_pause_response("surface_selection_required", "surface.match"),
                    "candidates": candidates, "explicitSurfaceBinding": True}
        matched = mouse.match_captured_surface(command.surface, command.width, command.height, command.id)
        if not matched:
            reason = "window_unavailable" if command.id is not None else "surface_unmatched"
            return _pause_response(reason, "surface.match")
        reason = (mouse.window_control_state(matched["id"], *match_point)[0]
                  if matched["kind"] == "window" and not foreground_maintenance else "ready")
        if reason == "ready" and check_keyboard_focus and matched["kind"] == "window" and not foreground_maintenance:
            reason = mouse.window_keyboard_focus_state(matched["id"], *match_point)
        return ok_response(
            "surface.match", guardedInput=True,
            controlAllowed=reason == "ready",
            pauseReason=None if reason == "ready" else reason,
            message=None if reason == "ready" else _PAUSE_MESSAGES[reason],
            **matched,
        )

    if isinstance(command, MouseMove):
        _move_pump.set_target(command.x, command.y, window_id)
        return None

    if isinstance(command, MouseDown):
        _move_pump.cancel_pending()
        mouse.down(command.x, command.y, button=command.button, window_id=window_id)
        _move_pump.note_position(command.x, command.y)
        _move_pump.set_held_button(command.button)
        return ok_response("mouse.down")

    if isinstance(command, MouseUp):
        _move_pump.cancel_pending()
        mouse.up(command.x, command.y, button=command.button, window_id=window_id)
        _move_pump.note_position(command.x, command.y)
        _move_pump.set_held_button(None)
        return ok_response("mouse.up")

    if isinstance(command, MouseClick):
        _move_pump.cancel_pending()
        _move_pump.set_held_button(None)
        mouse.click(command.x, command.y, button=command.button,
                    window_id=window_id, clicks=command.clicks)
        _move_pump.note_position(command.x, command.y)
        return ok_response("mouse.click")

    if isinstance(command, MouseScroll):
        _move_pump.cancel_pending()
        mouse.scroll(command.x, command.y, dx=command.dx, dy=command.dy, window_id=window_id)
        _move_pump.note_position(command.x, command.y)
        return ok_response("mouse.scroll")

    if isinstance(command, KeyDown):
        keyboard.down(command.code, list(command.modifiers))
        return ok_response("key.down")

    if isinstance(command, KeyUp):
        keyboard.up(command.code, list(command.modifiers))
        return ok_response("key.up")

    if isinstance(command, KeyReleaseAll):
        keyboard.release_all()
        return ok_response("key.releaseAll")

    raise ProtocolError(f"unhandled command: {type(command).__name__}")


async def handle_client(websocket: ServerConnection) -> None:
    peer = websocket.remote_address
    logger.info("client connected: %s", peer)
    loop = asyncio.get_running_loop()
    connection_status.add(websocket, lambda: loop.call_soon_threadsafe(
        lambda: asyncio.create_task(websocket.close(code=4000, reason="Disconnected from RemoteAgent menu"))))
    _move_pump.start()
    matched_surface: dict[str, Any] | None = None
    last_window_point = (0.5, 0.5)
    check_keyboard_focus = False
    presentation = WindowPresentation()
    foreground_control = False
    bound_surface = None
    legacy_input_owned = False

    def owns_input():
        return presentation.owns_control if foreground_control else legacy_input_owned

    def pause_own_input():
        if owns_input():
            _pause_active_input()

    async def publish_move_pause(reason: str) -> None:
        if not owns_input():
            return
        keyboard.release_all()
        if foreground_control:
            if reason == "window_obscured":
                # Hover must not pause the publisher before its click arrives.
                # MovePump already released any unsafe drag; never replay it.
                await websocket.send(json.dumps(ok_response("mouse.move", dropped=True)))
                return
            reason = await presentation.recover(reason)
        connection_status.update(websocket, state=_control_status(reason))
        await websocket.send(json.dumps(_pause_response(reason, "mouse.move")))

    try:
        await websocket.send(json.dumps(screen_info_response()))
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps(error_response("invalid JSON")))
                continue

            try:
                msg_type = data.get("type") if isinstance(data, dict) else None
                if msg_type == "session.identify":
                    connection_status.update(websocket, meeting=data.get("meeting"),
                                             browser=data.get("browser"), tab=data.get("tab"))
                    await websocket.send(json.dumps(ok_response("session.identify")))
                    continue
                if msg_type in {"surface.activate", "surface.pause"}:
                    if not foreground_control or not bound_surface or data.get("surfaceId") != bound_surface["id"]:
                        raise ProtocolError("captured window is not bound to this connection")
                    pause_own_input()
                    reason = (await presentation.activate(bound_surface["id"]) if msg_type == "surface.activate"
                              else await presentation.pause())
                    current = mouse.match_captured_surface("window", 0, 0, bound_surface["id"])
                    response = ok_response(msg_type, **(current or bound_surface), guardedInput=True,
                        foregroundControl=True, controlAllowed=reason == "ready",
                        pauseReason=None if reason == "ready" else reason)
                    if reason == "ready":
                        matched_surface = current or bound_surface
                    connection_status.update(websocket, state=_control_status(reason))
                    await websocket.send(json.dumps(response))
                    continue
                if msg_type in {"mouse.move", "mouse.down", "mouse.up", "mouse.click", "mouse.scroll", "key.down", "key.up"}:
                    parse_message(data)  # Validate the complete request before native focus changes.
                    if foreground_control:
                        reason = await presentation.refresh()
                        if reason != "ready":
                            raise WindowControlPaused(reason)
                    if not matched_surface or data.get("surfaceId") != matched_surface["id"]:
                        raise ProtocolError("captured surface is not matched")
                    if matched_surface["kind"] == "window":
                        requested_point = last_window_point
                        if msg_type.startswith("mouse."):
                            check_keyboard_focus = False
                            nx, ny = data.get("nx"), data.get("ny")
                            if (isinstance(nx, bool) or isinstance(ny, bool)
                                    or not isinstance(nx, (int, float)) or not isinstance(ny, (int, float))
                                    or not (0 <= nx <= 1 and 0 <= ny <= 1)):
                                raise ProtocolError("missing window-relative position")
                            requested_point = (float(nx), float(ny))
                        # Validate the latest requested point immediately before
                        # injection. Foreground maintenance never replaces this
                        # check, even when periodic matching says ready.
                        last_window_point = requested_point
                        if foreground_control and msg_type in {"mouse.click", "mouse.down", "mouse.scroll", "key.down", "key.up"}:
                            await presentation.focus_for_input()
                        # Raising may change window geometry. Resolve the actual
                        # destination only after activation, then check again
                        # inside mouse injection. Never use stale absolute x/y.
                        reason, bounds = mouse.window_control_state(matched_surface["id"], *requested_point)
                        if foreground_control and msg_type == "mouse.move" and reason == "window_obscured":
                            pause_own_input()
                            await websocket.send(json.dumps(ok_response("mouse.move", dropped=True)))
                            continue
                        if reason != "ready" or bounds is None:
                            raise WindowControlPaused(reason)
                        if msg_type.startswith("key."):
                            check_keyboard_focus = True
                            reason = mouse.window_keyboard_focus_state(matched_surface["id"], *last_window_point)
                            if reason != "ready":
                                raise WindowControlPaused(reason)
                        if msg_type.startswith("mouse."):
                            data["x"], data["y"] = mouse.window_relative_point(bounds, float(nx), float(ny))
                    if not foreground_control:
                        legacy_input_owned = True
                    _move_pump.set_blocked_handler(publish_move_pause)
                command = parse_message(data)
                if isinstance(command, KeyReleaseAll) and not owns_input():
                    await websocket.send(json.dumps(ok_response("key.releaseAll")))
                    continue
                if isinstance(command, SurfaceMatch) and foreground_control and bound_surface:
                    if command.surface != "window" or command.id not in (None, bound_surface["id"]):
                        raise ProtocolError("selecting another surface requires a new control connection")
                    # A refresh queued before the initial reply may not have
                    # received the ID yet. Pin it to the already-bound window.
                    command = SurfaceMatch(surface="window", width=command.width,
                                           height=command.height, id=bound_surface["id"])
                if isinstance(command, SurfaceMatch) and (
                    matched_surface is None or command.id != matched_surface["id"]
                ):
                    last_window_point = (0.5, 0.5)
                    check_keyboard_focus = False
                window_id = (matched_surface["id"] if matched_surface and matched_surface["kind"] == "window"
                             and not isinstance(command, SurfaceMatch) else None)
                response = dispatch(command, window_id=window_id, match_point=last_window_point,
                                    check_keyboard_focus=check_keyboard_focus,
                                    foreground_maintenance=foreground_control or data.get("foregroundControl") is True)
                if isinstance(command, SurfaceMatch):
                    if data.get("foregroundControl") is True and command.surface == "window":
                        if response.get("ok") and response.get("kind") == "window":
                            first_binding = bound_surface is None
                            bound_surface = {k: v for k, v in response.items() if k in
                                             {"id", "kind", "x", "y", "width", "height", "owner", "name"}}
                            foreground_control = True
                            if first_binding:
                                reason = (await presentation.pause() if data.get("startPaused") is True
                                          else await presentation.activate(bound_surface["id"]))
                            else:
                                reason = await presentation.refresh()
                            response.update(foregroundControl=True, controlAllowed=reason == "ready",
                                pauseReason=None if reason == "ready" else reason,
                                message=None if reason == "ready" else _PAUSE_MESSAGES[reason])
                        elif foreground_control:
                            # The target may be restoring. Keep the bound ID;
                            # presentation owns readiness, not a sampled hit.
                            reason = await presentation.refresh()
                            response = ok_response("surface.match", **bound_surface,
                                guardedInput=True, foregroundControl=True,
                                controlAllowed=False,
                                pauseReason="preparing_window" if reason == "ready" else reason)
                    matched_surface = response if response and response.get("ok") else None
                    if not response.get("controlAllowed", False):
                        pause_own_input()
            except WindowControlPaused as exc:
                pause_own_input()
                if foreground_control:
                    reason = await presentation.recover(exc.reason)
                else:
                    reason = exc.reason
                response = _pause_response(reason, msg_type)
            except mouse.WindowHitMismatch:
                pause_own_input()
                if foreground_control:
                    reason = await presentation.recover("window_obscured")
                else:
                    reason = "window_obscured"
                response = _pause_response(reason, msg_type)
            except ProtocolError as exc:
                pause_own_input()
                msg_type = data.get("type") if isinstance(data, dict) else None
                response = error_response(str(exc), msg_type if isinstance(msg_type, str) else None)
            except Exception as exc:
                logger.exception("dispatch failed")
                msg_type = data.get("type") if isinstance(data, dict) else None
                response = error_response(
                    f"dispatch failed: {exc}",
                    msg_type if isinstance(msg_type, str) else None,
                )

            if response is not None:
                if msg_type == "surface.match":
                    connection_status.update(websocket,
                        window=connection_status.surface_label(response),
                        state=_control_status('ready' if response.get('controlAllowed') else str(response.get('pauseReason') or 'matching shared window')))
                elif response.get('pauseReason'):
                    connection_status.update(websocket, state=_control_status(str(response['pauseReason'])))
                await websocket.send(json.dumps(response))
    except ConnectionClosed:
        logger.info("client connection closed: %s", peer)
    finally:
        # Release held input before publishing ownership as available. A waiting
        # connection must never release keys/buttons belonging to the owner.
        pause_own_input()
        _move_pump.clear_blocked_handler(publish_move_pause)
        if foreground_control:
            try:
                await presentation.close()
            except Exception:
                logger.exception("could not clear shared-window frame")
        connection_status.remove(websocket)
        logger.info("client disconnected: %s", peer)


async def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    async with serve(handle_client, host, port) as server:
        sockets = server.sockets or []
        bound = ", ".join(str(s.getsockname()) for s in sockets) or f"{host}:{port}"
        logger.info("agent listening on ws://%s (bind %s)", bound, f"{host}:{port}")
        await asyncio.Future()  # run forever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local macOS remote-control agent (WebSocket → CGEvent mouse/keyboard)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port (default: 8765)")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    log_path = Path.home() / "Library" / "Logs" / "RemoteAgent.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers or None,
    )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    _configure_logging(verbose=args.verbose)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "binding to %s — expects localhost only",
            args.host,
        )
    listen = f"{args.host}:{args.port}"
    worker = threading.Thread(
        target=lambda: asyncio.run(run_server(host=args.host, port=args.port)),
        name="agent-ws",
        daemon=True,
    )
    try:
        worker.start()
        from agent.macos.floating import run_floating_app

        run_floating_app(listen=listen)
    except KeyboardInterrupt:
        logger.info("shutting down")
    except Exception:
        logger.exception("agent failed to start UI")
        raise


if __name__ == "__main__":
    main()
