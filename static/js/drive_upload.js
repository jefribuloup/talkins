/*
 * drive_upload.js — unggah file BESAR (di atas batas ±4 MB server Vercel) lewat Google Drive.
 *
 * Alur:
 *  1. Minta izin Google Drive (scope drive.file: hanya file yang dibuat aplikasi ini) lewat popup
 *     Google Identity Services. Token ada di browser; tidak butuh client secret.
 *  2. Browser mengunggah file LANGSUNG ke Google Drive user (resumable upload), tidak lewat Vercel,
 *     jadi tidak kena batas ukuran body.
 *  3. Browser mengirim id file + token ke /api/upload-drive; server mengunduh dari Drive lalu
 *     memprosesnya persis seperti upload biasa (termasuk deteksi file duplikat).
 *
 * Pemakaian:
 *   if (MejaDrive.isLarge(file)) {
 *     const data = await MejaDrive.uploadAndIngest(file, (msg) => setStatus(msg, false));
 *   }
 * `data` berbentuk sama dengan balasan /api/upload ({file_id, sheets, ...} atau {duplicate: true, ...}).
 */
(function () {
  "use strict";
  if (window.MejaDrive) return; // router bisa memuat ulang script saat pindah halaman

  var SCOPE = "https://www.googleapis.com/auth/drive.file";
  var GIS_SRC = "https://accounts.google.com/gsi/client";
  var DEFAULT_LIMIT_BYTES = 4 * 1024 * 1024;

  var cfgPromise = null;
  var gisPromise = null;
  var cachedToken = null; // { value, exp }

  function t(key, vars) {
    var s = window.MejaDataI18n ? window.MejaDataI18n.t(key) : key;
    return String(s).replace(/\{(\w+)\}/g, function (_, k) {
      return vars && vars[k] !== undefined ? vars[k] : "{" + k + "}";
    });
  }

  function fail(code, vars) {
    var err = new Error(t("drive.err." + code, vars));
    err.code = code;
    return err;
  }

  function mb(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1);
  }

  // File dianggap "besar" bila melewati batas unggahan server (meta max-upload-mb, via MejaHttp)
  function isLarge(file) {
    if (!file || typeof file.size !== "number") return false;
    if (window.MejaHttp && window.MejaHttp.checkUploadSize) return !!window.MejaHttp.checkUploadSize(file);
    return file.size > DEFAULT_LIMIT_BYTES;
  }

  function getConfig() {
    if (!cfgPromise) {
      cfgPromise = fetch("/api/drive/config", { credentials: "same-origin" })
        .then(function (res) {
          return res.json().then(function (j) {
            if (!res.ok) throw new Error(j && j.error ? j.error : "config");
            return j;
          });
        })
        .catch(function (e) {
          cfgPromise = null; // boleh dicoba lagi
          throw e;
        });
    }
    return cfgPromise;
  }

  function loadGis() {
    if (window.google && window.google.accounts && window.google.accounts.oauth2) return Promise.resolve();
    if (!gisPromise) {
      gisPromise = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = GIS_SRC;
        s.async = true;
        s.onload = function () { resolve(); };
        s.onerror = function () { gisPromise = null; reject(fail("gis")); };
        document.head.appendChild(s);
      });
    }
    return gisPromise;
  }

  // Siapkan di belakang layar (config + script Google) supaya popup izin cepat muncul saat file dipilih
  function prepare() {
    getConfig().then(function (cfg) { if (cfg && cfg.enabled) loadGis().catch(function () {}); }).catch(function () {});
  }

  function requestToken(clientId) {
    if (cachedToken && cachedToken.exp - Date.now() > 60 * 1000) return Promise.resolve(cachedToken.value);
    return new Promise(function (resolve, reject) {
      var client = window.google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: SCOPE,
        callback: function (resp) {
          if (!resp || resp.error || !resp.access_token) {
            reject(fail(resp && resp.error === "access_denied" ? "cancelled" : "auth"));
            return;
          }
          var ttl = (parseInt(resp.expires_in, 10) || 3000) * 1000;
          cachedToken = { value: resp.access_token, exp: Date.now() + ttl };
          resolve(resp.access_token);
        },
        error_callback: function (e) {
          reject(fail(e && (e.type === "popup_closed" || e.type === "popup_failed_to_open") ? "cancelled" : "auth"));
        },
      });
      client.requestAccessToken({ prompt: "" });
    });
  }

  // Unggah resumable langsung ke Google Drive. Mengembalikan id file di Drive.
  function uploadToDrive(file, token, onProgress) {
    return fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": file.type || "application/octet-stream",
        "X-Upload-Content-Length": String(file.size),
      },
      body: JSON.stringify({ name: file.name }),
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        cachedToken = null;
        throw fail("auth");
      }
      var sessionUri = res.ok ? res.headers.get("Location") : null;
      if (!sessionUri) throw fail("upload");

      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open("PUT", sessionUri);
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable && onProgress) onProgress(Math.min(99, Math.round((e.loaded / e.total) * 100)));
        };
        xhr.onload = function () {
          if (xhr.status === 200 || xhr.status === 201) {
            try {
              resolve(JSON.parse(xhr.responseText).id);
            } catch (e) {
              reject(fail("upload"));
            }
          } else {
            reject(fail("upload"));
          }
        };
        xhr.onerror = function () { reject(fail("upload")); };
        xhr.send(file);
      });
    });
  }

  function uploadAndIngest(file, onStatus) {
    var say = function (m) { if (onStatus) onStatus(m); };
    var cfg;
    return getConfig()
      .catch(function () { throw fail("notConfigured"); })
      .then(function (c) {
        cfg = c;
        if (!cfg || !cfg.enabled || !cfg.client_id) throw fail("notConfigured");
        if (file.size > cfg.max_mb * 1024 * 1024) {
          throw fail("tooLarge", { name: file.name, size: mb(file.size), max: cfg.max_mb });
        }
        say(t("drive.status.auth"));
        return loadGis();
      })
      .then(function () { return requestToken(cfg.client_id); })
      .then(function (token) {
        say(t("drive.status.uploading", { percent: 0, size: mb(file.size) }));
        return uploadToDrive(file, token, function (p) {
          say(t("drive.status.uploading", { percent: p, size: mb(file.size) }));
        }).then(function (driveId) {
          say(t("drive.status.processing"));
          return fetch("/api/upload-drive", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file_id: driveId, access_token: token }),
          });
        });
      })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data && data.error ? data.error : t("drive.err.upload"));
          return data;
        });
      });
  }

  window.MejaDrive = { isLarge: isLarge, prepare: prepare, uploadAndIngest: uploadAndIngest };
  prepare();
})();
