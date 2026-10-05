// Diagnostico do mestre: atualiza o ultimo quadro (JPEG gerado na hora, so em memoria) enquanto a aba esta visivel.
// So pede a imagem quando o resumo (fragmento htmx) informa que ja existe um quadro: antes disso nao ha o que mostrar (evita 404).
(function () {
  const img = document.getElementById("diag-quadro");
  if (!img) return;
  const base = "/painel/cameras/" + location.pathname.split("/")[3] + "/diagnostico/quadro.jpg";
  const aviso = document.getElementById("diag-sem-quadro");
  let ocupado = false;
  function temQuadro() {
    const m = document.getElementById("diag-tem-quadro");
    return !!(m && m.dataset.q);
  }
  function atualizar() {
    if (document.hidden || ocupado || !temQuadro()) return;
    ocupado = true;
    const novo = new Image();
    novo.onload = () => { img.src = novo.src; img.hidden = false; if (aviso) aviso.hidden = true; ocupado = false; };
    novo.onerror = () => { ocupado = false; };            // quadro ja descartado: mantem o ultimo
    novo.src = base + "?t=" + Date.now();
  }
  setInterval(atualizar, 1000);
})();
