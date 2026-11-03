# Launch screen assets

These files are bundled with RemoteAgent by `packaging/RemoteAgent.spec`.

- `logo.svg` and `logo-text.svg`: Squidly brand symbol and wordmark.
- `splash-photo.jpg`: 1040 × 1120 crop of the supplied launch photograph.
- `fonts/`: Poppins Regular and Bold, registered only for the running process.
  The font license is included in `fonts/OFL.txt`.

The native layout and three-second dismissal live in `agent/macos/splash.py`.
The panel is shown after Cocoa finishes launching and uses a transparent window
with a 12-point clipping radius on all four corners.

Local source artwork and preview exports are kept in ignored
`build/design-source/` and `build/previews/`; release packages remain in `dist/`.

## Floating companion

`floating.html` is a bundled, offline view hosted by `agent/macos/floating.py`.
It replaces the menu-bar entry with a draggable vertical capsule containing the
full animated Squidly logo and connection-status dot, with room for its tentacles.
The animation reuses login's `squidly-loader` geometry and motion.
The bridge accepts only messages from this bundled main-frame document;
connection metadata is rendered as plain text, never HTML.

The non-activating Cocoa panel keeps keyboard focus on the shared application,
remembers its collapsed position, and clamps both sizes to the usable display.
The panel supports multiple connections, copying a meeting ID, disconnecting
all connections, and quitting. No external web server is used for its UI.
The capsule and expanded panel follow system appearance. Dark mode uses
SquidlySession's `#252222` panel and `#1b1818` footer colors.
