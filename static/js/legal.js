/*
 * legal.js: tombol hapus data di halaman Privasi dan Pengaturan.
 *  - [data-action="delete-data"]    -> hapus semua file & riwayat chat milik sesi/akun ini
 *  - [data-action="delete-account"] -> hapus akun (hanya Pengaturan; kata sandi diminta bila akun punya sandi)
 */
(function () {
  "use strict";
  var buttons = document.querySelectorAll("[data-action]");
  if (!buttons.length) return;

  function tr(key, vars) {
    var s = window.MejaDataI18n ? window.MejaDataI18n.t(key) : key;
    return String(s).replace(/\{(\w+)\}/g, function (_, k) { return vars && vars[k] !== undefined ? vars[k] : "{" + k + "}"; });
  }
  function status(btn, text, isError) {
    var box = btn.closest("section, form, div");
    var el = box && box.querySelector(".legal-status, .settings-status");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("is-error", !!isError);
    el.classList.toggle("is-ok", !!text && !isError);
  }
  function confirmDialog(title, message, label) {
    if (window.MejaConfirm) {
      return window.MejaConfirm.ask({ variant: "danger", title: title, message: message, confirmLabel: label, cancelLabel: tr("legal.delete.cancel") });
    }
    return Promise.resolve(window.confirm(message));
  }
  function csrfToken() {
    var root = document.getElementById("settingsRoot");
    return root ? root.getAttribute("data-csrf") || "" : "";
  }
  function send(url, body, withCsrf) {
    var headers = { "Content-Type": "application/json" };
    if (withCsrf) headers["X-CSRF-Token"] = csrfToken();
    return fetch(url, { method: "POST", credentials: "same-origin", noLoader: true, headers: headers, body: JSON.stringify(body) })
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; }); });
  }
  function errorText(data) {
    var code = data && data.error;
    var key = code ? "settings.error." + code : "";
    var s = key ? tr(key) : key;
    return s && s !== key ? s : tr("legal.delete.failed");
  }

  buttons.forEach(function (btn) {
    var action = btn.getAttribute("data-action");
    if (action !== "delete-data" && action !== "delete-account") return;

    btn.addEventListener("click", function () {
      var isAccount = action === "delete-account";
      var pwInput = isAccount ? document.getElementById("deleteAccountPassword") : null;
      if (isAccount && pwInput && !pwInput.value) { pwInput.focus(); status(btn, tr("settings.error.current_wrong"), true); return; }

      confirmDialog(
        tr(isAccount ? "legal.account.confirmTitle" : "legal.delete.confirmTitle"),
        tr(isAccount ? "legal.account.confirmMsg" : "legal.delete.confirmMsg"),
        tr(isAccount ? "legal.account.confirmBtn" : "legal.delete.confirmBtn")
      ).then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        var req = isAccount
          ? send("/api/settings/delete-account", { confirm: true, current_password: pwInput ? pwInput.value : "" }, true)
          : send("/api/data/delete-all", { confirm: true }, false);
        req.then(function (r) {
          btn.disabled = false;
          if (!r.ok) { status(btn, errorText(r.data), true); return; }
          if (isAccount) { window.location.href = (r.data && r.data.redirect) || "/"; return; }
          status(btn, tr("legal.delete.done", { n: r.data.deleted_files || 0 }), false);
        }).catch(function () { btn.disabled = false; status(btn, tr("legal.delete.failed"), true); });
      });
    });
  });
})();
