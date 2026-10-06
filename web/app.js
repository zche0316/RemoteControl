const $ = (id) => document.getElementById(id);

const FIREBASE_MOVE_KEY = "move";
const FIREBASE_KEY_KEY = "key";
const FIREBASE_BUTTON_KEY = "button";
const FIREBASE_CLICK_KEY = "click";
const FIREBASE_SCROLL_KEY = "scroll";
const FIREBASE_MIN_INTERVAL_MS = 50;
const HOVER_SUPPRESS_AFTER_DISCRETE_MS = 120;
const CLICK_SLOP_NORM = 0.04;

const logEl = $("log");
const statusEl = $("status");
const roleEl = $("role");
const canvas = $("pad");
const padWrap = $("padWrap");
const padStage = $("padStage");
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
let suppressHoverUntil = 0;
let lastRecordMoveAt = 0;
let lastRecordCoords = "";
let participantConnecting = false;
let pressStart = null;
let pressButton = "left";
let sentButtonDown = false;
let hostMoveSetLog = 0;
let recvMoveLog = 0;
let keysArmed = false;
const heldKeys = new Set();
const fbPrimed = new Set();

function hasSquidly() {
  return typeof window.SquidlyAPI !== "undefined";
}

function log(line, kind) {
  const t = new Date().toISOString().slice(11, 19);
  const prefix = kind ? `[${kind}] ` : "";
  logEl.textContent += `${t} ${prefix}${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function previewFb(value) {
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  try {
    const s = typeof value === "string" ? value : JSON.stringify(value);
    return `${typeof value} ${s.length > 140 ? `${s.slice(0, 137)}...` : s}`;
  } catch {
    return `${typeof value} ${String(value)}`;
  }
}

function sampleLog(count) {
  return count <= 5 || count % 20 === 0;
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
  const typeBtn = $("typeTestBtn");
  if (typeBtn) typeBtn.disabled = !on || role !== "local";
  updateGrabButton();
  updateReplayButtons();
}

function hostHint() {
  return "Host: pad moves the participant cursor. Click, drag, and scroll are separate events. Click the pad or Grab keyboard, then type (after clicking the field with the pad).";
}

function participantHint() {
  return "Participant: pad is a preview. Connects to the local agent and injects host hover/click/scroll/drag/keys. This Mac receives the cursor and keyboard.";
}

function localHint() {
  return padMode() === "live"
    ? "Live: use the pad to move/click/scroll like a real mouse. Click into a field, grab the keyboard, then type. Esc releases grab."
    : "Record: hover, click, drag, and scroll are captured. Nothing is sent until Replay. Keyboard is live-only.";
}

function isRecordMode() {
  return role === "local" && padMode() === "record";
}

function applyRole(next) {
  const normalized =
    next === "host" || next === "participant" ? next : "local";
  const changed = normalized !== role;
  if (changed) log(`role ${role} → ${normalized}`);
  if (changed && normalized !== "local") {
    setKeysArmed(false, { silent: true });
  }
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
  padStage.style.setProperty("--pad-ar", `${width} / ${height}`);
  padStage.style.setProperty("--pad-ar-num", String(width / height));
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

function formatTypedPayload(kind, mapped, extra = []) {
  return [nextEventSeq(), kind, mapped.nx.toFixed(4), mapped.ny.toFixed(4), ...extra].join("|");
}

function formatKeyPayload(action, code, key, modifiers) {
  if (action === "key.releaseAll") {
    return `${nextEventSeq()}|kr`;
  }
  const kind = action === "key.down" ? "kd" : "ku";
  const safeCode = String(code || "").replace(/\|/g, "");
  const safeKey = String(key || "").replace(/\|/g, "");
  const mods = (modifiers || []).join("+");
  return [nextEventSeq(), kind, safeCode, safeKey, mods].join("|");
}

function parseModList(raw) {
  if (!raw) return [];
  const allowed = new Set(["shift", "ctrl", "alt", "meta"]);
  const out = [];
  for (const part of String(raw).split("+")) {
    if (allowed.has(part) && !out.includes(part)) out.push(part);
  }
  return out;
}

function parseKeyPayload(value) {
  const parts = parsePipes(value, 2);
  if (!parts) return null;
  const kind = parts[1];
  if (kind === "kr") return { kind: "kr" };
  if ((kind === "kd" || kind === "ku") && parts.length >= 4) {
    const code = parts[2];
    if (!code) return null;
    return {
      kind,
      code,
      key: parts[3] ?? "",
      modifiers: parseModList(parts[4] ?? ""),
    };
  }
  return null;
}

function parseStreamPayload(value) {
  const parts = parsePipes(value, 2);
  if (!parts) return null;
  if (parts.length >= 2 && (parts[1] === "kd" || parts[1] === "ku" || parts[1] === "kr")) {
    return parseKeyPayload(value);
  }
  if (parts.length === 3) {
    const nx = Number(parts[1]);
    const ny = Number(parts[2]);
    if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
    return { kind: "move", mapped: mappedFromNorm(clamp01(nx), clamp01(ny)) };
  }
  if (parts.length < 4) return null;
  const kind = parts[1];
  const nx = Number(parts[2]);
  const ny = Number(parts[3]);
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  const mapped = mappedFromNorm(clamp01(nx), clamp01(ny));
  if (kind === "move" && parts.length === 4) {
    return { kind: "move", mapped };
  }
  if ((kind === "click" || kind === "down" || kind === "up") && parts.length === 5) {
    const button = parts[4];
    if (button !== "left" && button !== "right" && button !== "middle") return null;
    return { kind, mapped, button };
  }
  if (kind === "scroll" && parts.length === 6) {
    const dx = Number(parts[4]);
    const dy = Number(parts[5]);
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return null;
    return { kind: "scroll", mapped, dx: Math.round(dx), dy: Math.round(dy) };
  }
  return null;
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
  if (!hasSquidly() || role !== "host") {
    log(`fb set skipped ${key} squidly=${hasSquidly()} role=${role}`, "err");
    return false;
  }
  const parts = typeof payload === "string" ? payload.split("|") : [];
  const hoverMove = key === FIREBASE_MOVE_KEY && parts.length === 3;
  if (hoverMove) hostMoveSetLog += 1;
  if (!hoverMove || sampleLog(hostMoveSetLog)) {
    log(`fb set ${key} ${payload}`);
  }
  window.SquidlyAPI.firebaseSet(key, payload);
  return true;
}

function holdMoveAfterDiscrete() {
  pendingMove = null;
  lastMovePayload = "";
  suppressHoverUntil = performance.now() + HOVER_SUPPRESS_AFTER_DISCRETE_MS;
}

function publishFirebaseMove(mapped) {
  if (!hasSquidly() || role !== "host") return false;
  const now = performance.now();
  if (now < suppressHoverUntil) return false;
  if (now - lastFirebaseAt < FIREBASE_MIN_INTERVAL_MS) return false;
  const coordsKey = `${mapped.nx.toFixed(4)}|${mapped.ny.toFixed(4)}`;
  if (coordsKey === lastMovePayload) return false;
  lastFirebaseAt = now;
  lastMovePayload = coordsKey;
  publishFirebase(FIREBASE_MOVE_KEY, formatMovePayload(mapped.nx, mapped.ny));
  return true;
}

function emitMove(mapped) {
  if (role === "host" && pressed && !sentButtonDown) return false;
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
  const payload = formatTypedPayload(action, mapped, [button]);
  const type = action === "down" ? "mouse.down" : "mouse.up";
  recordEvent(type, mapped, { button });
  if (role === "host") {
    holdMoveAfterDiscrete();
    return publishFirebase(FIREBASE_MOVE_KEY, payload);
  }
  if (injectsLocally()) {
    return send({ type, x: mapped.x, y: mapped.y, button }, { silent: true });
  }
  return isRecordMode();
}

function emitClick(mapped, button) {
  const payload = formatTypedPayload("click", mapped, [button]);
  recordEvent("mouse.click", mapped, { button });
  if (role === "host") {
    holdMoveAfterDiscrete();
    return publishFirebase(FIREBASE_MOVE_KEY, payload);
  }
  if (injectsLocally()) {
    return send({ type: "mouse.click", x: mapped.x, y: mapped.y, button }, { silent: true });
  }
  return isRecordMode();
}

function emitScroll(mapped, dx, dy) {
  const now = performance.now();
  if (now - lastScrollAt < FIREBASE_MIN_INTERVAL_MS) {
    if (role === "host") holdMoveAfterDiscrete();
    return false;
  }
  lastScrollAt = now;
  const payload = formatTypedPayload("scroll", mapped, [dx, dy]);
  recordEvent("mouse.scroll", mapped, { dx, dy });
  if (role === "host") {
    holdMoveAfterDiscrete();
    return publishFirebase(FIREBASE_MOVE_KEY, payload);
  }
  if (injectsLocally()) {
    return send(
      { type: "mouse.scroll", x: mapped.x, y: mapped.y, dx, dy },
      { silent: true },
    );
  }
  return isRecordMode();
}

function eventModifiers(ev) {
  const mods = [];
  if (ev.shiftKey) mods.push("shift");
  if (ev.ctrlKey) mods.push("ctrl");
  if (ev.altKey) mods.push("alt");
  if (ev.metaKey) mods.push("meta");
  return mods;
}

function canArmKeys() {
  if (role === "host") return hasSquidly();
  if (role === "local") return !!ws && ws.readyState === WebSocket.OPEN;
  return false;
}

function canInjectKeys() {
  return role === "local" && !!ws && ws.readyState === WebSocket.OPEN;
}

function keyboardGrabbed() {
  return keysArmed && canArmKeys();
}

function updateGrabButton() {
  const btn = $("grabKeysBtn");
  if (!btn) return;
  if (role === "participant") {
    btn.hidden = true;
    btn.disabled = true;
    return;
  }
  btn.hidden = false;
  btn.disabled = !canArmKeys();
  btn.textContent = keysArmed ? "Release keyboard" : "Grab keyboard";
}

function setKeysArmed(on, { silent = false } = {}) {
  const next = Boolean(on) && canArmKeys();
  if (!next) releaseHeldKeys();
  const changed = next !== keysArmed;
  keysArmed = next;
  padWrap.classList.toggle("keys-grabbed", keysArmed);
  $("padKeys").hidden = !keysArmed;
  updateGrabButton();
  if (!silent && changed) {
    log(keysArmed ? "keyboard grabbed — type with your keyboard" : "keyboard released");
  }
}

function isEditableTarget(ev) {
  const t = ev.target;
  if (!t || t === canvas || t === padWrap || t === padStage) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  return false;
}

function blockedShortcut(ev) {
  return ev.metaKey && (ev.code === "KeyQ" || ev.key === "q" || ev.key === "Q");
}

function shouldIgnoreKeyEvent(ev) {
  if (ev.isComposing || ev.key === "Process" || ev.key === "Dead") return true;
  if (ev.repeat) return true;
  if (!ev.code) return true;
  return false;
}

function emitKey(action, ev) {
  const modifiers = eventModifiers(ev);
  const code = ev.code;
  const key = ev.key;
  log(`${action} ${code} ${key} [${modifiers.join("+") || "none"}]`);
  if (role === "host") {
    return publishFirebase(FIREBASE_KEY_KEY, formatKeyPayload(action, code, key, modifiers));
  }
  if (canInjectKeys()) {
    return send({ type: action, code, key, modifiers }, { silent: true });
  }
  return false;
}

function releaseHeldKeys() {
  const codes = [...heldKeys];
  heldKeys.clear();
  if (!codes.length) return;
  if (role === "host") {
    for (const code of codes) {
      publishFirebase(FIREBASE_KEY_KEY, formatKeyPayload("key.up", code, "", []));
    }
    publishFirebase(FIREBASE_KEY_KEY, formatKeyPayload("key.releaseAll"));
    log(`keys released (${codes.length})`);
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  for (const code of codes) {
    send({ type: "key.up", code, key: "", modifiers: [] }, { silent: true });
  }
  send({ type: "key.releaseAll" }, { silent: true });
  log(`keys released (${codes.length})`);
}

function charToKeyEvent(ch) {
  if (ch === " ") return { code: "Space", key: " ", modifiers: [] };
  if (ch === "\n") return { code: "Enter", key: "Enter", modifiers: [] };
  if (ch >= "a" && ch <= "z") {
    return { code: `Key${ch.toUpperCase()}`, key: ch, modifiers: [] };
  }
  if (ch >= "A" && ch <= "Z") {
    return { code: `Key${ch}`, key: ch, modifiers: ["shift"] };
  }
  return null;
}

async function typeHelloTest() {
  if (role !== "local") {
    log("type test is local only", "err");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("not connected", "err");
    return;
  }
  log("click TextEdit (or 备忘录) now — typing Hello in 2s");
  $("typeTestBtn").disabled = true;
  await new Promise((r) => setTimeout(r, 2000));
  if (role !== "local" || !ws || ws.readyState !== WebSocket.OPEN) {
    setConnected(ws && ws.readyState === WebSocket.OPEN);
    return;
  }
  const text = "Hello";
  const pause = () => new Promise((r) => setTimeout(r, 40));
  for (const ch of text) {
    const ev = charToKeyEvent(ch);
    if (!ev) continue;
    const shifted = ev.modifiers.includes("shift");
    if (shifted) {
      send(
        { type: "key.down", code: "ShiftLeft", key: "Shift", modifiers: ["shift"] },
        { silent: true },
      );
      await pause();
    }
    send(
      { type: "key.down", code: ev.code, key: ev.key, modifiers: ev.modifiers },
      { silent: true },
    );
    await pause();
    send({ type: "key.up", code: ev.code, key: ev.key, modifiers: ev.modifiers }, { silent: true });
    await pause();
    if (shifted) {
      send({ type: "key.up", code: "ShiftLeft", key: "Shift", modifiers: [] }, { silent: true });
      await pause();
    }
  }
  send({ type: "key.releaseAll" }, { silent: true });
  log(`typed ${JSON.stringify(text)} via key.down/up`);
  setConnected(true);
}

function injectRemote(obj, { silent = false } = {}) {
  const ok = send(obj, { silent: true });
  if (!ok) {
    log(`inject ${obj.type} failed (agent ws not open)`, "err");
    return false;
  }
  if (!silent) {
    if (obj.type.startsWith("key.")) {
      const extra = obj.code ? ` ${obj.code}` : "";
      log(`inject ${obj.type}${extra}`);
    } else {
      const extra =
        obj.type === "mouse.scroll"
          ? ` dx=${obj.dx} dy=${obj.dy}`
          : obj.button
            ? ` ${obj.button}`
            : "";
      log(
        `inject ${obj.type}${extra} → ${Math.round(obj.x)},${Math.round(obj.y)}`,
      );
    }
  }
  return true;
}

function applyRemoteMove(mapped) {
  showMapped(mapped);
  pushTrail(mapped);
  if (role === "participant") {
    injectRemote(
      { type: "mouse.move", x: mapped.x, y: mapped.y },
      { silent: !sampleLog(recvMoveLog) },
    );
  }
}

function applyRemoteButton(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  injectRemote({
    type: parsed.action === "down" ? "mouse.down" : "mouse.up",
    x: parsed.mapped.x,
    y: parsed.mapped.y,
    button: parsed.button,
  });
}

function applyRemoteClick(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  injectRemote({
    type: "mouse.click",
    x: parsed.mapped.x,
    y: parsed.mapped.y,
    button: parsed.button,
  });
}

function applyRemoteScroll(parsed) {
  showMapped(parsed.mapped);
  if (role !== "participant") return;
  injectRemote({
    type: "mouse.scroll",
    x: parsed.mapped.x,
    y: parsed.mapped.y,
    dx: parsed.dx,
    dy: parsed.dy,
  });
}

function applyRemoteKey(parsed) {
  if (role !== "participant") return;
  if (parsed.kind === "kr") {
    injectRemote({ type: "key.releaseAll" });
    return;
  }
  injectRemote({
    type: parsed.kind === "kd" ? "key.down" : "key.up",
    code: parsed.code,
    key: parsed.key,
    modifiers: parsed.modifiers || [],
  });
}

function onFirebaseValue(key, value) {
  if (!fbPrimed.has(key)) {
    fbPrimed.add(key);
    log(`fb on ${key} primed ${previewFb(value)} (stale, ignored)`);
    return;
  }
  const isMoveKey = key === FIREBASE_MOVE_KEY;
  if (value == null || value === "") {
    log(`fb on ${key} empty (initial)`);
    return;
  }
  if (isMoveKey) recvMoveLog += 1;
  const stream = isMoveKey ? parseStreamPayload(value) : null;
  const keyEvent = key === FIREBASE_KEY_KEY ? parseKeyPayload(value) : null;
  const hoverMove = stream?.kind === "move";
  const quietMove = hoverMove && !sampleLog(recvMoveLog);
  if (!quietMove) {
    log(`fb on ${key} role=${role} ${previewFb(value)}`);
  }
  if (role !== "participant") {
    if (!quietMove) log(`fb on ${key} ignored (role=${role})`);
    return;
  }

  if (key === FIREBASE_KEY_KEY) {
    if (!keyEvent) {
      log(`fb parse key failed ${previewFb(value)}`, "err");
      return;
    }
    applyRemoteKey(keyEvent);
    return;
  }

  if (isMoveKey) {
    if (!stream) {
      log(`fb parse stream failed ${previewFb(value)}`, "err");
      return;
    }
    if (stream.kind === "move") {
      applyRemoteMove(stream.mapped);
      return;
    }
    if (stream.kind === "kd" || stream.kind === "ku" || stream.kind === "kr") {
      applyRemoteKey(stream);
      return;
    }
    if (stream.kind === "down" || stream.kind === "up") {
      applyRemoteButton({ mapped: stream.mapped, action: stream.kind, button: stream.button });
      return;
    }
    if (stream.kind === "click") {
      applyRemoteClick({ mapped: stream.mapped, button: stream.button });
      return;
    }
    if (stream.kind === "scroll") {
      applyRemoteScroll({ mapped: stream.mapped, dx: stream.dx, dy: stream.dy });
    }
    return;
  }
  if (key === FIREBASE_BUTTON_KEY) {
    const parsed = parseButtonPayload(value);
    if (!parsed) {
      log(`fb parse button failed ${previewFb(value)}`, "err");
      return;
    }
    applyRemoteButton(parsed);
    return;
  }
  if (key === FIREBASE_CLICK_KEY) {
    const parsed = parseClickPayload(value);
    if (!parsed) {
      log(`fb parse click failed ${previewFb(value)}`, "err");
      return;
    }
    applyRemoteClick(parsed);
    return;
  }
  if (key === FIREBASE_SCROLL_KEY) {
    const parsed = parseScrollPayload(value);
    if (!parsed) {
      log(`fb parse scroll failed ${previewFb(value)}`, "err");
      return;
    }
    applyRemoteScroll(parsed);
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
    pressed = false;
    pendingMove = null;
    sentButtonDown = false;
    pressStart = null;
    heldKeys.clear();
    setKeysArmed(false, { silent: true });
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
        data.type === "mouse.scroll" ||
        data.type === "key.down" ||
        data.type === "key.up" ||
        data.type === "key.releaseAll")
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
  canvas.focus();
  queueMove(mapped);

  pressed = true;
  sentButtonDown = false;
  pressStart = mapped;
  pressButton = buttonName(ev.button);
  padWrap.classList.add("active");
  canvas.setPointerCapture(ev.pointerId);
  if (role === "host") {
    log(
      `pad down ${pressButton} ${mapped.nx.toFixed(3)},${mapped.ny.toFixed(3)}`,
    );
  }
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
    if (role === "host") log(`pad drag-down ${pressButton}`);
    emitButton(pressStart, "down", pressButton);
    sentButtonDown = true;
  }
});

function endPress(ev) {
  if (!pressed) return;
  const mapped = (ev && eventToMapped(ev)) || hover || pressStart;
  const kind = sentButtonDown ? `drag-up ${pressButton}` : `click ${pressButton}`;
  pressed = false;
  padWrap.classList.remove("active");
  if (mapped) {
    if (sentButtonDown) {
      emitButton(mapped, "up", pressButton);
    } else {
      emitClick(mapped, pressButton);
    }
  } else if (role === "host") {
    log(`pad ${kind} ignored (no coords)`, "err");
  }
  if (role === "host") log(`pad ${kind}`);
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
    showMapped(mapped);
    if (role === "host") pendingMove = null;
    const toLines = (delta) => {
      if (delta === 0) return 0;
      const lines = Math.max(1, Math.round(Math.abs(delta) / 40));
      return delta > 0 ? -lines : lines;
    };
    const dx = toLines(ev.deltaX);
    const dy = toLines(ev.deltaY);
    if (role === "host") log(`pad wheel dx=${dx} dy=${dy}`);
    emitScroll(mapped, dx, dy);
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
  setConnected(ws && ws.readyState === WebSocket.OPEN);
};
$("pingBtn").onclick = () => send({ type: "ping" });
$("typeTestBtn").onclick = () => typeHelloTest();
$("grabKeysBtn").onclick = () => {
  if (!canArmKeys()) return;
  setKeysArmed(!keysArmed);
};
$("moveBtn").onclick = () =>
  send({ type: "mouse.move", x: Number($("x").value), y: Number($("y").value) }, { silent: true });
$("clearPathBtn").onclick = () => {
  trail.length = 0;
  log("cleared pad path");
};
$("clearBtn").onclick = () => {
  logEl.textContent = "";
};

function fsElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function padIsFullscreen() {
  return fsElement() === padStage || padStage.classList.contains("expanded");
}

function setCssExpanded(on) {
  padStage.classList.toggle("expanded", on);
  document.body.classList.toggle("pad-expanded", on);
}

function updateFsButton() {
  const btn = $("padFsBtn");
  if (padIsFullscreen()) {
    btn.textContent = "×";
    btn.title = "Exit fullscreen";
  } else {
    btn.textContent = "Fullscreen";
    btn.title = "Fullscreen";
  }
}

async function exitPadFullscreen() {
  setCssExpanded(false);
  const current = fsElement();
  if (current) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) {
      try {
        await exit.call(document);
      } catch {
        /* ignore */
      }
    }
  }
  updateFsButton();
  resizeCanvas();
}

async function togglePadFullscreen() {
  if (padIsFullscreen()) {
    await exitPadFullscreen();
    return;
  }
  const req = padStage.requestFullscreen || padStage.webkitRequestFullscreen;
  if (req) {
    try {
      await req.call(padStage);
      updateFsButton();
      resizeCanvas();
      return;
    } catch {
      /* iframe / permission — fall through to CSS fill */
    }
  }
  setCssExpanded(true);
  updateFsButton();
  resizeCanvas();
}

$("padFsBtn").onclick = () => togglePadFullscreen();
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && padStage.classList.contains("expanded")) {
    exitPadFullscreen();
    return;
  }
  if (keyboardGrabbed() && ev.key === "Escape" && !padIsFullscreen()) {
    ev.preventDefault();
    setKeysArmed(false);
    return;
  }
  if (!keyboardGrabbed()) return;
  if (isEditableTarget(ev)) return;
  if (padIsFullscreen() && ev.key === "Escape") return;
  if (shouldIgnoreKeyEvent(ev)) {
    if (ev.repeat && heldKeys.has(ev.code)) ev.preventDefault();
    return;
  }
  if (blockedShortcut(ev)) {
    ev.preventDefault();
    log("blocked Cmd+Q", "err");
    return;
  }
  ev.preventDefault();
  if (heldKeys.has(ev.code)) return;
  heldKeys.add(ev.code);
  emitKey("key.down", ev);
});

document.addEventListener("keyup", (ev) => {
  if (!heldKeys.has(ev.code)) return;
  if (isEditableTarget(ev) && !keyboardGrabbed()) return;
  heldKeys.delete(ev.code);
  ev.preventDefault();
  emitKey("key.up", ev);
});

canvas.addEventListener("focus", () => {
  if (canArmKeys()) setKeysArmed(true);
});

canvas.addEventListener("blur", () => {
  releaseHeldKeys();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) releaseHeldKeys();
});

window.addEventListener("blur", () => {
  releaseHeldKeys();
});

document.addEventListener("fullscreenchange", () => {
  if (fsElement() === padStage) setCssExpanded(false);
  updateFsButton();
  resizeCanvas();
});
document.addEventListener("webkitfullscreenchange", () => {
  if (fsElement() === padStage) setCssExpanded(false);
  updateFsButton();
  resizeCanvas();
});

function initSquidly() {
  if (!hasSquidly()) {
    applyRole("local");
    log("SquidlyAPI absent — local record/replay or live inject");
    return;
  }

  const initial = window.session_info?.user;
  log(`session_info.user=${initial ?? "undefined"}`);
  applyRole(initial === "host" || initial === "participant" ? initial : "host");
  log(
    `SquidlyAPI present — mouse on firebase "${FIREBASE_MOVE_KEY}", keys on "${FIREBASE_KEY_KEY}"`,
  );

  window.SquidlyAPI.addSessionInfoListener((info) => {
    log(`sessionInfo ${previewFb(info)}`);
    if (info && (info.user === "host" || info.user === "participant")) {
      applyRole(info.user);
    }
  });

  window.SquidlyAPI.firebaseOnValue(FIREBASE_MOVE_KEY, (value) => {
    onFirebaseValue(FIREBASE_MOVE_KEY, value);
  });
  window.SquidlyAPI.firebaseOnValue(FIREBASE_KEY_KEY, (value) => {
    onFirebaseValue(FIREBASE_KEY_KEY, value);
  });
}

window.addEventListener("resize", resizeCanvas);
applyScreenInfo(screenBounds);
initSquidly();
requestAnimationFrame(tick);
