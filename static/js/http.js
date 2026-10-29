/*
 * http.js — pesan error yang ramah untuk SEMUA request fetch di aplikasi.
 *
 * Masalah yang diselesaikan:
 * - Server/platform kadang membalas teks polos atau HTML (mis. "Request Entity Too Large" dari
 *   Vercel untuk body > ±4,5 MB). `res.json()` lalu gagal dengan pesan teknis
 *   `Unexpected token 'R', "Request En"... is not valid JSON` yang sampai ke layar user.
 * - Jaringan putus -> `TypeError: Failed to fetch`.
 *
 * Cara kerja: window.fetch dibungkus sekali (dimuat PERTAMA di tiap halaman). Kalau body balasan
 * bukan JSON, `res.json()` melempar Error berisi kalimat ramah sesuai status HTTP & bahasa aktif.
 * Kode lama (`const data = await res.json(); if (!res.ok) throw new Error(data.error ...)`) tetap
 * jalan tanpa diubah, dan `err.message` yang ditampilkan ke user selalu pesan yang mudah dipahami.
 */
(function () {
  if (window.MejaHttp) return; // router bisa memuat ulang script saat pindah halaman

  var LANG_KEY = "meja-data-lang";

  var MSG = {
    id: {
      tooLarge:
        "Data yang dikirim terlalu besar. Kalau kamu sedang mengunggah file, kecilkan dulu ukurannya " +
        "(maksimal sekitar {mb} MB) atau unggah lewat link.",
      fileTooLarge:
        "File “{name}” berukuran {size} MB, sedangkan batas unggahan sekitar {mb} MB. " +
        "Kecilkan dulu filenya (hapus sheet atau baris yang tidak dipakai) atau unggah lewat link.",
      session: "Sesi kamu sudah habis. Silakan masuk lagi.",
      forbidden: "Kamu tidak punya akses ke data ini.",
      notFound:
        "Data yang diminta tidak ditemukan. Mungkin file sudah dihapus atau sesi sudah berganti. " +
        "Coba muat ulang halaman atau unggah filenya lagi.",
      busy: "Server sedang sibuk atau butuh waktu terlalu lama. Tunggu sebentar lalu coba lagi.",
      rateLimit: "Terlalu banyak permintaan dalam waktu singkat. Tunggu sebentar lalu coba lagi.",
      server: "Maaf, server sedang bermasalah. Coba lagi beberapa saat lagi.",
      generic: "Maaf, permintaanmu belum berhasil diproses. Coba lagi sebentar lagi.",
      offline: "Koneksi internet sedang terputus. Periksa jaringanmu lalu coba lagi.",
      network: "Tidak bisa terhubung ke server. Periksa koneksi internetmu lalu coba lagi.",
      errorTitle: "Upload belum berhasil",
      close: "Tutup pesan",
    },
    en: {
      tooLarge:
        "The data you sent is too large. If you're uploading a file, make it smaller " +
        "(up to about {mb} MB) or upload it via a link.",
      fileTooLarge:
        "The file “{name}” is {size} MB, but the upload limit is about {mb} MB. " +
        "Please shrink it (remove unused sheets or rows) or upload it via a link.",
      session: "Your session has expired. Please sign in again.",
      forbidden: "You don't have access to this data.",
      notFound:
        "The requested data couldn't be found. The file may have been deleted or your session changed. " +
        "Try reloading the page or uploading the file again.",
      busy: "The server is busy or took too long. Please wait a moment and try again.",
      rateLimit: "Too many requests in a short time. Please wait a moment and try again.",
      server: "Sorry, the server is having trouble. Please try again in a little while.",
      generic: "Sorry, we couldn't process your request. Please try again in a moment.",
      offline: "Your internet connection is down. Check your network and try again.",
      network: "Can't reach the server. Check your internet connection and try again.",
      errorTitle: "Upload didn't go through",
      close: "Dismiss message",
    },
  };

  function lang() {
    try {
      return localStorage.getItem(LANG_KEY) === "en" ? "en" : "id";
    } catch (e) {
      return "id";
    }
  }

  // Batas unggahan (MB) dikirim server lewat <meta name="max-upload-mb">; default 4 MB (batas Vercel).
  function maxMb() {
    var m = document.querySelector('meta[name="max-upload-mb"]');
    var n = m ? parseFloat(m.getAttribute("content")) : NaN;
    return n > 0 ? n : 4;
  }

  function fill(text, vars) {
    return text.replace(/\{(\w+)\}/g, function (_, k) {
      return vars && vars[k] !== undefined ? vars[k] : "{" + k + "}";
    });
  }

  function msg(key, vars) {
    return fill(MSG[lang()][key], vars);
  }

  function forStatus(status) {
    if (status === 413) return msg("tooLarge", { mb: maxMb() });
    if (status === 401) return msg("session");
    if (status === 403) return msg("forbidden");
    if (status === 404) return msg("notFound");
    if (status === 408 || status === 502 || status === 503 || status === 504) return msg("busy");
    if (status === 429) return msg("rateLimit");
    if (status >= 500) return msg("server");
    return msg("generic");
  }

  // Cek ukuran file SEBELUM dikirim -> user langsung dapat penjelasan, tanpa menunggu upload gagal.
  // Mengembalikan pesan ramah (string) kalau terlalu besar, atau "" kalau aman.
  function checkUploadSize(file) {
    if (!file || typeof file.size !== "number") return "";
    var limit = maxMb() * 1024 * 1024;
    if (file.size <= limit) return "";
    return msg("fileTooLarge", {
      name: file.name || "",
      size: (file.size / (1024 * 1024)).toFixed(1),
      mb: maxMb(),
    });
  }

  // Tampilkan status upload DI LUAR kotak dropzone (elemen `el` ada tepat di bawah kotak).
  // - error  -> kartu error (ikon, judul, pesan ramah, tombol tutup)
  // - lainnya (memproses / berhasil) -> satu baris teks biasa
  // Pesan dimasukkan lewat textContent (bukan innerHTML) supaya aman dari injeksi HTML.
  function renderStatus(el, message, isError) {
    if (!el) return;
    el.textContent = "";
    el.classList.remove("is-error");
    if (!message) return;
    if (!isError) {
      el.textContent = message;
      return;
    }
    el.classList.add("is-error");

    var icon = document.createElement("span");
    icon.className = "upload-error-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "!";

    var title = document.createElement("strong");
    title.className = "upload-error-title";
    title.textContent = msg("errorTitle");

    var text = document.createElement("span");
    text.className = "upload-error-text";
    text.textContent = message;

    var body = document.createElement("div");
    body.className = "upload-error-body";
    body.appendChild(title);
    body.appendChild(text);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "upload-error-close";
    close.setAttribute("aria-label", msg("close"));
    close.textContent = "×";
    close.addEventListener("click", function () {
      renderStatus(el, "", false);
    });

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(close);
  }

  var nativeFetch = window.fetch;
  if (typeof nativeFetch === "function") {
    window.fetch = function () {
      return nativeFetch.apply(this, arguments).then(
        function (res) {
          var nativeJson = res.json;
          res.json = function () {
            return nativeJson.call(res).catch(function () {
              // Body bukan JSON (teks polos / HTML dari server atau platform)
              throw new Error(forStatus(res.status));
            });
          };
          return res;
        },
        function (err) {
          if (err && err.name === "AbortError") throw err; // dibatalkan sengaja (mis. pindah halaman)
          throw new Error(navigator.onLine === false ? msg("offline") : msg("network"));
        }
      );
    };
  }

  window.MejaHttp = {
    forStatus: forStatus,
    checkUploadSize: checkUploadSize,
    message: msg,
    renderStatus: renderStatus,
  };
})();
