"""Localhost WebSocket server that dispatches mouse commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from agent.macos import keyboard, mouse
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
    "window_unavailable": "Shared window is unavailable. Restore it to resume control.",
    "window_not_frontmost": "Remote control paused. Bring the shared window to the front to resume.",
    "window_obscured": "Remote control paused. Move the covering window away to resume.",
    "surface_unmatched": "Remote control paused. The shared window could not be identified.",
}


class WindowControlPaused(ProtocolError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(_PAUSE_MESSAGES[reason])


def _pause_response(reason: str, msg_type: str) -> dict[str, Any]:
    return {**error_response(_PAUSE_MESSAGES[reason], msg_type), "controlAllowed": False, "pauseReason": reason}


class MovePump:
    """Latest-wins cursor target; injects interpolated CGEvent moves off the WS read path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target: tuple[float, float] | None = None
        self._current: tuple[float, float] | None = None
        self._held_button: str | None = None
        self._task: asyncio.Task[None] | None = None

    def set_target(self, x: float, y: float) -> None:
        with self._lock:
            self._target = (float(x), float(y))

    def set_held_button(self, button: str | None) -> None:
        with self._lock:
            self._held_button = button

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
                self._target = None
            if target is None:
                continue
            try:
                self._current = mouse.move_interpolated(
                    target[0],
                    target[1],
                    origin=current,
                    drag_button=held,
                )
            except Exception:
                logger.exception("move pump failed")


_move_pump = MovePump()


def screen_info_response() -> dict[str, Any]:
    return ok_response("screen.info", **mouse.main_display_bounds())


def _pause_active_input() -> None:
    _move_pump.release_held()
    keyboard.release_all()


def dispatch(command: Any) -> dict[str, Any] | None:
    if isinstance(command, Ping):
        return ok_response("ping")

    if isinstance(command, ScreenInfo):
        return screen_info_response()

    if isinstance(command, SurfaceMatch):
        matched = mouse.match_captured_surface(command.surface, command.width, command.height, command.id)
        if not matched:
            reason = "window_unavailable" if command.id is not None else "surface_unmatched"
            return _pause_response(reason, "surface.match")
        reason = mouse.window_control_state(matched["id"])[0] if matched["kind"] == "window" else "ready"
        return ok_response(
            "surface.match", guardedInput=True,
            controlAllowed=reason == "ready",
            pauseReason=None if reason == "ready" else reason,
            message=None if reason == "ready" else _PAUSE_MESSAGES[reason],
            **matched,
        )

    if isinstance(command, MouseMove):
        _move_pump.set_target(command.x, command.y)
        return None

    if isinstance(command, MouseDown):
        _move_pump.cancel_pending()
        mouse.down(command.x, command.y, button=command.button)
        _move_pump.note_position(command.x, command.y)
        _move_pump.set_held_button(command.button)
        return ok_response("mouse.down")

    if isinstance(command, MouseUp):
        _move_pump.cancel_pending()
        mouse.up(command.x, command.y, button=command.button)
        _move_pump.note_position(command.x, command.y)
        _move_pump.set_held_button(None)
        return ok_response("mouse.up")

    if isinstance(command, MouseClick):
        _move_pump.cancel_pending()
        _move_pump.set_held_button(None)
        mouse.click(command.x, command.y, button=command.button)
        _move_pump.note_position(command.x, command.y)
        return ok_response("mouse.click")

    if isinstance(command, MouseScroll):
        _move_pump.cancel_pending()
        mouse.scroll(command.x, command.y, dx=command.dx, dy=command.dy)
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
    _move_pump.start()
    matched_surface: dict[str, Any] | None = None
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
                if msg_type in {"mouse.move", "mouse.down", "mouse.up", "mouse.click", "mouse.scroll", "key.down", "key.up"}:
                    if not matched_surface or data.get("surfaceId") != matched_surface["id"]:
                        raise ProtocolError("captured surface is not matched")
                    if matched_surface["kind"] == "window":
                        reason, bounds = mouse.window_control_state(matched_surface["id"])
                        if reason != "ready" or bounds is None:
                            raise WindowControlPaused(reason)
                        if msg_type.startswith("mouse."):
                            nx, ny = data.get("nx"), data.get("ny")
                            if (isinstance(nx, bool) or isinstance(ny, bool)
                                    or not isinstance(nx, (int, float)) or not isinstance(ny, (int, float))
                                    or not (0 <= nx <= 1 and 0 <= ny <= 1)):
                                raise ProtocolError("missing window-relative position")
                            data["x"] = float(bounds["X"]) + float(nx) * float(bounds["Width"])
                            data["y"] = float(bounds["Y"]) + float(ny) * float(bounds["Height"])
                command = parse_message(data)
                response = dispatch(command)
                if isinstance(command, SurfaceMatch):
                    matched_surface = response if response and response.get("ok") else None
                    if not response.get("controlAllowed", False):
                        _pause_active_input()
            except WindowControlPaused as exc:
                _pause_active_input()
                response = _pause_response(exc.reason, msg_type)
            except ProtocolError as exc:
                _pause_active_input()
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
                await websocket.send(json.dumps(response))
    finally:
        _move_pump.release_held()
        released = keyboard.release_all()
        if released:
            logger.info("released %s held key(s) on disconnect", released)
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
        from agent.macos.statusbar import run_status_app

        run_status_app(listen=listen)
    except KeyboardInterrupt:
        logger.info("shutting down")
    except Exception:
        logger.exception("agent failed to start UI")
        raise
