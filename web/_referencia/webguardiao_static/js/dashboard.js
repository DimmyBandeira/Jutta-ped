// WebGuardião Core — JS vanilla

/* ================================================================
   Player Play / Pause
   O player go2rtc é uma página stream.html carregada num <iframe>.
   Play  → define iframe.src = data-player-url
   Pause → limpa iframe.src
================================================================ */
const playing = new Set();

function togglePlay(cameraId) {
  const card   = document.getElementById(`camera-card-${cameraId}`);
  const iframe = document.getElementById(`player-${cameraId}`);
  const btn    = document.getElementById(`btn-play-${cameraId}`);
  if (!card || !iframe) return;

  const url = card.dataset.playerUrl;
  if (!url) {
    alert("Esta câmera não tem URL go2rtc configurada.");
    return;
  }

  if (playing.has(cameraId)) {
    iframe.src = "about:blank";
    playing.delete(cameraId);
    if (btn) btn.textContent = "Play";
    detachWs(cameraId);
  } else {
    iframe.src = url;
    playing.add(cameraId);
    if (btn) btn.textContent = "Pause";
    attachWs(cameraId);
  }
}

/* ================================================================
   Detecções de IA — WebSocket por câmera
   Conecta em /ws/cameras/{id}/detections quando o play é ativado.
   O servidor envia JSON:
     { detections: [{cls, conf, bbox:[x1,y1,x2,y2]}], frame_w, frame_h }
================================================================ */
const _ws = {};   // {cameraId: WebSocket}

function attachWs(cameraId) {
  if (_ws[cameraId]) return;

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/cameras/${cameraId}/detections`);

  ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    drawDetections(cameraId, msg.detections, msg.frame_w, msg.frame_h);
  };

  ws.onclose = () => {
    delete _ws[cameraId];
    clearCanvas(cameraId);
    // Reconecta automaticamente se o play ainda estiver ativo
    if (playing.has(cameraId)) {
      setTimeout(() => attachWs(cameraId), 3000);
    }
  };

  ws.onerror = () => ws.close();
  _ws[cameraId] = ws;
}

function detachWs(cameraId) {
  const ws = _ws[cameraId];
  if (ws) { ws.close(); delete _ws[cameraId]; }
  clearCanvas(cameraId);
}

/* ================================================================
   Desenho de bounding boxes no canvas overlay
================================================================ */
function clearCanvas(cameraId) {
  const canvas = document.getElementById(`overlay-${cameraId}`);
  if (!canvas) return;
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

function drawDetections(cameraId, detections, frameW, frameH) {
  const canvas = document.getElementById(`overlay-${cameraId}`);
  if (!canvas) return;

  // Sincroniza o tamanho lógico do canvas ao tamanho físico (pixel perfeito)
  if (canvas.width !== canvas.offsetWidth || canvas.height !== canvas.offsetHeight) {
    canvas.width  = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;
  }

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!detections || !detections.length) return;

  const sx = canvas.width  / frameW;
  const sy = canvas.height / frameH;

  for (const det of detections) {
    const [x1, y1, x2, y2] = det.bbox;
    const rx = x1 * sx, ry = y1 * sy;
    const rw = (x2 - x1) * sx, rh = (y2 - y1) * sy;

    // Caixa
    ctx.strokeStyle = "#22d3ee";
    ctx.lineWidth   = 2;
    ctx.strokeRect(rx, ry, rw, rh);

    // Fundo da label
    const label = `${det.cls} ${Math.round(det.conf * 100)}%`;
    ctx.font = "bold 11px monospace";
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = "rgba(8,145,178,0.85)";
    ctx.fillRect(rx, ry - 18, tw + 8, 18);

    // Texto da label
    ctx.fillStyle = "#ffffff";
    ctx.fillText(label, rx + 4, ry - 4);
  }
}

// Compatibilidade: mantém window.drawBoundingBox para uso futuro por código externo
window.drawBoundingBox = (cameraId, bbox) => {
  drawDetections(cameraId, [{
    cls: bbox.label || "?",
    conf: 1,
    bbox: [
      bbox.x * 1000, bbox.y * 1000,
      (bbox.x + bbox.w) * 1000, (bbox.y + bbox.h) * 1000,
    ],
  }], 1000, 1000);
};

/* ================================================================
   Modal genérico (formulários HTMX)
================================================================ */
function openModal() {
  const m = document.getElementById("modal");
  if (m) m.classList.add("open");
}

function closeModal() {
  const m = document.getElementById("modal");
  if (m) { m.classList.remove("open"); m.innerHTML = ""; }
}

// Fecha ao clicar no fundo escuro
document.addEventListener("click", (e) => {
  const m = document.getElementById("modal");
  if (m && e.target === m) closeModal();
});

// Fecha apenas em POSTs HTMX bem-sucedidos (não fecha em GET de carregamento de form)
document.body.addEventListener("htmx:afterRequest", (e) => {
  const verb = (e.detail.requestConfig?.verb || "get").toLowerCase();
  if (verb === "get") return;

  const xhr = e.detail.xhr;
  if (xhr && xhr.status >= 200 && xhr.status < 300) {
    const path = e.detail.requestConfig?.path || "";
    const isPreview = path.includes("preview") || path.includes("test-stream") || path.includes("toggle");
    if (!isPreview) closeModal();
  }
});

/* ================================================================
   Exports globais
================================================================ */
window.togglePlay  = togglePlay;
window.openModal   = openModal;
window.closeModal  = closeModal;
window.drawDetections = drawDetections;
