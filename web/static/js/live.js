// Monitoramento: play/pause do player go2rtc (iframe) + overlay de bboxes (canvas).
// Overlay: polling de /painel/cameras/{id}/overlay (contrato plugin/schemas/overlay.schema.json,
// com frame_w/frame_h). So as cameras em "Play" consultam; sem WebSocket nesta fase.
const _tocando = new Map();          // cameraId -> timer do overlay
const _INTERVALO_OVERLAY_MS = 200;
const _MAX_IDADE_OVERLAY_MS = 2000;  // overlay mais velho que isso nao e desenhado
const _COR = { child: "#f87171", adult: "#22d3ee", uncertain: "#facc15" };
const _ROTULO = { child: "criança", adult: "adulto", uncertain: "incerto" };

function _els(id) {
  return {
    card: document.getElementById(`cam-${id}`),
    iframe: document.getElementById(`player-${id}`),
    btn: document.getElementById(`btn-play-${id}`),
  };
}

function livePlay(id, ligar) {
  const { card, iframe, btn } = _els(id);
  if (!card || !iframe) return;
  const tocando = _tocando.has(id);
  if (ligar === undefined) ligar = !tocando;
  if (ligar === tocando) return;
  if (ligar) {
    const url = card.dataset.playerUrl;
    if (!url) return;                // stream ainda nao disponivel
    iframe.src = url;
    if (btn) btn.textContent = "Pause";
    _tocando.set(id, setInterval(() => atualizarOverlay(id), _INTERVALO_OVERLAY_MS));
  } else {
    clearInterval(_tocando.get(id));
    _tocando.delete(id);
    iframe.src = "about:blank";
    if (btn) btn.textContent = "Play";
    limpar(id);
  }
}

function liveTodas(ligar) {
  document.querySelectorAll(".cam-card").forEach((card) => livePlay(Number(card.dataset.cameraId), ligar));
}

async function atualizarOverlay(id) {
  try {
    const r = await fetch(`/painel/cameras/${id}/overlay`, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (!r.ok) return limpar(id);
    const msg = await r.json();
    if (!_tocando.has(id)) return;      // pausou enquanto a resposta vinha: nao redesenha sobre o player parado
    desenhar(id, msg);
  } catch (e) {
    limpar(id);
  }
}

function limpar(id) {
  const c = document.getElementById(`overlay-${id}`);
  if (c) c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

// O video do go2rtc e exibido "contain" (com barras); mapeia coordenadas do frame para a area real do video.
function areaDoVideo(cw, ch, fw, fh) {
  const escala = Math.min(cw / fw, ch / fh);
  return { escala, dx: (cw - fw * escala) / 2, dy: (ch - fh * escala) / 2 };
}

function desenhar(id, msg) {
  const c = document.getElementById(`overlay-${id}`);
  if (!c) return;
  if (c.width !== c.offsetWidth || c.height !== c.offsetHeight) { c.width = c.offsetWidth; c.height = c.offsetHeight; }
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!msg.objects || !msg.objects.length || !msg.frame_w || !msg.frame_h) return;
  if (msg.frame_age_ms != null && msg.frame_age_ms > _MAX_IDADE_OVERLAY_MS) return;
  const { escala, dx, dy } = areaDoVideo(c.width, c.height, msg.frame_w, msg.frame_h);
  ctx.font = "bold 11px monospace";
  for (const o of msg.objects) {
    const [x1, y1, x2, y2] = o.bbox_xyxy;
    const cor = _COR[o.label] || "#22d3ee";
    const rx = dx + x1 * escala, ry = dy + y1 * escala, rw = (x2 - x1) * escala, rh = (y2 - y1) * escala;
    ctx.strokeStyle = cor; ctx.lineWidth = 2;
    ctx.strokeRect(rx, ry, rw, rh);
    const txt = `${_ROTULO[o.label] || o.label} ${Math.round(o.confidence * 100)}%`;
    const topo = Math.max(ry - 16, 0);
    ctx.fillStyle = cor; ctx.fillRect(rx, topo, ctx.measureText(txt).width + 8, 16);
    ctx.fillStyle = "#000"; ctx.fillText(txt, rx + 4, topo + 12);
  }
}

window.livePlay = livePlay;
window.liveTodas = liveTodas;
