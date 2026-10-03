// Som de alerta para popups novos. Navegadores bloqueiam audio antes de um gesto do usuario:
// o AudioContext e "destravado" no primeiro clique/tecla da sessao.
(function () {
  let ctx = null;
  function unlock() {
    try { ctx = ctx || new (window.AudioContext || window.webkitAudioContext)(); ctx.resume(); } catch (e) {}
    document.removeEventListener("click", unlock);
    document.removeEventListener("keydown", unlock);
  }
  document.addEventListener("click", unlock);
  document.addEventListener("keydown", unlock);

  function beep() {
    if (!ctx || ctx.state !== "running") return;
    [0, 0.25].forEach((delay) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.frequency.value = 880; gain.gain.value = 0.15;
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(ctx.currentTime + delay); osc.stop(ctx.currentTime + delay + 0.15);
    });
  }

  const stack = document.getElementById("popup-stack");
  if (!stack) return;
  // Tabela "Alertas recentes" (so existe no Monitoramento): atualiza quando um popup entra ou sai.
  let pendente = null;
  function atualizarAlertas() {
    clearTimeout(pendente);
    pendente = setTimeout(() => {
      const lista = document.getElementById("lista-eventos-recentes");
      if (lista && window.htmx) htmx.trigger(lista, "alertas-mudaram");
    }, 250);
  }
  new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && n.classList.contains("popup")) { if (!n.dataset.silencioso) beep(); atualizarAlertas(); }
      }
      for (const n of m.removedNodes) {
        if (n.nodeType === 1 && n.classList && n.classList.contains("popup")) atualizarAlertas();
      }
    }
  }).observe(stack, { childList: true });
  // popups renderizados junto da pagina (pendentes) nao devem apitar de novo
  stack.querySelectorAll(".popup").forEach((n) => (n.dataset.silencioso = "1"));
})();

// ---- SSE: duplicatas e ciencia dada em outro navegador ------------------------------------------
// (1) Um popup pode chegar duas vezes: renderizado na pagina (pendentes) e de novo pelo SSE
//     (replay/corrida na conexao). Descarta o repetido antes de o htmx inseri-lo.
// (2) `event: ack` = outro operador deu ciencia: remove o popup daqui tambem.
document.body.addEventListener("htmx:sseBeforeMessage", (e) => {
  const msg = e.detail;
  if (!msg || msg.type !== "popup") return;
  const m = /id="popup-([^"]+)"/.exec(msg.data || "");
  if (m && document.getElementById(`popup-${m[1]}`)) e.preventDefault();
});
document.body.addEventListener("htmx:sseMessage", (e) => {
  const msg = e.detail;
  if (!msg || msg.type !== "ack") return;
  const el = document.getElementById(`popup-${(msg.data || "").trim()}`);
  if (el) el.remove();
});
