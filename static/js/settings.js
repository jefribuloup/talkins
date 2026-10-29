/*
 * settings.js — halaman Pengaturan: ganti username & buat/ubah kata sandi.
 * Semua request memakai JSON + header X-CSRF-Token (token dari data-csrf di <main>).
 */
(function () {
  "use strict";
  var root = document.getElementById("settingsRoot");
  if (!root) return;

  var csrf = root.getAttribute("data-csrf") || "";
  var hasPassword = root.getAttribute("data-has-password") === "1";

  function tr(key, vars) {
    var s = window.MejaDataI18n ? window.MejaDataI18n.t(key) : key;
    return String(s).replace(/\{(\w+)\}/g, function (_, k) {
      return vars && vars[k] !== undefined ? vars[k] : "{" + k + "}";
    });
  }

  // Pesan untuk kode error server: coba settings.error.<kode>, lalu login.error.<kode>
  function errorText(code, vars) {
    var keys = ["settings.error." + code, "login.error." + code];
    for (var i = 0; i < keys.length; i++) {
      var s = tr(keys[i], vars);
      if (s !== keys[i]) return s;
    }
    return tr("login.error.failed");
  }

  function setStatus(form, text, isError) {
    var el = form.querySelector(".settings-status");
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("is-error", !!isError);
    el.classList.toggle("is-ok", !!text && !isError);
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, data: data }; });
    });
  }

  function busy(form, on) {
    var btn = form.querySelector(".settings-submit");
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = tr("login.submit.loading"); }
    else if (btn.dataset.label) { btn.textContent = btn.dataset.label; }
    btn.disabled = on;
  }

  // Tombol Tampilkan / Sembunyikan kata sandi
  root.querySelectorAll(".pw-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = btn.parentNode.querySelector("input");
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("data-i18n", show ? "login.pw.hide" : "login.pw.show");
      btn.textContent = tr(show ? "login.pw.hide" : "login.pw.show");
    });
  });

  // ---------- Username ----------
  var formUsername = document.getElementById("formUsername");
  formUsername.addEventListener("submit", function (e) {
    e.preventDefault();
    var username = formUsername.elements.username.value.trim();
    if (!/^[a-zA-Z0-9_.]{3,30}$/.test(username)) {
      setStatus(formUsername, errorText("username_invalid"), true);
      return;
    }
    setStatus(formUsername, "", false);
    busy(formUsername, true);
    post("/api/settings/username", { username: username })
      .then(function (r) {
        if (!r.ok) { setStatus(formUsername, errorText(r.data.error), true); return; }
        formUsername.elements.username.value = r.data.username;
        // perbarui "@username" di menu akun (navbar tidak ikut dimuat ulang)
        document.querySelectorAll(".user-menu-head small").forEach(function (el) {
          if (el.textContent.charAt(0) === "@") el.textContent = "@" + r.data.username;
        });
        setStatus(formUsername, r.data.unchanged ? tr("settings.ok.usernameSame") : tr("settings.ok.username"), false);
      })
      .catch(function (err) { setStatus(formUsername, err.message, true); })
      .then(function () { busy(formUsername, false); });
  });

  // ---------- Kata sandi ----------
  var formPassword = document.getElementById("formPassword");
  formPassword.addEventListener("submit", function (e) {
    e.preventDefault();
    var f = formPassword.elements;
    var body = { new_password: f.new_password.value };
    if (hasPassword) body.current_password = f.current_password.value;

    if (hasPassword && !body.current_password) { setStatus(formPassword, errorText("current_wrong"), true); return; }
    if (body.new_password.length < 8) { setStatus(formPassword, errorText("password_short"), true); return; }
    if (body.new_password !== f.confirm_password.value) { setStatus(formPassword, errorText("password_mismatch"), true); return; }

    setStatus(formPassword, "", false);
    busy(formPassword, true);
    post("/api/settings/password", body)
      .then(function (r) {
        if (!r.ok) {
          setStatus(formPassword, errorText(r.data.error, { minutes: r.data.minutes }), true);
          return;
        }
        formPassword.reset();
        if (r.data.created) {
          setStatus(formPassword, tr("settings.ok.passwordCreated"), false);
          setTimeout(function () { window.location.reload(); }, 1800); // tampilkan form "Ubah kata sandi"
        } else {
          setStatus(formPassword, tr("settings.ok.passwordChanged"), false);
        }
      })
      .catch(function (err) { setStatus(formPassword, err.message, true); })
      .then(function () { busy(formPassword, false); });
  });
})();
