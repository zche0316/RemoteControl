# Foreground window control

On macOS 15.2 or later with an updated RemoteAgent, the Share screen action uses RemoteAgent's ScreenCaptureKit picker.
One native single-window selection supplies both the SCContentFilter used to
capture pixels and the WindowServer ID used for control. The webpage never
matches video dimensions, window titles, or frontmost applications to identify
the shared window, and there is no second window picker.

A dedicated localhost `/capture` WebSocket sends `capture.info`, accepts one
`capture.start`, then returns `capture.started` with the native ID and a random
capture lease. Binary JPEG frames follow. The browser keeps only the newest
pending frame, draws it into a canvas stream, and passes that stream to the
existing meeting WebRTC connection. Native capture is limited to 20 fps and a
1920-pixel longest edge. Geometry changes update the stream configuration;
width and height are presentation data, never binding authorization.

Remote control is still off after sharing starts. When it is enabled through
the existing meeting flow, `surface.match` references the live `captureId`.
The Agent resolves that lease to the original window ID and rejects missing,
expired, or mismatched capture references. Closing the capture socket, stopping
from macOS, closing the window, or disconnecting the Agent revokes the lease,
releases owned held input and hides the control frame. Resume cannot revive an
ended capture. Pausing remote control alone keeps the video share running.

If native sharing is unavailable, the page offers an explicit browser tab/screen
sharing action. Browser sharing can continue without native window control;
the page explains that RemoteAgent window sharing is required to allow control.
Canceling the native picker leaves an existing share intact. Replacing a share
retires the previous capture only once the new selection produces a picture.

The webpage requests `foregroundControl: true` on `surface.match`. The agent
uniquely associates the window with its owning application's Accessibility
window and requests a raise without activating its application. The agent
confirms the actual focused window before keyboard input. An ambiguous native
window association pauses control instead of raising an arbitrary app window.

Four green, click-through, non-activating panels follow the window's edges.
The three-point inset keeps the border visible at display edges. The final hit
test skips only these explicitly registered, mouse-transparent panel IDs.
The panels never become the key window. Move
and resize updates come from current WindowServer bounds, including displays
with negative coordinates.

The frame is single-Space, without CanJoinAllSpaces. Its isOnActiveSpace check
also pauses/hides control when leaving that desktop, even if Quartz continues
to report the target as on-screen. Returning requires Resume. New control
owners get fresh panels; moving the target between Spaces can reattach them
on explicit Resume only when an OS hit confirms the target on the new desktop.

While control is active, a 100ms timer tracks the exact window and its frame.
It never raises windows, activates applications or sets main/focused window
attributes. Even a plain AXRaise can undo an in-progress minimize animation
before AXMinimized changes, so all raising is reserved for explicit actions.
Readiness depends on the bound window being available, not
on owning global focus. Local meeting controls can keep focus. Some apps make
AXRaise change their own focused window, so non-activating preparation also
skips raising over a different active window in the same application.

The public AXRaise API only raises as far as the application permits. It does
not provide a global always-on-top level for another app's window. A different
active application's window may remain above the shared one. Do not emulate
that missing capability by repeatedly acquiring focus. Covered remote points
remain blocked by the injection-time guard. Ordinary refreshes do not sample
occlusion or pause because the sharing user switched focus.

**Pause control** sends `surface.pause`, stops raising and hides the frame.
Only explicit **Resume control** clears that pause. Disconnect also stops
raising and closes the frame. The native companion's Disconnect action is
available without switching to the meeting webpage. A missing window,
permission failure or unavailable window pauses
control and asks for user action; ordinary focus switches do not.

Minimizing the window latches `window_minimized`, hides the frame
and stops raising. Polling never unminimizes it. Only explicit Resume restores
that same window. If the window still exists but is not on-screen, use
`window_not_visible`: return to its desktop (or show the hidden window), then
Resume. Neither background refresh nor Resume raises a non-minimized window
that remains off-screen. These control pauses do not stop the shared video.
An off-screen/disappearing window has a 400ms observation grace period (without
raising) to let minimize animations update AXMinimized before classifying it.

If a previous control connection is still closing, a new connection remembers
its requested window ID and waits without taking over. Periodic surface refresh
retries activation after the owner releases the frame; no Resume is required.
This also waits behind a genuinely active connection rather than stealing its
window. Manual pause or disconnect cancels the wait. Webpage callbacks from a
replaced WebSocket cannot overwrite the replacement's status or stop its timer.
Waiting window connections also cannot release the owner's held input or
replace its move-error callback. Owner cleanup releases held input before
making the window available to the next connection.
The existing remote-control toolbar button still ends the control session.

Window restoration is asynchronous. An explicit resume waits briefly for the
native restore transition before reporting failure. Accessibility
permission is required. Native presentation follows the bound window; explicit control actions can raise.

The final per-point WindowServer hit check and the focused-window keyboard
check remain active. A validated remote click, button-down, scroll or keyboard
request may activate/focus the already-bound window once. Resolve current
geometry and check the actual hit *after* that focus transition, then check again
at injection. Keyboard additionally verifies the focused window. Hover/move
never acquires focus; an obscured move is dropped without pausing the session
or stopping the publisher, so the following explicit click can still arrive.
An unsafe event is dropped, never replayed.
A hit mismatch triggers best-effort raising rather than a sticky occlusion pause.
A successful Raise request does not guarantee that a different window cannot
appear between the final check and event injection.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/manual_shared_window_smoke.py
.venv/bin/python tests/manual_unified_capture_smoke.py
.venv/bin/python tests/manual_shared_window_smoke.py --existing-window
.venv/bin/python tests/manual_shared_window_smoke.py --handoff-only
```

The native smoke check creates two disposable Cocoa windows. It checks exact
window activation, click-through green strips, movement/resizing, switching to
another window in the same app while preserving local focus, manual pause/resume,
automatic minimize/restore, and cleanup.
It sends one guarded click to a disposable fixture button to verify that an
explicit action raises the original window and never clicks its covering fixture.
It sends no keyboard input and restores the previously active application on exit.
This does not replace a two-person browser session test.
The optional `--existing-window` mode checks the already-frontmost real app,
without clicking, typing, moving, resizing, minimizing, or closing its window.
`--handoff-only` checks waiting/cancellation and automatic ownership transfer on
the already-frontmost window without creating competing test application windows.

## Capture replacement

The native picker disables inline source changes. Use Share screen again to
select another source. The explicit browser-sharing fallback requests
`surfaceSwitching: "exclude"` as a browser hint.
Selection revokes the old control locally and closes its Agent connection,
clearing the bound ID, queued input and stale callbacks. The new stream keeps
playing but requires a new control authorization. Both participants see a small
notice explaining that control was turned off for the changed content.

Observable track/source/label/capture-handle changes also revoke control before
forwarding input. Width/height changes alone do not revoke it. There is no
portable guarantee of detecting an otherwise unobservable browser-internal
source replacement, so native inline switching is not an advertised flow.

RTC control inputs carry their `controlId`. The sharer rejects legacy/unscoped
inputs and those from an old authorization, including after selecting a new
same-sized window. Both participants must reload the updated webpage. This
does not replace capture-lease validation or the per-event hit guards.
