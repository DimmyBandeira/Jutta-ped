// Menu lateral: em telas estreitas vira barra superior com botão de menu (aria-expanded/aria-controls).
(function () {
  const botao = document.getElementById("menu-botao");
  const menu = document.getElementById("menu-lateral");
  if (!botao || !menu) return;

  function definir(aberto, devolverFoco) {
    menu.classList.toggle("aberto", aberto);
    botao.setAttribute("aria-expanded", aberto ? "true" : "false");
    botao.setAttribute("aria-label", aberto ? "Fechar menu" : "Abrir menu");
    if (!aberto && devolverFoco) botao.focus();
  }

  botao.addEventListener("click", () => definir(!menu.classList.contains("aberto")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.classList.contains("aberto")) definir(false, true);
  });
  // Ao voltar para a largura de desktop o menu fica sempre visível (CSS); zera o estado do botão.
  const larga = window.matchMedia("(min-width: 900px)");
  const aoMudar = () => { if (larga.matches) definir(false, false); };
  if (larga.addEventListener) larga.addEventListener("change", aoMudar); else larga.addListener(aoMudar);
})();
