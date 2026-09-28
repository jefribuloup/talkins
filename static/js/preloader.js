/*
 * Preloader global Meja Data.
 * - Otomatis muncul di setiap proses fetch (muat data, upload, chat, ekspor, dsb.) di semua page.
 * - Berada di tengah layar dan TIDAK memblokir interaksi (pointer-events: none).
 * - Baru tampil kalau proses > 150 ms (biar tidak berkedip), dan minimal tampil 300 ms.
 * - Opt-out per request: fetch(url, { noLoader: true }).
 * - API manual: MejaLoader.show() / MejaLoader.hide().
 * - Progress bar pindah page di bawah navbar: otomatis saat klik link internal,
 *   atau manual lewat MejaProgress.start() / MejaProgress.done().
 */
(function () {
  "use strict";

  var SHOW_DELAY = 150;   // ms sebelum preloader benar-benar tampil
  var MIN_VISIBLE = 300;  // ms minimal tampil setelah muncul

  var active = 0;
  var showTimer = null;
  var hideTimer = null;
  var shownAt = 0;
  var el = null;

  function label() {
    try {
      if (window.MejaDataI18n) return window.MejaDataI18n.t("common.loading");
    } catch (e) {}
    return "Memuat data...";
  }

  function build() {
    if (el) return el;
    el = document.createElement("div");
    el.className = "md-preloader";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<div class="md-preloader-box"><span class="md-spin"></span></div>';
    document.body.appendChild(el);
    return el;
  }

  function reveal() {
    showTimer = null;
    build();
    el.setAttribute("aria-label", label());
    el.classList.add("show");
    shownAt = Date.now();
  }

  function show() {
    active++;
    if (active === 1) {
      clearTimeout(hideTimer);
      hideTimer = null;
      showTimer = setTimeout(reveal, SHOW_DELAY);
    }
  }

  function hide() {
    if (active > 0) active--;
    if (active !== 0) return;
    clearTimeout(showTimer);
    showTimer = null;
    if (el && el.classList.contains("show")) {
      var wait = Math.max(0, MIN_VISIBLE - (Date.now() - shownAt));
      hideTimer = setTimeout(function () {
        el.classList.remove("show");
        hideTimer = null;
      }, wait);
    }
  }

  var nativeFetch = window.fetch;
  if (typeof nativeFetch === "function") {
    window.fetch = function (input, init) {
      if (init && init.noLoader) return nativeFetch.apply(this, arguments);
      show();
      var p;
      try {
        p = nativeFetch.apply(this, arguments);
      } catch (err) {
        hide();
        throw err;
      }
      return p.then(
        function (res) { hide(); return res; },
        function (err) { hide(); throw err; }
      );
    };
  }


  // ---------- Progress bar pindah page (di bawah navbar) ----------

  var progEl = null;
  var progBar = null;
  var progValue = 0;
  var progTimer = null;
  var progSafety = null;

  function progInit() {
    if (progEl) return true;
    progEl = document.getElementById("navProgress");
    progBar = progEl ? progEl.firstElementChild : null;
    return !!progBar;
  }

  function progSet(v) {
    progValue = v;
    progBar.style.transform = "scaleX(" + v + ")";
  }

  function progStart() {
    if (!progInit()) return;
    clearInterval(progTimer);
    clearTimeout(progSafety);
    progBar.style.transition = "none";
    progSet(0);
    void progBar.offsetWidth; // paksa reflow supaya animasi mulai dari 0
    progBar.style.transition = "";
    progEl.classList.add("active");
    progSet(0.08);
    // merayap pelan mendekati 90%, lalu menunggu halaman baru terbuka
    progTimer = setInterval(function () {
      progSet(progValue + (0.9 - progValue) * 0.08);
    }, 200);
    // pengaman: kalau halaman tidak berpindah (mis. link berupa unduhan), tutup bar
    progSafety = setTimeout(progDone, 12000);
  }

  function progDone() {
    if (!progInit()) return;
    clearInterval(progTimer);
    clearTimeout(progSafety);
    progSet(1);
    setTimeout(function () {
      progEl.classList.remove("active");
      setTimeout(function () {
        progBar.style.transition = "none";
        progSet(0);
      }, 260);
    }, 200);
  }

  function isInternalNavLink(a, e) {
    if (!a || !a.href) return false;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    if (a.target && a.target !== "_self") return false;
    if (a.hasAttribute("download")) return false;
    var url;
    try { url = new URL(a.href, window.location.href); } catch (err) { return false; }
    if (url.origin !== window.location.origin) return false;
    if (url.pathname.indexOf("/api/") === 0 || url.pathname.indexOf("/static/") === 0) return false;
    // link ke anchor di halaman yang sama tidak memicu perpindahan page
    if (url.pathname === window.location.pathname && url.search === window.location.search) return false;
    return true;
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a") : null;
    if (e.defaultPrevented || !isInternalNavLink(a, e)) return;
    progStart();
  });

  // tombol Back/Forward yang mengambil halaman dari cache browser: pastikan bar tidak nyangkut
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) progDone();
  });

  window.MejaProgress = { start: progStart, done: progDone };

  window.MejaLoader = { show: show, hide: hide };
})();
