# Foreground window control

Single-window control uses the same captured WindowServer ID throughout a
connection. The initial unique video-size match is unchanged. Once bound,
refreshes never select a different window by size.

The webpage requests `foregroundControl: true` on `surface.match`. The agent
uniquely associates the window with its owning application's Accessibility
window, activates that application, and raises the selected window. The agent
confirms the actual focused window before allowing input. An ambiguous native
window association pauses control instead of raising an arbitrary app window.

Four green, click-through, non-activating panels follow the window's edges.
The three-point inset keeps the border visible at display edges. The final hit
test skips only these explicitly registered, mouse-transparent panel IDs.
The panels never become the key window. Move
and resize updates come from current WindowServer bounds, including displays
with negative coordinates.

Switching to another application or another window of the same application
pauses control and removes the frame. Refreshing never raises a window or
automatically clears this pause. Only the sharing user can select **Resume
control** in SquidlySession to send `surface.activate` for the already-bound
ID. **Pause control** sends `surface.pause`; disconnecting closes the frame.
The existing remote-control toolbar button still ends the control session.

Window restoration is asynchronous. An explicit resume waits briefly for the
native restore/activation transition before reporting failure. Accessibility
permission is required. The capture-start browser focus hint is best effort;
native foreground control provides the ongoing session behavior.

The final per-point WindowServer hit check and the focused-window keyboard
check remain active. A successful Raise request does not guarantee that a
different window cannot appear before a subsequent click.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/manual_shared_window_smoke.py
.venv/bin/python tests/manual_shared_window_smoke.py --existing-window
```

The native smoke check creates two disposable Cocoa windows. It checks exact
window activation, click-through green strips, movement/resizing, switching to
another window in the same app, explicit resume, minimize/restore, and cleanup.
It sends no mouse clicks or keyboard input and restores the previously active
application on exit. This does not replace a two-person browser session test.
The optional `--existing-window` mode checks the already-frontmost real app,
without clicking, typing, moving, resizing, minimizing, or closing its window.
