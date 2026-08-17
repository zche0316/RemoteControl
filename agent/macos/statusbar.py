"""Menu-bar (status item) presence while the agent is running.

LSUIElement / accessory policy: no Dock icon, only the extra-menu-bar item.
"""

from __future__ import annotations

import logging
import sys

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSSquareStatusItemLength,
    NSStatusBar,
)
from Foundation import NSObject

from agent.macos.statusicon import make_template_image

logger = logging.getLogger(__name__)

_retained: list[object] = []
_listen = ""
_installed = False
_quit_target = None


def _install_status_item(listen: str) -> None:
    global _installed
    if _installed:
        return
    _installed = True
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSSquareStatusItemLength)
    button = item.button()
    if button is not None:
        button.setImage_(make_template_image())
        button.setTitle_("")
        button.setToolTip_(f"RemoteAgent on ({listen})")
    menu = NSMenu.alloc().init()
    # Default auto-enable greys out terminate: on LSUIElement / accessory apps.
    menu.setAutoenablesItems_(False)
    title = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("RemoteAgent", None, "")
    title.setEnabled_(False)
    menu.addItem_(title)
    listening = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"On · {listen}", None, ""
    )
    listening.setEnabled_(False)
    menu.addItem_(listening)
    menu.addItem_(NSMenuItem.separatorItem())
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit RemoteAgent", "quitAgent:", "q"
    )
    quit_item.setTarget_(_quit_target)
    quit_item.setEnabled_(True)
    menu.addItem_(quit_item)
    item.setMenu_(menu)
    _retained.append(item)


class AgentAppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _notification) -> None:
        _install_status_item(_listen)
        logger.info("menu bar status item installed")

    def quitAgent_(self, _sender) -> None:
        logger.info("quit from status menu")
        NSApp.terminate_(None)

