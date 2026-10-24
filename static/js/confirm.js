/*
 * Dialog konfirmasi global (MejaConfirm).
 * Tampil sebagai lapisan tersendiri di ATAS semua popup (menempel di <body>, bukan di dalam
 * kotak popup), dengan backdrop blur, ikon, dan animasi. Idempoten: aman dimuat ulang oleh router.
 *
 *   const ok = await MejaConfirm.ask({
 *     variant: "warn" | "danger",   // warna & ikon
 *     title, message, detail,        // detail = nama file (ditampilkan sebagai chip)
 *     confirmLabel, cancelLabel,
 *   });  // -> true bila user menekan tombol konfirmasi, false bila batal/Esc/klik luar
 */
(function () {
  "use strict";

  var active = null; // { el, resolve, onKey, prevFocus, abortHandler }

  function close(result) {
    if (!active) return;
    var cur = active;
    active = null;
    document.removeEventListener("keydown", cur.onKey, true);
    cur.el.classList.remove("show");
    cur.el.classList.add("hide");
    setTimeout(function () {
      if (cur.el.parentNode) cur.el.parentNode.removeChild(cur.el);
    }, 160);
    if (cur.prevFocus && cur.prevFocus.focus) {
      try { cur.prevFocus.focus(); } catch (e) { /* abaikan */ }
    }
    cur.resolve(result);
  }

  function ask(opts) {
    opts = opts || {};
    if (active) close(false); // hanya satu dialog pada satu waktu

    var variant = opts.variant === "danger" ? "danger" : "warn";
    var icon = variant === "danger" ? "🗑" : "!";

    return new Promise(function (resolve) {
      var el = document.createElement("div");
      el.className = "mc-overlay " + variant;
      el.setAttribute("role", "alertdialog");
      el.setAttribute("aria-modal", "true");

      var card = document.createElement("div");
      card.className = "mc-card";

      var badge = document.createElement("div");
      badge.className = "mc-icon";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = icon;

      var title = document.createElement("h3");
      title.className = "mc-title";
      title.textContent = opts.title || "";
      var titleId = "mc-title-" + Date.now();
      title.id = titleId;
      el.setAttribute("aria-labelledby", titleId);

      card.appendChild(badge);
      card.appendChild(title);

      if (opts.detail) {
        var chip = document.createElement("div");
        chip.className = "mc-detail";
        chip.textContent = opts.detail;
        card.appendChild(chip);
      }
      if (opts.message) {
        var msg = document.createElement("p");
        msg.className = "mc-message";
        msg.textContent = opts.message;
        card.appendChild(msg);
      }

      var actions = document.createElement("div");
      actions.className = "mc-actions";
      var btnCancel = document.createElement("button");
      btnCancel.type = "button";
      btnCancel.className = "btn";
      btnCancel.textContent = opts.cancelLabel || "Batal";
      var btnOk = document.createElement("button");
      btnOk.type = "button";
      btnOk.className = "btn " + (variant === "danger" ? "btn-danger" : "btn-primary");
      btnOk.textContent = opts.confirmLabel || "OK";
      actions.appendChild(btnCancel);
      actions.appendChild(btnOk);
      card.appendChild(actions);
      el.appendChild(card);

      btnCancel.addEventListener("click", function () { close(false); });
      btnOk.addEventListener("click", function () { close(true); });
      el.addEventListener("mousedown", function (e) {
        if (e.target === el) close(false); // klik di luar kartu = batal
      });

      var onKey = function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          close(false);
        } else if (e.key === "Tab") {
          // fokus dikunci di dalam dialog
          var btns = [btnCancel, btnOk];
          var i = btns.indexOf(document.activeElement);
          e.preventDefault();
          btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
        }
      };
      document.addEventListener("keydown", onKey, true);

      active = { el: el, resolve: resolve, onKey: onKey, prevFocus: document.activeElement };
      document.body.appendChild(el);
      requestAnimationFrame(function () { el.classList.add("show"); });
      // Aksi destruktif: fokus awal di "Batal" supaya Enter tidak menghapus tanpa sengaja
      (variant === "danger" ? btnCancel : btnOk).focus();

      // Pindah halaman lewat router -> tutup dialog
      if (window.MejaRouter && window.MejaRouter.signal) {
        window.MejaRouter.signal.addEventListener("abort", function () { close(false); }, { once: true });
      }
    });
  }

  window.MejaConfirm = { ask: ask, close: function () { close(false); } };
})();
