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

from agent.macos import mouse
from agent.protocol import (
    MouseClick,
    MouseMove,
    MouseScroll,
    Ping,
    ProtocolError,
    error_response,
    ok_response,
    parse_message,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def dispatch(command: Any) -> dict[str, Any]:
    if isinstance(command, Ping):
        return ok_response("ping")

    if isinstance(command, MouseMove):
        mouse.move(command.x, command.y)
        return ok_response("mouse.move")

    if isinstance(command, MouseClick):
        mouse.click(command.x, command.y, button=command.button)
        return ok_response("mouse.click")

    if isinstance(command, MouseScroll):
        mouse.scroll(command.x, command.y, dx=command.dx, dy=command.dy)
        return ok_response("mouse.scroll")

    raise ProtocolError(f"unhandled command: {type(command).__name__}")


async def handle_client(websocket: ServerConnection) -> None:
    peer = websocket.remote_address
    logger.info("client connected: %s", peer)
    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps(error_response("invalid JSON")))
                continue

            try:
                command = parse_message(data)
                response = dispatch(command)
            except ProtocolError as exc:
                msg_type = data.get("type") if isinstance(data, dict) else None
                response = error_response(str(exc), msg_type if isinstance(msg_type, str) else None)
            except Exception as exc:
                logger.exception("dispatch failed")
                msg_type = data.get("type") if isinstance(data, dict) else None
                response = error_response(
                    f"dispatch failed: {exc}",
                    msg_type if isinstance(msg_type, str) else None,
                )

            await websocket.send(json.dumps(response))
    finally:
        logger.info("client disconnected: %s", peer)


async def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    async with serve(handle_client, host, port) as server:
        sockets = server.sockets or []
        bound = ", ".join(str(s.getsockname()) for s in sockets) or f"{host}:{port}"
        logger.info("agent listening on ws://%s (bind %s)", bound, f"{host}:{port}")
        await asyncio.Future()  # run forever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local macOS remote-control agent (WebSocket → CGEvent mouse)",
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
