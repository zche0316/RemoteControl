"""Synthetic keyboard events via CoreGraphics CGEventPost."""

from __future__ import annotations

import threading

from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventPost,
    CGEventSetFlags,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
    kCGHIDEventTap,
)

# UI Events `code` → macOS virtual key (ANSI / ISO common set).
_CODE_TO_VK: dict[str, int] = {
    "KeyA": 0x00,
    "KeyS": 0x01,
    "KeyD": 0x02,
    "KeyF": 0x03,
    "KeyH": 0x04,
    "KeyG": 0x05,
    "KeyZ": 0x06,
    "KeyX": 0x07,
    "KeyC": 0x08,
    "KeyV": 0x09,
    "KeyB": 0x0B,
    "KeyQ": 0x0C,
    "KeyW": 0x0D,
    "KeyE": 0x0E,
    "KeyR": 0x0F,
    "KeyY": 0x10,
    "KeyT": 0x11,
    "Digit1": 0x12,
    "Digit2": 0x13,
    "Digit3": 0x14,
    "Digit4": 0x15,
    "Digit6": 0x16,
    "Digit5": 0x17,
    "Equal": 0x18,
    "Digit9": 0x19,
    "Digit7": 0x1A,
    "Minus": 0x1B,
    "Digit8": 0x1C,
    "Digit0": 0x1D,
    "BracketRight": 0x1E,
    "KeyO": 0x1F,
    "KeyU": 0x20,
    "BracketLeft": 0x21,
    "KeyI": 0x22,
    "KeyP": 0x23,
    "Enter": 0x24,
    "KeyL": 0x25,
    "KeyJ": 0x26,
    "Quote": 0x27,
    "KeyK": 0x28,
    "Semicolon": 0x29,
    "Backslash": 0x2A,
    "Comma": 0x2B,
    "Slash": 0x2C,
    "KeyN": 0x2D,
    "KeyM": 0x2E,
    "Period": 0x2F,
    "Tab": 0x30,
    "Space": 0x31,
    "Backquote": 0x32,
    "Backspace": 0x33,
    "Escape": 0x35,
    "MetaRight": 0x36,
    "OSRight": 0x36,
    "MetaLeft": 0x37,
    "OSLeft": 0x37,
    "ShiftLeft": 0x38,
    "CapsLock": 0x39,
    "AltLeft": 0x3A,
    "ControlLeft": 0x3B,
    "ShiftRight": 0x3C,
    "AltRight": 0x3D,
    "ControlRight": 0x3E,
    "F17": 0x40,
    "NumpadDecimal": 0x41,
    "NumpadMultiply": 0x43,
    "NumpadAdd": 0x45,
    "NumLock": 0x47,
    "AudioVolumeUp": 0x48,
    "AudioVolumeDown": 0x49,
    "AudioVolumeMute": 0x4A,
    "NumpadDivide": 0x4B,
    "NumpadEnter": 0x4C,
    "NumpadSubtract": 0x4E,
    "F18": 0x4F,
    "F19": 0x50,
    "NumpadEqual": 0x51,
    "Numpad0": 0x52,
    "Numpad1": 0x53,
    "Numpad2": 0x54,
    "Numpad3": 0x55,
    "Numpad4": 0x56,
    "Numpad5": 0x57,
    "Numpad6": 0x58,
    "Numpad7": 0x59,
    "F20": 0x5A,
    "Numpad8": 0x5B,
    "Numpad9": 0x5C,
    "IntlBackslash": 0x0A,
    "F5": 0x60,
    "F6": 0x61,
    "F7": 0x62,
    "F3": 0x63,
    "F8": 0x64,
    "F9": 0x65,
    "F11": 0x67,
    "F13": 0x69,
    "F16": 0x6A,
    "F14": 0x6B,
    "F10": 0x6D,
    "ContextMenu": 0x6E,
    "F12": 0x6F,
    "F15": 0x71,
    "Help": 0x72,
    "Home": 0x73,
    "PageUp": 0x74,
    "Delete": 0x75,
    "F4": 0x76,
    "End": 0x77,
    "F2": 0x78,
    "PageDown": 0x79,
    "F1": 0x7A,
    "ArrowLeft": 0x7B,
    "ArrowRight": 0x7C,
    "ArrowDown": 0x7D,
    "ArrowUp": 0x7E,
}

_MOD_FLAGS = {
    "shift": int(kCGEventFlagMaskShift),
    "ctrl": int(kCGEventFlagMaskControl),
    "control": int(kCGEventFlagMaskControl),
    "alt": int(kCGEventFlagMaskAlternate),
    "option": int(kCGEventFlagMaskAlternate),
    "meta": int(kCGEventFlagMaskCommand),
    "cmd": int(kCGEventFlagMaskCommand),
    "command": int(kCGEventFlagMaskCommand),
}

_lock = threading.Lock()
_held: set[int] = set()


def _flags(modifiers: list[str]) -> int:
    bits = 0
    for name in modifiers:
        mask = _MOD_FLAGS.get(name.lower())
        if mask is not None:
            bits |= mask
    return bits


def _post(vk: int, key_down: bool, flags: int) -> None:
    event = CGEventCreateKeyboardEvent(None, vk, key_down)
    if event is None:
        raise RuntimeError("failed to create CGEvent")
    CGEventSetFlags(event, flags)
    CGEventPost(kCGHIDEventTap, event)


def _blocked(vk: int, flags: int) -> bool:
    """Refuse Command+Q so a grabbed pad cannot quit the frontmost app."""
    cmd = int(kCGEventFlagMaskCommand)
    if vk == 0x0C and (flags & cmd) == cmd:
        return True
    return False


def down(code: str, modifiers: list[str] | None = None) -> str:
    """Press a key identified by UI Events `code`. Returns the resolved action."""
    vk = _CODE_TO_VK.get(code)
    if vk is None:
        raise ValueError(f"unsupported key code: {code}")
    flags = _flags(modifiers or [])
    if _blocked(vk, flags):
        raise ValueError("blocked shortcut: Command+Q")
    with _lock:
        _post(vk, True, flags)
        _held.add(vk)
    return code


def up(code: str, modifiers: list[str] | None = None) -> str:
    """Release a key identified by UI Events `code`."""
    vk = _CODE_TO_VK.get(code)
    if vk is None:
        raise ValueError(f"unsupported key code: {code}")
    flags = _flags(modifiers or [])
    with _lock:
        _post(vk, False, flags)
        _held.discard(vk)
    return code


def release_all() -> int:
    """Release every key this process currently holds. Returns how many were lifted."""
    with _lock:
        held = list(_held)
        _held.clear()
    for vk in held:
        try:
            _post(vk, False, 0)
        except Exception:
            pass
    return len(held)
