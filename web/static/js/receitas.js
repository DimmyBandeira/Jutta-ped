// Formulário de receitas: filtra os atributos pelo especialista escolhido e sugere valores.
// Sem JavaScript o formulário continua funcionando (todos os atributos ficam listados).
(function () {
  document.querySelectorAll("[data-cond]").forEach((linha) => {
    const esp = linha.querySelector("[data-cond-esp]");
    const attr = linha.querySelector("[data-cond-attr]");
    const igual = linha.querySelector("[data-cond-igual]");
    if (!esp || !attr) return;

    function filtrar(manter) {
      const alvo = esp.value;
      attr.querySelectorAll("optgroup").forEach((g) => {
        const visivel = !alvo || g.dataset.esp === alvo;
        g.hidden = !visivel;
        g.disabled = !visivel;
      });
      const sel = attr.selectedOptions[0];
      if (!manter || (sel && sel.parentElement.tagName === "OPTGROUP" && sel.parentElement.disabled)) attr.value = "";
      sugerir();
    }
    function sugerir() {
      const sel = attr.selectedOptions[0];
      igual.setAttribute("list", sel && sel.dataset.lista ? sel.dataset.lista : "dl-padrao");
    }
    esp.addEventListener("change", () => filtrar(false));
    attr.addEventListener("change", () => {
      const sel = attr.selectedOptions[0];
      if (sel && sel.dataset.esp && esp.value !== sel.dataset.esp) { esp.value = sel.dataset.esp; filtrar(true); }
      sugerir();
    });
    filtrar(true);
  });
})();
