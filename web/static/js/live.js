// Monitoramento: play/pause do player go2rtc (iframe) + overlay de bboxes (canvas).
// Overlay: polling de /painel/cameras/{id}/overlay (contrato plugin/schemas/overlay.schema.json,
// acrescido de frame_w/frame_h). Sem WebSocket nesta fase.
const _tocando = new Map();   // cameraId -> timer do overlay
const _COR = { child: "#f87171", adult: "#22d3ee", uncertain: "#facc15" };

function livePlay(id) {
  const card = document.getElementById(`cam-${id}`);
  const iframe = document.getElementById(`player-${id}`);
  const btn = document.getElementById(`btn-play-${id}`);
  if (!card || !iframe) return;
  const url = card.dataset.playerUrl;
  if (!url) { alert("Esta câmera ainda não tem stream no go2rtc."); return; }
  if (_tocando.has(id)) {
    clearInterval(_tocando.get(id)); _tocando.delete(id);
    iframe.src = "about:blank"; btn.textContent = "Play"; limpar(id);
  } else {
    iframe.src = url; btn.textContent = "Pause";
    _tocando.set(id, setInterval(() => atualizarOverlay(id), 500));
  }
}

async function atualizarOverlay(id) {
  try {
    const r = await fetch(`/painel/cameras/${id}/overlay`, { headers: { Accept: "application/json" } });
    if (!r.ok) return limpar(id);
    desenhar(id, await r.json());
  } catch (e) { limpar(id); }
}

function limpar(id) {
  const c = document.getElementById(`overlay-${id}`);
  if (c) c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

function desenhar(id, msg) {
  const c = document.getElementById(`overlay-${id}`);
  if (!c) return;
  if (c.width !== c.offsetWidth || c.height !== c.offsetHeight) { c.width = c.offsetWidth; c.height = c.offsetHeight; }
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!msg.objects || !msg.objects.length || !msg.frame_w || !msg.frame_h) return;
  if (msg.frame_age_ms != null && msg.frame_age_ms > 2000) return;   // overlay velho: nao desenhar
  const sx = c.width / msg.frame_w, sy = c.height / msg.frame_h;
  for (const o of msg.objects) {
    const [x1, y1, x2, y2] = o.bbox_xyxy, cor = _COR[o.label] || "#22d3ee";
    ctx.strokeStyle = cor; ctx.lineWidth = 2;
    ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
    const txt = `${o.label} ${Math.round(o.confidence * 100)}%`;
    ctx.font = "bold 11px monospace";
    ctx.fillStyle = cor; ctx.fillRect(x1 * sx, y1 * sy - 16, ctx.measureText(txt).width + 8, 16);
    ctx.fillStyle = "#000"; ctx.fillText(txt, x1 * sx + 4, y1 * sy - 4);
  }
}
window.livePlay = livePlay;
