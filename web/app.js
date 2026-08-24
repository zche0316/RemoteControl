const $ = (id) => document.getElementById(id);

const FIREBASE_MOVE_KEY = "move";
const FIREBASE_MIN_INTERVAL_MS = 50;

const logEl = $("log");
const statusEl = $("status");
const roleEl = $("role");
const canvas = $("pad");
const padWrap = $("padWrap");
const padCoords = $("padCoords");
const ctx = canvas.getContext("2d");

let ws = null;
let dragging = false;
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
let lastFirebaseAt = 0;
let lastMovePayload = "";
let participantConnecting = false;

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
    $("padHint").textContent =
      "Host: hold and drag to publish a path. This Mac is not injected.";
    if (ws) ws.close();
    setConnected(false);
  } else if (role === "participant") {
    $("padHint").textContent =
      "Participant: pad is a preview. Connects to the local agent and follows the host.";
    setConnected(ws && ws.readyState === WebSocket.OPEN);
    if (changed || !ws || ws.readyState !== WebSocket.OPEN) {
      connectAgent();
    }
  } else {
    $("padHint").textContent =
      padMode() === "live"
        ? "Live: hold and drag injects immediately. The system cursor will leave this window."
        : "Record: hold and drag to capture a path. Nothing is sent until Replay.";
    setConnected(ws && ws.readyState === WebSocket.OPEN);
  }
}

function dumpRecording() {
  $("recordingJson").value = recording
    .map((s) =>
      JSON.stringify({ type: "mouse.move", x: s.x, y: s.y, t: s.t, nx: s.nx, ny: s.ny }),
    )
    .join("\n");
  updateReplayButtons();
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
    send({ type: "mouse.move", x: s.x, y: s.y }, { silent: true });
    hover = { nx: s.nx, ny: s.ny, x: s.x, y: s.y };
    replayIndex += 1;
  }
  if (replayIndex >= recording.length) {
    log(`replay done (${recording.length} mouse.move)`);
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
    `replay in ${delay}ms — ${recording.length} mouse.move, ${recording[recording.length - 1].t.toFixed(0)}ms path`,
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
    ctx.strokeStyle = dragging || replaying || role === "participant" ? "#3d9a6a" : "#5a6270";
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
    ctx.fillStyle = dragging || replaying || role === "participant" ? "#3d9a6a" : "#9aa0a6";
    ctx.beginPath();
    ctx.arc(x, y, Math.max(4, w / 120), 0, Math.PI * 2);
    ctx.fill();
  }
}

function pushTrail(mapped) {
  trail.push({ nx: mapped.nx, ny: mapped.ny });
  if (trail.length > 200) trail.shift();
}

function samplePoint(mapped) {
  const t = performance.now() - recordStart;
  recording.push({
    t: Math.round(t * 10) / 10,
    nx: mapped.nx,
    ny: mapped.ny,
    x: Math.round(mapped.x * 10) / 10,
    y: Math.round(mapped.y * 10) / 10,
  });
  pendingMove = mapped;
  pushTrail(mapped);
  $("x").value = String(Math.round(mapped.x));
  $("y").value = String(Math.round(mapped.y));
}

function formatMovePayload(nx, ny) {
  moveSeq += 1;
  return `${moveSeq}|${nx.toFixed(4)}|${ny.toFixed(4)}`;
}

function parseMovePayload(value) {
  if (typeof value !== "string") return null;
  const parts = value.split("|");
  if (parts.length !== 3) return null;
  const nx = Number(parts[1]);
  const ny = Number(parts[2]);
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  return mappedFromNorm(clamp01(nx), clamp01(ny));
}

function publishFirebaseMove(mapped) {
  if (!hasSquidly() || role !== "host") return false;
  const now = performance.now();
  if (now - lastFirebaseAt < FIREBASE_MIN_INTERVAL_MS) return false;
  const coordsKey = `${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}`;
  if (coordsKey === lastMovePayload) return false;
  lastFirebaseAt = now;
  lastMovePayload = coordsKey;
  window.SquidlyAPI.firebaseSet(FIREBASE_MOVE_KEY, formatMovePayload(mapped.nx, mapped.ny));
  return true;
}

function applyRemoteMove(mapped) {
  hover = mapped;
  pushTrail(mapped);
  $("x").value = String(Math.round(mapped.x));
  $("y").value = String(Math.round(mapped.y));
  padCoords.textContent =
    `${mapped.nx.toFixed(3)}, ${mapped.ny.toFixed(3)} → ` +
    `${Math.round(mapped.x)}, ${Math.round(mapped.y)}`;
  if (role === "participant") {
    send({ type: "mouse.move", x: mapped.x, y: mapped.y }, { silent: true });
  }
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
    dragging = false;
    pendingMove = null;
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
    if (data && data.type === "mouse.move") return;
    log(`← ${ev.data}`);
  };
}

canvas.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  if (!padIsInteractive()) return;
  const mapped = eventToMapped(ev);
  if (!mapped) return;
  stopReplay();
  dragging = true;
  padWrap.classList.add("active");
  canvas.setPointerCapture(ev.pointerId);
  trail.length = 0;
  recording = [];
  recordStart = performance.now();
  hover = mapped;
  samplePoint(mapped);
});

canvas.addEventListener("pointermove", (ev) => {
  if (!padIsInteractive() && role === "participant") return;
  const mapped = eventToMapped(ev);
  if (!mapped) return;
  hover = mapped;
  padCoords.textContent =
    `${mapped.nx.toFixed(3)}, ${mapped.ny.toFixed(3)} → ` +
    `${Math.round(mapped.x)}, ${Math.round(mapped.y)}`;
  if (!dragging) return;
  samplePoint(mapped);
});

function endDrag(ev) {
  if (!dragging) return;
  dragging = false;
  padWrap.classList.remove("active");
  pendingMove = null;
  dumpRecording();
  if (recording.length) {
    const dt = recording[recording.length - 1].t;
    const extra = role === "host" ? " (published to participant)" : " (not sent)";
    log(`recorded ${recording.length} points over ${dt.toFixed(0)}ms${extra}`);
  }
  if (ev && canvas.hasPointerCapture?.(ev.pointerId)) {
    canvas.releasePointerCapture(ev.pointerId);
  }
}

canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);
canvas.addEventListener("lostpointercapture", endDrag);

function tick() {
  if (dragging && pendingMove) {
    if (role === "host") {
      if (publishFirebaseMove(pendingMove)) pendingMove = null;
    } else if (
      role === "local" &&
      padMode() === "live" &&
      ws &&
      ws.readyState === WebSocket.OPEN
    ) {
      send({ type: "mouse.move", x: pendingMove.x, y: pendingMove.y }, { silent: true });
      pendingMove = null;
    }
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
    $("padHint").textContent =
      padMode() === "live"
        ? "Live: hold and drag injects immediately. The system cursor will leave this window."
        : "Record: hold and drag to capture a path. Nothing is sent until Replay.";
  }
};
$("pingBtn").onclick = () => send({ type: "ping" });
$("moveBtn").onclick = () =>
  send({ type: "mouse.move", x: Number($("x").value), y: Number($("y").value) }, { silent: true });
$("clearBtn").onclick = () => {
  logEl.textContent = "";
};

function initSquidly() {
  if (!hasSquidly()) {
    applyRole("local");
    log("SquidlyAPI absent — local record/replay");
    return;
  }

  const initial = window.session_info?.user;
  applyRole(initial === "host" || initial === "participant" ? initial : "host");
  log(`SquidlyAPI present — firebase move key "${FIREBASE_MOVE_KEY}", no cursor listener`);

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
}

window.addEventListener("resize", resizeCanvas);
applyScreenInfo(screenBounds);
initSquidly();
requestAnimationFrame(tick);
