const $ = (id) => document.getElementById(id);

const FIREBASE_MOVE_KEY = "move";
const FIREBASE_BUTTON_KEY = "button";
const FIREBASE_CLICK_KEY = "click";
const FIREBASE_SCROLL_KEY = "scroll";
const FIREBASE_MIN_INTERVAL_MS = 50;
const CLICK_SLOP_NORM = 0.01;

const logEl = $("log");
const statusEl = $("status");
const roleEl = $("role");
const canvas = $("pad");
const padWrap = $("padWrap");
const padCoords = $("padCoords");
const ctx = canvas.getContext("2d");

let ws = null;
let pressed = false;
let pendingMove = null;
let hover = null;
let recording = [];
let recordStart = 0;
let replayHandle = 0;
let replayRaf = 0;
let replaying = false;
let replayIndex = 0;
let replayOrigin = 0;
const trail = [];
let screenBounds = {
  x: 0,
  y: 0,
  width: window.screen.width || 1440,
  height: window.screen.height || 900,
};

/** @type {"local" | "host" | "participant"} */
let role = "local";
let moveSeq = 0;
let eventSeq = 0;
let lastFirebaseAt = 0;
let lastMovePayload = "";
let lastScrollAt = 0;
let lastRecordMoveAt = 0;
let lastRecordCoords = "";
let participantConnecting = false;
let pressStart = null;
let pressButton = "left";
let sentButtonDown = false;

function hasSquidly() {
  return typeof window.SquidlyAPI !== "undefined";
}

function log(line, kind) {
  const t = new Date().toISOString().slice(11, 19);
  const prefix = kind ? `[${kind}] ` : "";
  logEl.textContent += `${t} ${prefix}${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function padMode() {
  return $("padMode").value;
}

function padIsInteractive() {
  return role !== "participant";
}

function injectsLocally() {
  return role === "local" && padMode() === "live";
}

function buttonName(button) {
  if (button === 1) return "middle";
  if (button === 2) return "right";
  return "left";
}

function updateReplayButtons() {
  const has = recording.length > 0;
  const on = ws && ws.readyState === WebSocket.OPEN;
  const localReplay = role === "local";
  $("replayBtn").disabled = !localReplay || !has || !on || replaying;
  $("stopReplayBtn").disabled = !replaying;
  $("copyRecBtn").disabled = !has;
}

function setConnected(on) {
  statusEl.textContent = on ? "connected" : "disconnected";
  statusEl.className = on ? "on" : "off";
  const allowManual = role !== "host";
  $("connectBtn").disabled = on || !allowManual;
  $("disconnectBtn").disabled = !on;
  $("pingBtn").disabled = !on;
  $("moveBtn").disabled = !on || role === "host";
  updateReplayButtons();
}

function hostHint() {
  return "Host: move on the pad to move the participant cursor (no button held). Click, drag, right/middle click, and scroll are separate events.";
}

function participantHint() {
  return "Participant: pad is a preview. Connects to the local agent and injects host hover/click/scroll/drag. This Mac's cursor is controlled.";
}

function localHint() {
  return padMode() === "live"
    ? "Live: hovering the pad moves the system cursor. Click, drag, and scroll inject immediately."
    : "Record: hover, click, drag, and scroll are captured. Nothing is sent until Replay.";
}

function isRecordMode() {
  return role === "local" && padMode() === "record";
}

function applyRole(next) {
  const normalized =
    next === "host" || next === "participant" ? next : "local";
  const changed = normalized !== role;
  role = normalized;
  roleEl.textContent = `role: ${role}`;
  roleEl.className = role;

  const squidly = hasSquidly();
  $("localControls").hidden = squidly && role !== "local";
  $("debugRow").hidden = role === "host";
  padWrap.classList.toggle("preview", role === "participant");

  if (role === "host") {
    $("padHint").textContent = hostHint();
    if (ws) ws.close();
    setConnected(false);
  } else if (role === "participant") {
    $("padHint").textContent = participantHint();
    setConnected(ws && ws.readyState === WebSocket.OPEN);
    if (changed || !ws || ws.readyState !== WebSocket.OPEN) {
      connectAgent();
    }
  } else {
    $("padHint").textContent = localHint();
    setConnected(ws && ws.readyState === WebSocket.OPEN);
  }
}

function dumpRecording() {
  $("recordingJson").value = recording
    .map((s) => {
      const row = { type: s.type, x: s.x, y: s.y, t: s.t, nx: s.nx, ny: s.ny };
      if (s.button) row.button = s.button;
      if (s.dx != null) row.dx = s.dx;
      if (s.dy != null) row.dy = s.dy;
      return JSON.stringify(row);
    })
    .join("\n");
  updateReplayButtons();
}

function replaySample(s) {
  hover = { nx: s.nx, ny: s.ny, x: s.x, y: s.y };
  const type = s.type || "mouse.move";
  if (type === "mouse.move") {
    send({ type, x: s.x, y: s.y }, { silent: true });
    return;
  }
  if (type === "mouse.click" || type === "mouse.down" || type === "mouse.up") {
    send({ type, x: s.x, y: s.y, button: s.button || "left" }, { silent: true });
    return;
  }
  if (type === "mouse.scroll") {
    send({ type, x: s.x, y: s.y, dx: s.dx || 0, dy: s.dy || 0 }, { silent: true });
  }
}

function stopReplay() {
  if (replayHandle) {
    clearTimeout(replayHandle);
    replayHandle = 0;
  }
  if (replayRaf) {
    cancelAnimationFrame(replayRaf);
    replayRaf = 0;
  }
  replaying = false;
  updateReplayButtons();
}

function replayTick() {
  if (!replaying) return;
  const elapsed = performance.now() - replayOrigin;
  while (replayIndex < recording.length && recording[replayIndex].t <= elapsed) {
    const s = recording[replayIndex];
    replaySample(s);
    replayIndex += 1;
  }
  if (replayIndex >= recording.length) {
    log(`replay done (${recording.length} events)`);
    stopReplay();
    return;
  }
  replayRaf = requestAnimationFrame(replayTick);
}

function startReplay() {
  if (role !== "local") {
    log("replay is only for local (no Squidly) testing", "err");
    return;
  }
  if (!recording.length) {
    log("no recording", "err");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("not connected", "err");
    return;
  }
  stopReplay();
  const delay = Math.max(0, Number($("replayDelay").value) || 0);
  log(
    `replay in ${delay}ms — ${recording.length} events, ${recording[recording.length - 1].t.toFixed(0)}ms`,
  );
  replaying = true;
  updateReplayButtons();
  replayHandle = setTimeout(() => {
    replayHandle = 0;
    replayIndex = 0;
    replayOrigin = performance.now();
    replayRaf = requestAnimationFrame(replayTick);
  }, delay);
}

function send(obj, { silent = false } = {}) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    if (!silent) log("not connected", "err");
    return false;
  }
  const raw = JSON.stringify(obj);
  if (!silent) log(`→ ${raw}`);
  ws.send(raw);
  return true;
}

function applyScreenInfo(info) {
  const x = Number(info.x);
  const y = Number(info.y);
  const width = Number(info.width);
  const height = Number(info.height);
  if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
    return;
  }
  screenBounds = { x, y, width, height };
  padWrap.style.aspectRatio = `${width} / ${height}`;
  resizeCanvas();
  padCoords.textContent = `screen ${Math.round(width)}×${Math.round(height)} @ ${Math.round(x)},${Math.round(y)}`;
}

function clamp01(v) {
  return Math.min(1, Math.max(0, v));
}

function mappedFromNorm(nx, ny) {
  return {
    nx,
    ny,
    x: screenBounds.x + nx * screenBounds.width,
    y: screenBounds.y + ny * screenBounds.height,
  };
}

function eventToMapped(ev) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const nx = clamp01((ev.clientX - rect.left) / rect.width);
  const ny = clamp01((ev.clientY - rect.top) / rect.height);
  return mappedFromNorm(nx, ny);
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

function drawPad() {
  resizeCanvas();
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = "#2c313c";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(w / 2, 0);
  ctx.lineTo(w / 2, h);
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.stroke();

  if (trail.length > 1) {
    ctx.beginPath();
    ctx.strokeStyle = pressed || replaying || role === "participant" ? "#3d9a6a" : "#5a6270";
    ctx.lineWidth = Math.max(1, w / 400);
    ctx.lineJoin = "round";
    trail.forEach((p, i) => {
      const x = p.nx * w;
      const y = p.ny * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  if (hover) {
    const x = hover.nx * w;
    const y = hover.ny * h;
    ctx.fillStyle = pressed || replaying || role === "participant" ? "#3d9a6a" : "#9aa0a6";
    ctx.beginPath();
    ctx.arc(x, y, Math.max(4, w / 120), 0, Math.PI * 2);
    ctx.fill();
  }
}

function pushTrail(mapped) {
  trail.push({ nx: mapped.nx, ny: mapped.ny });
  if (trail.length > 200) trail.shift();
}

function showMapped(mapped) {
  hover = mapped;
  $("x").value = String(Math.round(mapped.x));
  $("y").value = String(Math.round(mapped.y));
  padCoords.textContent =
    `${mapped.nx.toFixed(3)}, ${mapped.ny.toFixed(3)} → ` +
    `${Math.round(mapped.x)}, ${Math.round(mapped.y)}`;
}

function recordEvent(type, mapped, extra = {}) {
  if (!isRecordMode()) return;
  if (recordStart === 0) recordStart = performance.now();
  const row = {
    t: Math.round((performance.now() - recordStart) * 10) / 10,
    type,
    nx: mapped.nx,
    ny: mapped.ny,
    x: Math.round(mapped.x * 10) / 10,
    y: Math.round(mapped.y * 10) / 10,
    ...extra,
  };
  recording.push(row);
  if (recording.length > 4000) recording.shift();
  dumpRecording();
}

function queueMove(mapped) {
  pendingMove = mapped;
  pushTrail(mapped);
  showMapped(mapped);
}

function nextEventSeq() {
  eventSeq += 1;
  return eventSeq;
}

function formatMovePayload(nx, ny) {
  moveSeq += 1;
  return `${moveSeq}|${nx.toFixed(4)}|${ny.toFixed(4)}`;
}

function parsePipes(value, minParts) {
  if (typeof value !== "string") return null;
  const parts = value.split("|");
  if (parts.length < minParts) return null;
  return parts;
}

function parseMovePayload(value) {
  const parts = parsePipes(value, 3);
  if (!parts || parts.length !== 3) return null;
  const nx = Number(parts[1]);
  const ny = Number(parts[2]);
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  return mappedFromNorm(clamp01(nx), clamp01(ny));
}

function parseButtonPayload(value) {
  const parts = parsePipes(value, 5);
  if (!parts || parts.length !== 5) return null;
  const nx = Number(parts[1]);
  const ny = Number(parts[2]);
  const action = parts[3];
  const button = parts[4];
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  if (action !== "down" && action !== "up") return null;
  if (button !== "left" && button !== "right" && button !== "middle") return null;
  return { mapped: mappedFromNorm(clamp01(nx), clamp01(ny)), action, button };
}

function parseClickPayload(value) {
  const parts = parsePipes(value, 4);
  if (!parts || parts.length !== 4) return null;
  const nx = Number(parts[1]);
  const ny = Number(parts[2]);
  const button = parts[3];
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  if (button !== "left" && button !== "right" && button !== "middle") return null;
  return { mapped: mappedFromNorm(clamp01(nx), clamp01(ny)), button };
}

function parseScrollPayload(value) {
  const parts = parsePipes(value, 5);
  if (!parts || parts.length !== 5) return null;
  const nx = Number(parts[1]);
  const ny = Number(parts[2]);
  const dx = Number(parts[3]);
  const dy = Number(parts[4]);
  if (![nx, ny, dx, dy].every(Number.isFinite)) return null;
  return {
    mapped: mappedFromNorm(clamp01(nx), clamp01(ny)),
    dx: Math.round(dx),
    dy: Math.round(dy),
  };
}

function publishFirebase(key, payload) {
  if (!hasSquidly() || role !== "host") return false;
  window.SquidlyAPI.firebaseSet(key, payload);
  return true;
}

function publishFirebaseMove(mapped) {
  if (!hasSquidly() || role !== "host") return false;
  const now = performance.now();
  if (now - lastFirebaseAt < FIREBASE_MIN_INTERVAL_MS) return false;
  const coordsKey = `${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}`;
  if (coordsKey === lastMovePayload) return false;
  lastFirebaseAt = now;
  lastMovePayload = coordsKey;
  publishFirebase(FIREBASE_MOVE_KEY, formatMovePayload(mapped.nx, mapped.ny));
  return true;
}

function emitMove(mapped) {
  if (role === "host") return publishFirebaseMove(mapped);
  if (injectsLocally()) {
    return send({ type: "mouse.move", x: mapped.x, y: mapped.y }, { silent: true });
  }
  if (isRecordMode()) {
    const now = performance.now();
    if (now - lastRecordMoveAt < FIREBASE_MIN_INTERVAL_MS) return false;
    const coordsKey = `${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}`;
    if (coordsKey === lastRecordCoords) return false;
    lastRecordMoveAt = now;
    lastRecordCoords = coordsKey;
    recordEvent("mouse.move", mapped);
    return true;
  }
  return false;
}

function emitButton(mapped, action, button) {
  const payload = `${nextEventSeq()}|${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}|${action}|${button}`;
  const type = action === "down" ? "mouse.down" : "mouse.up";
  recordEvent(type, mapped, { button });
  if (role === "host") {
    const ok = publishFirebase(FIREBASE_BUTTON_KEY, payload);
    if (ok) log(`host ${action} ${button}`);
    return ok;
  }
  if (injectsLocally()) {
    return send({ type, x: mapped.x, y: mapped.y, button }, { silent: true });
  }
  return isRecordMode();
}

function emitClick(mapped, button) {
  const payload = `${nextEventSeq()}|${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}|${button}`;
  recordEvent("mouse.click", mapped, { button });
  if (role === "host") {
    const ok = publishFirebase(FIREBASE_CLICK_KEY, payload);
    if (ok) log(`host click ${button}`);
    return ok;
  }
  if (injectsLocally()) {
    return send({ type: "mouse.click", x: mapped.x, y: mapped.y, button }, { silent: true });
  }
  return isRecordMode();
}

function emitScroll(mapped, dx, dy) {
  const now = performance.now();
  if (now - lastScrollAt < FIREBASE_MIN_INTERVAL_MS) return false;
  lastScrollAt = now;
  const payload = `${nextEventSeq()}|${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}|${dx}|${dy}`;
  recordEvent("mouse.scroll", mapped, { dx, dy });
  if (role === "host") return publishFirebase(FIREBASE_SCROLL_KEY, payload);
  if (injectsLocally()) {
    return send(
      { type: "mouse.scroll", x: mapped.x, y: mapped.y, dx, dy },
      { silent: true },
    );
  }
  return isRecordMode();
}

function applyRemoteMove(mapped) {
  showMapped(mapped);
  pushTrail(mapped);
  if (role === "participant") {
    send({ type: "mouse.move", x: mapped.x, y: mapped.y }, { silent: true });
  }
}

function applyRemoteButton(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  send(
    {
      type: parsed.action === "down" ? "mouse.down" : "mouse.up",
      x: parsed.mapped.x,
      y: parsed.mapped.y,
      button: parsed.button,
    },
    { silent: true },
  );
}

function applyRemoteClick(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  send(
    {
      type: "mouse.click",
      x: parsed.mapped.x,
      y: parsed.mapped.y,
      button: parsed.button,
    },
    { silent: true },
  );
}

function applyRemoteScroll(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  send(
    {
      type: "mouse.scroll",
      x: parsed.mapped.x,
      y: parsed.mapped.y,
      dx: parsed.dx,
      dy: parsed.dy,
    },
    { silent: true },
  );
}

function connectAgent() {
  if (role === "host") return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  if (participantConnecting) return;
  const url = $("url").value.trim();
  log(`connecting ${url}`);
  participantConnecting = true;
  ws = new WebSocket(url);
  ws.onopen = () => {
    participantConnecting = false;
    setConnected(true);
    log("open");
    log("requesting screen.info (main display bounds for pad mapping)");
    send({ type: "screen.info" }, { silent: true });
  };
  ws.onclose = () => {
    participantConnecting = false;
    setConnected(false);
    pressed = false;
    pendingMove = null;
    sentButtonDown = false;
    pressStart = null;
    padWrap.classList.remove("active");
    log("closed");
    ws = null;
    if (role === "participant") {
      setTimeout(() => {
        if (role === "participant") connectAgent();
      }, 1500);
    }
  };
  ws.onerror = () => log("socket error", "err");
  ws.onmessage = (ev) => {
    let data = null;
    try {
      data = JSON.parse(ev.data);
    } catch {
      log(`← ${ev.data}`);
      return;
    }
    if (data && data.type === "screen.info") {
      if (data.ok) {
        applyScreenInfo(data);
        log(
          `← screen.info display ${Math.round(data.width)}×${Math.round(data.height)} ` +
            `origin (${Math.round(data.x)}, ${Math.round(data.y)})`,
        );
      } else {
        log(
          "agent does not support screen.info — quit RemoteAgent.app / python -m agent and start it again from this repo",
          "err",
        );
      }
      return;
    }
    if (
      data &&
      (data.type === "mouse.move" ||
        data.type === "mouse.click" ||
        data.type === "mouse.down" ||
        data.type === "mouse.up" ||
        data.type === "mouse.scroll")
    ) {
      return;
    }
    log(`← ${ev.data}`);
  };
}

canvas.addEventListener("contextmenu", (ev) => {
  if (!padIsInteractive()) return;
  ev.preventDefault();
});

canvas.addEventListener("pointerdown", (ev) => {
  if (!padIsInteractive()) return;
  const mapped = eventToMapped(ev);
  if (!mapped) return;
  ev.preventDefault();
  stopReplay();
  queueMove(mapped);

  pressed = true;
  sentButtonDown = false;
  pressStart = mapped;
  pressButton = buttonName(ev.button);
  padWrap.classList.add("active");
  canvas.setPointerCapture(ev.pointerId);
});

canvas.addEventListener("pointermove", (ev) => {
  if (!padIsInteractive() && role === "participant") return;
  const mapped = eventToMapped(ev);
  if (!mapped) return;
  if (!padIsInteractive()) {
    showMapped(mapped);
    return;
  }
  queueMove(mapped);
  if (!pressed || !pressStart) return;
  const dist = Math.hypot(mapped.nx - pressStart.nx, mapped.ny - pressStart.ny);
  if (!sentButtonDown && dist > CLICK_SLOP_NORM) {
    emitButton(pressStart, "down", pressButton);
    sentButtonDown = true;
  }
});

function endPress(ev) {
  if (!pressed) return;
  const mapped = (ev && eventToMapped(ev)) || hover || pressStart;
  pressed = false;
  padWrap.classList.remove("active");
  if (mapped) {
    if (sentButtonDown) {
      emitButton(mapped, "up", pressButton);
    } else {
      emitClick(mapped, pressButton);
    }
  }
  sentButtonDown = false;
  pressStart = null;
  if (ev && canvas.hasPointerCapture?.(ev.pointerId)) {
    canvas.releasePointerCapture(ev.pointerId);
  }
}

canvas.addEventListener("pointerup", endPress);
canvas.addEventListener("pointercancel", endPress);
canvas.addEventListener("lostpointercapture", endPress);

canvas.addEventListener(
  "wheel",
  (ev) => {
    if (!padIsInteractive()) return;
    ev.preventDefault();
    const mapped = eventToMapped(ev);
    if (!mapped) return;
    queueMove(mapped);
    const toLines = (delta) => {
      if (delta === 0) return 0;
      const lines = Math.max(1, Math.round(Math.abs(delta) / 40));
      return delta > 0 ? -lines : lines;
    };
    emitScroll(mapped, toLines(ev.deltaX), toLines(ev.deltaY));
  },
  { passive: false },
);

function tick() {
  if (pendingMove) {
    if (emitMove(pendingMove)) pendingMove = null;
  }
  drawPad();
  requestAnimationFrame(tick);
}

$("connectBtn").onclick = () => connectAgent();
$("disconnectBtn").onclick = () => {
  if (role === "participant") {
    log("participant stays connected to the local agent");
    return;
  }
  if (ws) ws.close();
};
$("replayBtn").onclick = () => startReplay();
$("stopReplayBtn").onclick = () => {
  log("replay stopped");
  stopReplay();
};
$("copyRecBtn").onclick = async () => {
  const text = $("recordingJson").value;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    log("copied recording JSON");
  } catch {
    $("recordingJson").select();
    log("select the JSON and copy manually", "err");
  }
};
$("padMode").onchange = () => {
  if (role === "local") {
    $("padHint").textContent = localHint();
  }
  recording = [];
  recordStart = 0;
  dumpRecording();
};
$("pingBtn").onclick = () => send({ type: "ping" });
$("moveBtn").onclick = () =>
  send({ type: "mouse.move", x: Number($("x").value), y: Number($("y").value) }, { silent: true });
$("clearPathBtn").onclick = () => {
  trail.length = 0;
  log("cleared pad path");
};
$("clearBtn").onclick = () => {
  logEl.textContent = "";
};

function initSquidly() {
  if (!hasSquidly()) {
    applyRole("local");
    log("SquidlyAPI absent — local record/replay or live inject");
    return;
  }

  const initial = window.session_info?.user;
  applyRole(initial === "host" || initial === "participant" ? initial : "host");
  log(
    `SquidlyAPI present — firebase keys "${FIREBASE_MOVE_KEY}", "${FIREBASE_BUTTON_KEY}", "${FIREBASE_CLICK_KEY}", "${FIREBASE_SCROLL_KEY}"`,
  );

  window.SquidlyAPI.addSessionInfoListener((info) => {
    if (info && (info.user === "host" || info.user === "participant")) {
      applyRole(info.user);
    }
  });

  window.SquidlyAPI.firebaseOnValue(FIREBASE_MOVE_KEY, (value) => {
    if (role !== "participant") return;
    const mapped = parseMovePayload(value);
    if (!mapped) return;
    applyRemoteMove(mapped);
  });
  window.SquidlyAPI.firebaseOnValue(FIREBASE_BUTTON_KEY, (value) => {
    if (role !== "participant") return;
    const parsed = parseButtonPayload(value);
    if (!parsed) return;
    applyRemoteButton(parsed);
  });
  window.SquidlyAPI.firebaseOnValue(FIREBASE_CLICK_KEY, (value) => {
    if (role !== "participant") return;
    const parsed = parseClickPayload(value);
    if (!parsed) return;
    applyRemoteClick(parsed);
  });
  window.SquidlyAPI.firebaseOnValue(FIREBASE_SCROLL_KEY, (value) => {
    if (role !== "participant") return;
    const parsed = parseScrollPayload(value);
    if (!parsed) return;
    applyRemoteScroll(parsed);
  });
}

window.addEventListener("resize", resizeCanvas);
applyScreenInfo(screenBounds);
initSquidly();
requestAnimationFrame(tick);
