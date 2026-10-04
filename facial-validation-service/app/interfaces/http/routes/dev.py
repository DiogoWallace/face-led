# ruff: noqa: E501 - HTML/JS embutido da página de demonstração (local)
"""Página de demonstração do liveness ativo — SÓ em APP_ENV=local (ADR-010).

Referência de front com captura GUIADA: cada quadro é medido no worker
(`POST /dev/liveness/observe`, mesma regra da decisão) e a página só avança
quando o passo é cumprido. Os quadros são gravados SEM espelhamento, ~5 por
segundo, do início frontal até o fim, e enviados com a selfie. A decisão
continua sendo do worker, com todos os quadros. Fora do OpenAPI e nunca
montada em outro ambiente.
"""

import json
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.application.dto import CapturePolicy
from app.application.use_cases._common import validate_capture
from app.infrastructure.biometric.factories import active_liveness_parameters
from app.interfaces.http.dependencies import ContainerDep, CurrentTenant, read_capture

router = APIRouter(include_in_schema=False)


@router.post("/dev/liveness/observe")
async def observe_liveness_frame(
    frame: Annotated[UploadFile, File()], tenant: CurrentTenant, container: ContainerDep
) -> JSONResponse:
    """Giro e distância entre olhos de UM quadro (medidas geométricas, sem imagem)."""
    observer = container.frame_observer
    if observer is None:
        return JSONResponse({"available": False}, status_code=404)
    max_bytes = container.settings.liveness_frame_max_bytes
    capture = await read_capture(frame, max_bytes)
    validate_capture(capture, CapturePolicy(max_bytes=max_bytes))
    measured: dict[str, Any] | None = await observer.observe(capture.content, capture.content_type)
    if measured is None:
        return JSONResponse({"available": False}, status_code=503)
    return JSONResponse({"available": True, **measured})


@router.get("/dev/liveness", response_class=HTMLResponse)
async def liveness_demo(container: ContainerDep) -> HTMLResponse:
    params = asdict(active_liveness_parameters(container.settings))
    return HTMLResponse(_PAGE.replace("__PARAMS__", json.dumps(params)))


_PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Liveness — demonstração local</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}
 label{display:block;margin:8px 0 2px;font-size:14px} input,select{width:100%;padding:6px;box-sizing:border-box}
 .stage{position:relative;max-width:640px;border-radius:8px;overflow:hidden;background:#111}
 video{width:100%;transform:scaleX(-1);display:block}
 #overlay{position:absolute;inset:0;width:100%;height:100%}
 #step{font-size:26px;font-weight:600;min-height:40px;margin:12px 0 4px}
 #hint{font-size:16px;color:#a15c00;min-height:22px;margin-bottom:8px}
 .bar{height:10px;background:#ddd;border-radius:5px;margin:6px 0}.bar div{height:10px;width:0;border-radius:5px;transition:width .15s}
 #fill{background:#2a7} #budget{background:#8aa4c8}
 .barlabel{font-size:12px;color:#666}
 #steps{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
 #steps span{padding:4px 10px;border-radius:12px;background:#eee;font-size:14px}
 #steps span.now{background:#ffe08a}#steps span.ok{background:#bfe8c8}
 #debug{font-family:ui-monospace,monospace;font-size:13px;color:#555;margin-top:6px}
 pre{background:#f4f4f4;padding:10px;border-radius:6px;white-space:pre-wrap}
 button{margin-top:12px;padding:10px 18px;font-size:16px}
 .warn{background:#fff3cd;padding:8px;border-radius:6px;font-size:14px}
</style></head><body>
<h1>Prova de vida — demonstração local</h1>
<p class="warn">Só para teste em <code>APP_ENV=local</code>. O liveness ativo está
<b>PENDING CALIBRATION</b> (APCER/BPCER não medidos). Cada quadro é medido no worker e o passo
só avança quando é cumprido. O preview aparece espelhado para conforto; os quadros enviados
não são espelhados.</p>
<label>API key (X-API-Key)</label><input id="key" placeholder="fvs_...">
<label>subject_id</label><input id="subject" value="demo-1">
<label>Operação</label><select id="op">
 <option value="register">Cadastro (POST)</option>
 <option value="replace">Recadastro (PUT)</option>
 <option value="verify">Validação</option></select>
<label>Câmera</label><select id="camera"><option value="">Padrão do navegador</option></select>
<button id="go">Iniciar</button>
<div id="steps"></div>
<div id="step"></div><div id="hint"></div>
<div class="stage">
 <video id="v" autoplay playsinline muted></video>
 <svg id="overlay" viewBox="0 0 640 480" preserveAspectRatio="none">
  <defs><mask id="hole"><rect width="100%" height="100%" fill="white"/>
   <ellipse id="holeEllipse" cx="320" cy="240" rx="0" ry="0" fill="black"/></mask></defs>
  <rect width="100%" height="100%" fill="rgba(0,0,0,.45)" mask="url(#hole)"/>
  <ellipse id="oval" cx="320" cy="240" rx="0" ry="0" fill="none" stroke="#fff" stroke-width="4" stroke-dasharray="14 10"/>
  <rect id="facebox" x="0" y="0" width="0" height="0" fill="none" stroke="#7fd3ff" stroke-width="2" rx="6" visibility="hidden"/>
  <text id="arrow" x="0" y="0" font-size="110" font-weight="700" fill="#ffd34d" text-anchor="middle" dominant-baseline="middle" visibility="hidden"></text>
 </svg>
</div>
<div class="barlabel">Passo atual</div><div class="bar"><div id="fill"></div></div>
<div class="barlabel">Tempo de gravação restante</div><div class="bar"><div id="budget"></div></div>
<div id="debug"></div>
<pre id="out"></pre>
<script>
const P = __PARAMS__;
const INTERVAL = 200;                 // ~5 quadros por segundo
const POSITION_TIMEOUT = 30000, STEP_TIMEOUT = 12000, CENTER_TIMEOUT = 8000;
// Posição inicial: distância entre olhos / largura da imagem e rosto dentro do oval.
const START_MIN = 0.09, START_MAX = 0.20, CENTER_X_TOL = 0.10, CENTER_Y_TOL = 0.14;
const OVAL_FACE_WIDTH = 0.40;         // largura do oval ≈ largura do rosto na distância ideal
const LABEL = {TURN_LEFT: "Esquerda", TURN_RIGHT: "Direita", MOVE_CLOSER: "Aproximar"};
const COLOR = {idle: "#ffffff", bad: "#ffb020", good: "#3ccf6e"};
const $ = id => document.getElementById(id);

// ---------- câmera: escolha do dispositivo e mensagens claras de erro ----------
async function listCameras() {
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput");
    const sel = $("camera"), current = sel.value;
    sel.innerHTML = '<option value="">Padrão do navegador</option>' + devices.map((d, i) =>
      `<option value="${d.deviceId}">${d.label || "Câmera " + (i + 1)}</option>`).join("");
    if ([...sel.options].some(o => o.value === current)) sel.value = current;
    return devices.length;
  } catch (e) { return -1; }
}
async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia)
    throw new Error("este navegador não dá acesso à câmera nesta página (use http://127.0.0.1 ou localhost)");
  const id = $("camera").value;
  const preferred = {width: 640, height: 480, ...(id ? {deviceId: {exact: id}} : {facingMode: "user"})};
  try {
    return await navigator.mediaDevices.getUserMedia({video: preferred, audio: false});
  } catch (e) {
    // A câmera escolhida (ou as restrições) podem não existir mais: tenta qualquer câmera.
    if (e.name !== "NotFoundError" && e.name !== "OverconstrainedError") throw e;
    return await navigator.mediaDevices.getUserMedia({video: true, audio: false});
  }
}
function cameraMessage(e) {
  switch (e && e.name) {
    case "NotFoundError": case "OverconstrainedError":
      return "Nenhuma câmera encontrada. Confira se ela está conectada e ligada (atalho ou tampa de "
        + "privacidade) e se o Windows permite o acesso: Configurações → Privacidade e segurança → Câmera "
        + "→ 'Permitir que aplicativos da área de trabalho acessem a câmera'.";
    case "NotReadableError":
      return "A câmera está em uso por outro aplicativo (Teams, Zoom, outra aba…). Feche-o e tente de novo.";
    case "NotAllowedError":
      return "O acesso à câmera foi negado. Libere no ícone de câmera da barra de endereço e tente de novo.";
    default: return null;
  }
}
listCameras();
navigator.mediaDevices?.addEventListener?.("devicechange", listCameras);
const log = m => { $("out").textContent += m + "\\n"; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

class Abort extends Error {}

// ---------- guia desenhada sobre o vídeo (coordenadas da imagem; preview espelhado) ----------
const G = {w: 640, h: 480, scale: 1, color: COLOR.idle};
function setupOverlay(w, h) {
  G.w = w; G.h = h; $("overlay").setAttribute("viewBox", `0 0 ${w} ${h}`);
  drawOval(1, COLOR.idle); hideFace(); arrow(null);
}
function drawOval(scale, color) {
  G.scale = scale; G.color = color;
  const rx = OVAL_FACE_WIDTH * G.w / 2 * scale, ry = rx * 1.3;
  for (const id of ["oval", "holeEllipse"]) {
    const e = $(id); e.setAttribute("cx", G.w / 2); e.setAttribute("cy", G.h / 2);
    e.setAttribute("rx", rx); e.setAttribute("ry", Math.min(ry, G.h / 2 - 4));
  }
  $("oval").setAttribute("stroke", color);
  $("oval").setAttribute("stroke-dasharray", color === COLOR.good ? "none" : "14 10");
}
function showFace(o) {
  if (!o.box) return hideFace();
  const [x, y, w, h] = o.box, r = $("facebox");
  r.setAttribute("x", (1 - x - w) * G.w); r.setAttribute("y", y * G.h);   // espelhado como o preview
  r.setAttribute("width", w * G.w); r.setAttribute("height", h * G.h);
  r.setAttribute("visibility", "visible");
}
function hideFace() { $("facebox").setAttribute("visibility", "hidden"); }
function arrow(step) {
  const a = $("arrow");
  if (step !== "TURN_LEFT" && step !== "TURN_RIGHT") return a.setAttribute("visibility", "hidden");
  // No preview espelhado, a esquerda DA PESSOA fica à esquerda da tela.
  const left = step === "TURN_LEFT";
  a.textContent = left ? "◀" : "▶";
  a.setAttribute("x", left ? G.w * 0.1 : G.w * 0.9); a.setAttribute("y", G.h / 2);
  a.setAttribute("visibility", "visible");
}

function grab(video, canvas) {
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise(r => canvas.toBlob(r, "image/jpeg", 0.85));
}

function makeCapture(key, video, canvas, maxFrames) {
  const c = {frames: [], recording: false, latest: null, seq: 0, waiters: [], stopped: false,
             inflight: false, failures: 0};
  const notify = () => { for (const w of [...c.waiters]) w(); };
  c.timer = setInterval(async () => {
    if (c.stopped) return;
    const blob = await grab(video, canvas);
    if (c.recording) {
      c.frames.push(blob);
      $("budget").style.width = Math.max(0, 100 - c.frames.length / maxFrames * 100) + "%";
      if (c.frames.length >= maxFrames) { c.error = "tempo de gravação esgotado"; notify(); }
    }
    if (c.inflight) return;
    c.inflight = true;
    try {
      const form = new FormData(); form.append("frame", blob, "frame.jpg");
      const r = await fetch("/dev/liveness/observe", {method: "POST", headers: {"X-API-Key": key}, body: form});
      const o = await r.json();
      if (r.ok && o.available) { c.failures = 0; c.latest = o; c.seq++; showDebug(o); showFace(o); notify(); }
      else if (++c.failures > 10) { c.error = "o worker não está medindo os quadros (" + r.status + ")"; notify(); }
    } catch (e) { if (++c.failures > 10) { c.error = String(e); notify(); } }
    c.inflight = false;
  }, INTERVAL);
  // Espera `need` medidas SEGUIDAS cumprindo `pred`; `hint(o)` orienta enquanto não cumpre.
  c.until = (pred, timeout, hint, need = 1, timeoutMessage = "passo não detectado a tempo") =>
    new Promise((resolve, reject) => {
      let streak = 0, lastSeq = c.seq;
      const t0 = performance.now();
      const check = () => {
        if (c.error) { done(); return reject(new Abort(c.error)); }
        if (performance.now() - t0 > timeout) { done(); return reject(new Abort(timeoutMessage)); }
        if (c.seq === lastSeq || !c.latest) return;
        lastSeq = c.seq;
        const o = c.latest;
        if (pred(o)) { if (++streak >= need) { done(); resolve(o); } }
        else { streak = 0; $("hint").textContent = hint ? hint(o) : ""; }
      };
      const tick = setInterval(check, 50);
      const done = () => { clearInterval(tick); c.waiters = c.waiters.filter(w => w !== check); };
      c.waiters.push(check);
    });
  c.stop = () => { c.stopped = true; clearInterval(c.timer); };
  return c;
}

const tracked = o => o.face_count === 1 && o.yaw !== null && o.eye_distance !== null && o.box;
const eyesRel = o => o.eye_distance / o.image_width;
let BASE = null;
function showDebug(o) {
  const scale = (BASE && tracked(o)) ? (o.eye_distance / BASE).toFixed(2) : "–";
  $("debug").textContent = `rostos ${o.face_count} · giro ${tracked(o) ? o.yaw.toFixed(2) : "–"}`
    + ` (esq ≥ +${P.turn_min_yaw}, dir ≤ -${P.turn_min_yaw}) · escala ${scale} (aprox ≥ ${P.closer_min_scale})`
    + ` · olhos/largura ${tracked(o) ? eyesRel(o).toFixed(3) : "–"} (início ${START_MIN}–${START_MAX})`;
}

function faceHint(o) {
  if (o.face_count === 0) return "Não vejo seu rosto";
  if (o.face_count > 1) return "Fique sozinho na imagem";
  return null;
}

function positionIssue(o) {
  if (!tracked(o)) return faceHint(o) || "Não vejo seu rosto";
  const [x, y, w, h] = o.box;
  if (Math.abs(x + w / 2 - 0.5) > CENTER_X_TOL || Math.abs(y + h / 2 - 0.5) > CENTER_Y_TOL)
    return "Centralize o rosto no oval";
  if (eyesRel(o) > START_MAX) return "Afaste-se um pouco";
  if (eyesRel(o) < START_MIN) return "Aproxime-se um pouco";
  if (Math.abs(o.yaw) > P.frontal_max_yaw * 0.8) return "Olhe de frente para a câmera";
  return null;
}

function satisfies(o, step) {
  if (!tracked(o)) return false;
  const ratio = o.eye_distance / BASE;
  if (step === "MOVE_CLOSER") return ratio >= P.closer_min_scale && Math.abs(o.yaw) <= P.turn_min_yaw;
  if (ratio < P.turn_min_eye_ratio) return false;
  return step === "TURN_LEFT" ? o.yaw >= P.turn_min_yaw : o.yaw <= -P.turn_min_yaw;
}

function progress(o, step) {
  if (!tracked(o) || !BASE) return 0;
  if (step === "MOVE_CLOSER") return (o.eye_distance / BASE - 1) / (P.closer_min_scale - 1);
  return (step === "TURN_LEFT" ? o.yaw : -o.yaw) / P.turn_min_yaw;
}

function renderSteps(steps, current) {
  $("steps").innerHTML = steps.map((s, i) =>
    `<span class="${i < current ? "ok" : i === current ? "now" : ""}">${i + 1}. ${LABEL[s]}</span>`).join("");
}

$("go").onclick = async () => {
  $("out").textContent = ""; $("hint").textContent = ""; $("go").disabled = true; BASE = null;
  $("fill").style.width = "0%"; $("budget").style.width = "100%";
  const key = $("key").value.trim(), subject = $("subject").value.trim(), op = $("op").value;
  const headers = {"X-API-Key": key};
  let stream = null, cap = null;
  try {
    stream = await openCamera();
    listCameras();   // com a permissão concedida, os nomes das câmeras aparecem
    const video = $("v"); video.srcObject = stream; await video.play();
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640; canvas.height = video.videoHeight || 480;
    setupOverlay(canvas.width, canvas.height);

    const purpose = op === "verify" ? "VERIFICATION" : "REGISTRATION";
    const r = await fetch("/api/v1/liveness-sessions", {method: "POST",
      headers: {...headers, "Content-Type": "application/json"},
      body: JSON.stringify({subject_id: subject, purpose})});
    const session = await r.json();
    if (!r.ok) throw new Error(JSON.stringify(session));
    const steps = session.challenge;
    log("desafio: " + steps.join(" → "));
    renderSteps(steps, -1);

    cap = makeCapture(key, video, canvas, session.frames.max);

    // 1. Posição inicial: rosto no oval, de frente, numa distância que permita aproximar.
    $("step").textContent = "Encaixe o rosto no oval, olhando de frente";
    const recolor = setInterval(() => {
      if (cap.latest && BASE === null) drawOval(1, positionIssue(cap.latest) ? COLOR.bad : COLOR.good);
    }, 100);
    let start;
    try {
      start = await cap.until(o => positionIssue(o) === null, POSITION_TIMEOUT, positionIssue, 3,
        "não foi possível encaixar o rosto no oval");
    } finally { clearInterval(recolor); }
    BASE = start.eye_distance;
    const selfie = await grab(video, canvas);
    cap.recording = true; $("hint").textContent = "";

    // 2. Passos na ordem sorteada; volta ao centro entre eles (não depois do último).
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i], last = i === steps.length - 1;
      renderSteps(steps, i);
      $("step").textContent = session.instructions[i].replace(" e volte ao centro", "").replace(" e volte", "");
      drawOval(step === "MOVE_CLOSER" ? P.closer_min_scale : 1, COLOR.idle);
      arrow(step);
      $("fill").style.width = "0%";
      const watch = setInterval(() => {
        if (cap.latest) $("fill").style.width = Math.max(0, Math.min(100, progress(cap.latest, step) * 100)) + "%";
      }, 100);
      try {
        await cap.until(o => satisfies(o, step), STEP_TIMEOUT,
          o => faceHint(o) || (step === "MOVE_CLOSER" ? "Aproxime até o rosto preencher o oval"
            : tracked(o) && o.eye_distance / BASE < P.turn_min_eye_ratio ? "Vire só a cabeça, devagar"
            : "Devagar… continue virando"));
      } finally { clearInterval(watch); }
      $("fill").style.width = "100%"; arrow(null);
      drawOval(G.scale, COLOR.good);
      renderSteps(steps, i + 1);
      if (last) break;
      $("step").textContent = "Isso! Volte ao centro";
      drawOval(1, COLOR.idle);
      await cap.until(o => tracked(o) && Math.abs(o.yaw) <= P.frontal_max_yaw
          && o.eye_distance / BASE < 1 + (P.closer_min_scale - 1) / 2,
        CENTER_TIMEOUT, o => faceHint(o) || "Volte para o oval, olhando de frente",
        1, "não voltou ao centro a tempo");
      $("hint").textContent = "";
    }
    cap.stop(); stream.getTracks().forEach(t => t.stop());
    $("step").textContent = "Enviando…"; log(cap.frames.length + " quadros capturados");

    const form = new FormData();
    form.append("image", selfie, "selfie.jpg");
    form.append("liveness_session_id", session.session_id);
    cap.frames.forEach((f, i) => form.append("frames", f, `frame-${String(i).padStart(3, "0")}.jpg`));
    const url = op === "verify" ? `/api/v1/subjects/${subject}/verifications`
                                : `/api/v1/subjects/${subject}/face`;
    const sendHeaders = op === "verify" ? {...headers, "Idempotency-Key": crypto.randomUUID()} : headers;
    const sent = await fetch(url, {method: op === "replace" ? "PUT" : "POST", headers: sendHeaders, body: form});
    const accepted = await sent.json();
    if (!sent.ok) throw new Error(JSON.stringify(accepted));
    const id = accepted.verification_id || accepted.registration_id;
    const result = op === "verify" ? `/api/v1/verifications/${id}` : `/api/v1/face-registrations/${id}`;
    $("step").textContent = "Processando…";
    for (let i = 0; i < 40; i++) {
      await sleep(1000);
      const body = await (await fetch(result, {headers})).json();
      if (body.decision) {
        $("step").textContent = body.decision + (body.reason ? " — " + body.reason : "");
        log(JSON.stringify(body, null, 2)); break;
      }
    }
  } catch (e) {
    const camera = cameraMessage(e);
    $("step").textContent = e instanceof Abort ? "Não concluído: " + e.message : camera ? "Câmera indisponível" : "Erro";
    if (e instanceof Abort) $("hint").textContent = "Nada foi enviado. Clique em Iniciar para tentar de novo.";
    else if (camera) $("hint").textContent = camera;
    log(String(e));
  } finally {
    if (cap) cap.stop();
    if (stream) stream.getTracks().forEach(t => t.stop());
    arrow(null); hideFace();
    $("go").disabled = false;
  }
};
</script></body></html>
"""
