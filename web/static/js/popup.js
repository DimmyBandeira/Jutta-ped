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
  // "Fechar todos" aparece quando ha 2+ popups e fica logo acima da pilha
  function atualizarFecharTodos() {
    const botao = document.getElementById("popup-fechar-todos");
    if (!botao) return;
    const n = stack.querySelectorAll(".popup").length;
    botao.hidden = n < 2;
  }
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
    atualizarFecharTodos();
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


// ---- Dispensar popup (botao X) -----------------------------------------------------------------
// Dispensar NAO da ciencia: so esconde o popup neste navegador (ate fechar a aba). O alerta continua
// pendente em "Alertas recentes", de onde o operador pode dar ciencia depois.
const CHAVE_DISPENSADOS = "jutta_popups_dispensados";
function lerDispensados() {
  try { return JSON.parse(sessionStorage.getItem(CHAVE_DISPENSADOS) || "[]"); } catch (e) { return []; }
}
function gravarDispensado(id) {
  try {
    const lista = lerDispensados();
    if (!lista.includes(id)) { lista.push(id); sessionStorage.setItem(CHAVE_DISPENSADOS, JSON.stringify(lista.slice(-200))); }
  } catch (e) { /* sessionStorage indisponivel: o popup apenas volta ao recarregar */ }
}
function dispensarPopup(botao) {
  const popup = botao.closest(".popup");
  if (!popup) return;
  gravarDispensado(popup.dataset.eventId);
  popup.remove();
}
function dispensarTodos() {
  document.querySelectorAll("#popup-stack .popup").forEach((p) => { gravarDispensado(p.dataset.eventId); p.remove(); });
}
window.dispensarPopup = dispensarPopup;
window.dispensarTodos = dispensarTodos;

// Pendentes renderizados junto com a pagina que o operador ja dispensou nesta aba: escondem de novo
document.querySelectorAll("#popup-stack .popup").forEach((p) => {
  if (lerDispensados().includes(p.dataset.eventId)) p.remove();
});
// ...e o mesmo para os que chegam pelo SSE (replay apos reconexao)
document.body.addEventListener("htmx:sseBeforeMessage", (e) => {
  const msg = e.detail;
  if (!msg || msg.type !== "popup") return;
  const m = /id="popup-([^"]+)"/.exec(msg.data || "");
  if (m && lerDispensados().includes(m[1])) e.preventDefault();
});
