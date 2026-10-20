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
