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
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType === 1 && n.classList.contains("popup") && !n.dataset.silencioso) beep();
    }
  }).observe(stack, { childList: true });
  // popups renderizados junto da pagina (pendentes) nao devem apitar de novo
  stack.querySelectorAll(".popup").forEach((n) => (n.dataset.silencioso = "1"));
})();
