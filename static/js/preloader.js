/*
 * Preloader global Meja Data.
 * - Otomatis muncul di setiap proses fetch (muat data, upload, chat, ekspor, dsb.) di semua page.
 * - Berada di tengah layar dan TIDAK memblokir interaksi (pointer-events: none).
 * - Baru tampil kalau proses > 150 ms (biar tidak berkedip), dan minimal tampil 300 ms.
 * - Opt-out per request: fetch(url, { noLoader: true }).
 * - API manual: MejaLoader.show() / MejaLoader.hide().
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

  window.MejaLoader = { show: show, hide: hide };
})();
