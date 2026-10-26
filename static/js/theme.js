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

// ---------- Perilaku navbar (persisten antar-halaman) ----------
(function () {
  if (window.__mejaNavBound) return;
  window.__mejaNavBound = true;

  // Tandai menu yang sesuai dengan halaman yang sedang dibuka
  function markActive() {
    var path = window.location.pathname;
    document.querySelectorAll(".navbar .nav-link[data-nav]").forEach(function (a) {
      var targets = a.getAttribute("data-nav").split(",");
      var on = targets.some(function (t) { return t === "/" ? path === "/" : path === t || path.indexOf(t + "/") === 0; });
      a.classList.toggle("active", on);
    });
  }
  markActive();
  window.addEventListener("mejadata:pageenter", markActive);
  window.addEventListener("popstate", function () { setTimeout(markActive, 0); });

  // Bayangan navbar saat halaman di-scroll
  function onScroll() {
    var nav = document.querySelector(".navbar");
    if (nav) nav.classList.toggle("scrolled", window.scrollY > 4);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Tutup dropdown akun saat klik di luar, memilih menu, atau menekan Esc
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.user-menu[open]").forEach(function (d) {
      if (!d.contains(e.target) || e.target.closest(".user-menu-item")) d.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll("details.user-menu[open]").forEach(function (d) { d.removeAttribute("open"); });
  });

  // Sesi habis saat aplikasi dipakai: API membalas 401 -> arahkan ke halaman login
  var origFetch = window.fetch;
  window.fetch = function () {
    return origFetch.apply(this, arguments).then(function (res) {
      if (res.status === 401 && window.location.pathname.indexOf("/login") !== 0) {
        res.clone().json().then(function (j) {
          if (j && j.login_required) {
            window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search);
          }
        }).catch(function () {});
      }
      return res;
    });
  };
})();
