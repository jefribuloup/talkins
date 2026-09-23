(function () {
  const STORAGE_KEY = "meja-data-theme";
  const root = document.documentElement;
  const icon = document.getElementById("themeIcon");
  const btn = document.getElementById("themeToggle");

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    if (icon) icon.textContent = theme === "light" ? "☀" : "☾";
  }

  const saved = localStorage.getItem(STORAGE_KEY) || "dark";
  apply(saved);

  if (btn) {
    btn.addEventListener("click", () => {
      const current = root.getAttribute("data-theme") === "light" ? "light" : "dark";
      const next = current === "light" ? "dark" : "light";
      localStorage.setItem(STORAGE_KEY, next);
      apply(next);
    });
  }
})();
