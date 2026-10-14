(function () {
  const KEY = "vite-ui-theme";
  const MODE_KEY = "fiscalis-theme-mode";
  const osTheme = () => (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

  function mode() {
    const m = localStorage.getItem(MODE_KEY);
    if (m) return m;
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  }

  function apply(m) {
    localStorage.setItem(MODE_KEY, m);
    localStorage.setItem(KEY, m === "system" ? osTheme() : m);
  }

  const icon = document.querySelector('link[rel="icon"]');
  if (icon) icon.href = "/public/favicon.png?v=4";

  if (localStorage.getItem(KEY) === "system" || !localStorage.getItem(KEY)) {
    apply(mode());
    location.reload();
    return;
  }

  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (mode() === "system") { apply("system"); location.reload(); }
  });

  function choose(m) {
    if (m === mode()) return;
    apply(m);
    location.reload();          // Chainlit applies the theme and the blue palette on load
  }

  const ICON = {
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>',
    system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
  };
  const LABEL = { light: "Светлая", dark: "Тёмная", system: "Авто" };

  function render() {
    document.querySelectorAll(".kz-seg button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.mode === mode())));
  }

  function mount() {
    const sidebar = document.querySelector('[data-sidebar="sidebar"]');
    if (!sidebar || sidebar.querySelector(".kz-settings")) return;
    const box = document.createElement("div");
    box.className = "kz-settings";
    box.innerHTML = '<div class="kz-settings__label">Тема</div><div class="kz-seg" role="group" aria-label="Тема">' +
      ["light", "dark", "system"].map((m) =>
        `<button type="button" data-mode="${m}" title="${m === "system" ? "Как в системе" : LABEL[m]}">${ICON[m]}<span>${LABEL[m]}</span></button>`
      ).join("") + "</div>";
    box.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-mode]");
      if (b) choose(b.dataset.mode);
    });
    sidebar.appendChild(box);
    render();
  }

  new MutationObserver(mount).observe(document.body, { childList: true, subtree: true });
})();
