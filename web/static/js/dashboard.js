// Dashboard de monitoramento: gráfico "Alertas por período" (SVG desenhado aqui, sem bibliotecas)
// e pequenos ajustes de requisições HTMX. Não abre conexões SSE: o painel usa a de base.html.
(function () {
  const NS = "http://www.w3.org/2000/svg";

  // ---- Parâmetros vazios não são enviados (ex.: camera= sem valor) -----------------------------
  document.body.addEventListener("htmx:configRequest", (e) => {
    const p = e.detail && e.detail.parameters;
    const path = e.detail && e.detail.path;
    if (!p || !path || !/^\/painel\/(eventos\/recentes|sombra\/feed)/.test(path)) return;
    ["camera", "pendentes"].forEach((k) => { if (k in p && (p[k] === "" || p[k] == null)) delete p[k]; });
  });

  // ---- Sombra: só consulta enquanto a área está aberta -----------------------------------------
  window.sombraAberta = function () {
    const d = document.getElementById("sombra-area");
    return !!(d && d.open);
  };

  // ---- Gráfico -----------------------------------------------------------------------------------
  const elPeriodo = document.getElementById("grafico-periodo");
  const elCamera = document.getElementById("grafico-camera");
  const alvo = document.getElementById("grafico-alertas");
  const msg = document.getElementById("grafico-msg");
  const total = document.getElementById("grafico-total");
  const tabela = document.getElementById("grafico-tabela");
  if (!elPeriodo || !alvo) return;

  let controlador = null;
  let temporizador = null;

  function criar(nome, attrs, texto) {
    const n = document.createElementNS(NS, nome);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (texto != null) n.textContent = texto;
    return n;
  }

  function mensagem(texto, erro) {
    msg.textContent = texto || "";
    msg.classList.toggle("erro", !!erro);
  }

  function limparTabela() { while (tabela && tabela.firstChild) tabela.removeChild(tabela.firstChild); }

  function preencherTabela(pontos) {
    limparTabela();
    if (!tabela) return;
    pontos.forEach((p) => {
      const tr = document.createElement("tr");
      const a = document.createElement("td"); a.textContent = p.rotulo;
      const b = document.createElement("td"); b.textContent = String(p.total);
      tr.appendChild(a); tr.appendChild(b); tabela.appendChild(tr);
    });
  }

  function proximoTeto(max) {
    if (max <= 4) return 4;
    const base = Math.pow(10, Math.floor(Math.log10(max)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * base >= max) return m * base;
    return max;
  }

  function desenhar(dados) {
    const pontos = Array.isArray(dados.pontos) ? dados.pontos : [];
    const W = 640, H = 260, ml = 38, mr = 8, mt = 10, mb = 34;
    const iw = W - ml - mr, ih = H - mt - mb;
    const vazio = !!dados.vazio || !dados.total;
    const max = pontos.reduce((m, p) => Math.max(m, p.total), 0);
    const teto = vazio ? 4 : proximoTeto(max);
    const rotuloUnidade = dados.unidade === "dia" ? "dia" : "hora";
    const resumo = vazio
      ? `Sem alertas no período selecionado (${pontos.length} intervalos por ${rotuloUnidade}).`
      : `${dados.total} alerta(s) no período, em ${pontos.length} intervalos por ${rotuloUnidade}. Pico: ${max}.`;

    const svg = criar("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Gráfico de barras. ${resumo}`, preserveAspectRatio: "xMidYMid meet" });
    // grade e eixo Y
    for (let i = 0; i <= 4; i++) {
      const v = (teto / 4) * i;
      const y = mt + ih - (v / teto) * ih;
      svg.appendChild(criar("line", { x1: ml, x2: W - mr, y1: y, y2: y, class: i === 0 ? "eixo" : "grade" }));
      svg.appendChild(criar("text", { x: ml - 6, y: y + 4, "text-anchor": "end", class: "texto-eixo" }, String(Number.isInteger(v) ? v : v.toFixed(1))));
    }
    const n = Math.max(pontos.length, 1);
    const passo = iw / n;
    const largura = Math.max(Math.min(passo * 0.7, 28), 2);
    const cada = Math.max(1, Math.ceil(n / 8));   // rótulos do eixo X sem sobreposição
    pontos.forEach((p, i) => {
      const x = ml + passo * i + (passo - largura) / 2;
      const h = vazio ? 0 : (p.total / teto) * ih;
      if (p.total > 0) {
        const r = criar("rect", { x, y: mt + ih - h, width: largura, height: Math.max(h, 1), rx: 2, class: "barra" });
        r.appendChild(criar("title", {}, `${p.rotulo}: ${p.total} alerta(s)`));
        svg.appendChild(r);
      }
      if (i % cada === 0) svg.appendChild(criar("text", { x: ml + passo * i + passo / 2, y: H - 12, "text-anchor": "middle", class: "texto-eixo" }, p.rotulo));
    });
    alvo.replaceChildren(svg);
    preencherTabela(pontos);
    if (total) total.textContent = vazio ? "· 0 alertas" : `· ${dados.total} alerta${dados.total === 1 ? "" : "s"}`;
    mensagem(vazio ? "Sem alertas no período" : "");
  }

  function indisponivel(texto) {
    alvo.replaceChildren();
    limparTabela();
    if (total) total.textContent = "";
    mensagem(texto, true);
  }

  async function carregar() {
    if (controlador) controlador.abort();
    controlador = new AbortController();
    const q = new URLSearchParams({ periodo: elPeriodo.value });
    if (elCamera && elCamera.value) q.set("camera", elCamera.value);
    mensagem("Carregando histórico…");
    try {
      const r = await fetch(`/painel/alertas/historico?${q}`, { headers: { Accept: "application/json" }, credentials: "same-origin", signal: controlador.signal });
      if (!r.ok) {
        let detalhe = "";
        try { const j = await r.json(); detalhe = typeof j.detail === "string" ? j.detail : ""; } catch (e) { /* sem corpo JSON */ }
        return indisponivel(`Histórico indisponível (HTTP ${r.status}).${detalhe ? " " + detalhe : ""}`);
      }
      desenhar(await r.json());
    } catch (e) {
      if (e && e.name === "AbortError") return;
      indisponivel("Histórico indisponível: não foi possível consultar o servidor.");
    }
  }

  elPeriodo.addEventListener("change", carregar);
  if (elCamera) elCamera.addEventListener("change", carregar);
  // Atualiza quando chega/muda um alerta (evento disparado por popup.js) e a cada minuto.
  document.body.addEventListener("alertas-mudaram", () => { clearTimeout(temporizador); temporizador = setTimeout(carregar, 1500); });
  setInterval(() => { if (!document.hidden) carregar(); }, 60000);
  carregar();
})();
