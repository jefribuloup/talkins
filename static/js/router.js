/*
 * Router tanpa reload untuk Meja Data.
 * Klik link internal -> ambil HTML halaman tujuan lewat fetch -> ganti HANYA konten
 * di antara navbar dan footer -> jalankan script page tujuan -> perbarui URL (History API).
 * Navbar, footer, tema, bahasa, dan preloader tetap (tidak dimuat ulang).
 *
 * - Kalau ada yang gagal (server error, 404, format tak dikenal), otomatis kembali
 *   ke navigasi biasa supaya user tetap sampai ke halaman tujuan.
 * - Opt-out per link: <a data-no-router>.
 * - API: MejaRouter.go(url)  -> pindah page tanpa reload (fallback ke navigasi biasa)
 *        MejaRouter.signal   -> AbortSignal milik page aktif; batal otomatis saat pindah page,
 *                               dipakai script page untuk listener global (window/document).
 */
(function () {
  "use strict";

  var PERSISTENT_SCRIPTS = ["preloader.js", "theme.js", "i18n.js", "router.js"];
  var supported = !!(window.fetch && window.history && window.history.pushState &&
                     window.AbortController && window.DOMParser);

  var pageCtl = new AbortController();
  var navCtl = null;

  function keyOf(href) {
    var u = new URL(href, window.location.href);
    return u.pathname + u.search;
  }
  var currentKey = keyOf(window.location.href);

  function progress(action) {
    if (window.MejaProgress) window.MejaProgress[action]();
  }

  function fallback(url) {
    window.location.href = url; // navigasi biasa (reload penuh)
  }

  function scriptName(src) {
    try { return new URL(src, window.location.href).pathname.split("/").pop(); }
    catch (e) { return ""; }
  }
  function isPersistentScript(s) {
    return !!s.getAttribute("src") && PERSISTENT_SCRIPTS.indexOf(scriptName(s.getAttribute("src"))) !== -1;
  }
  function isKeep(el) {
    return el.tagName === "SCRIPT" || el.matches("header.navbar, footer.site-footer, .md-preloader");
  }

  function localCssPath(link) {
    try {
      var p = new URL(link.getAttribute("href"), window.location.href).pathname;
      return p.indexOf("/static/css/") === 0 ? p : null;
    } catch (e) { return null; }
  }
  function cssPaths(root) {
    var out = [];
    root.querySelectorAll('link[rel="stylesheet"]').forEach(function (l) {
      var p = localCssPath(l);
      if (p) out.push(p);
    });
    return out;
  }

  // Pasang CSS milik page tujuan (mis. workspace.css) SEBELUM konten diganti, supaya tidak berkedip.
  function loadNewStyles(doc) {
    var have = cssPaths(document);
    var jobs = [];
    doc.querySelectorAll('link[rel="stylesheet"]').forEach(function (l) {
      var p = localCssPath(l);
      if (!p || have.indexOf(p) !== -1) return;
      jobs.push(new Promise(function (resolve) {
        var n = document.createElement("link");
        n.rel = "stylesheet";
        n.href = l.getAttribute("href");
        n.onload = n.onerror = resolve;
        document.head.appendChild(n);
        setTimeout(resolve, 5000);
      }));
    });
    return Promise.all(jobs);
  }

  function syncBodyData(newBody) {
    var body = document.body;
    Array.prototype.slice.call(body.attributes).forEach(function (a) {
      if (a.name.indexOf("data-") === 0) body.removeAttribute(a.name);
    });
    Array.prototype.slice.call(newBody.attributes).forEach(function (a) {
      if (a.name.indexOf("data-") === 0) body.setAttribute(a.name, a.value);
    });
  }

  function swap(doc, finalUrl, push) {
    // 1) page lama selesai: batalkan listener global miliknya
    window.dispatchEvent(new CustomEvent("mejadata:pageleave"));
    pageCtl.abort();
    pageCtl = new AbortController();

    var body = document.body;
    var footer = body.querySelector("footer.site-footer");

    // 2) buang konten & script page lama (navbar, footer, script bersama tetap)
    Array.prototype.slice.call(body.children).forEach(function (el) {
      if (el.tagName === "SCRIPT") { if (!isPersistentScript(el)) el.remove(); return; }
      if (!isKeep(el)) el.remove();
    });

    // 3) pasang konten page baru tepat sebelum footer
    Array.prototype.slice.call(doc.body.children).forEach(function (el) {
      if (isKeep(el)) return;
      body.insertBefore(document.importNode(el, true), footer || null);
    });

    // 4) judul, atribut data-* body (mis. data-file-id), URL
    if (doc.title) document.title = doc.title;
    syncBodyData(doc.body);
    if (push) {
      window.history.pushState({ meja: 1 }, "", finalUrl);
    } else if (keyOf(finalUrl) !== keyOf(window.location.href)) {
      window.history.replaceState({ meja: 1 }, "", finalUrl);
    }
    currentKey = keyOf(window.location.href);

    // 5) buang CSS page lama yang tidak dipakai page baru
    var wanted = cssPaths(doc);
    document.querySelectorAll('head link[rel="stylesheet"]').forEach(function (l) {
      var p = localCssPath(l);
      if (p && wanted.indexOf(p) === -1) l.remove();
    });

    // 6) terjemahkan teks statis konten baru sesuai bahasa yang dipilih
    if (window.MejaDataI18n) window.MejaDataI18n.apply(window.MejaDataI18n.getLang());
  }

  // Jalankan ulang script milik page tujuan secara berurutan.
  function runPageScripts(doc) {
    var scripts = Array.prototype.filter.call(doc.body.children, function (el) {
      return el.tagName === "SCRIPT" && !isPersistentScript(el);
    });
    return scripts.reduce(function (chain, s) {
      return chain.then(function () {
        return new Promise(function (resolve) {
          var n = document.createElement("script");
          if (s.getAttribute("src")) {
            n.async = false;
            n.onload = n.onerror = resolve;
            n.src = s.getAttribute("src");
            document.body.appendChild(n);
          } else {
            n.textContent = s.textContent;
            document.body.appendChild(n);
            resolve();
          }
        });
      });
    }, Promise.resolve());
  }

  function navigate(url, push) {
    if (navCtl) navCtl.abort();
    var ctl = (navCtl = new AbortController());
    progress("start");

    var finalUrl = url;
    return window.fetch(url, {
      noLoader: true, // progress bar di navbar sudah jadi indikatornya
      credentials: "same-origin",
      signal: ctl.signal,
      headers: { Accept: "text/html" },
    })
      .then(function (res) {
        var type = res.headers.get("content-type") || "";
        if (!res.ok || type.indexOf("text/html") === -1) throw new Error("bukan halaman");
        finalUrl = res.url || url;
        return res.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        if (!doc.body || !doc.querySelector("header.navbar")) throw new Error("struktur tidak dikenali");
        return loadNewStyles(doc).then(function () { return doc; });
      })
      .then(function (doc) {
        if (ctl.signal.aborted) return;
        swap(doc, finalUrl, push);
        return runPageScripts(doc).then(function () {
          if (push) window.scrollTo(0, 0);
          progress("done");
        });
      })
      .catch(function (err) {
        if (ctl.signal.aborted) return; // digantikan navigasi yang lebih baru
        fallback(url);
      });
  }

  function eligible(a, e) {
    if (!a || !a.href) return null;
    if (e && (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)) return null;
    if (a.target && a.target !== "_self") return null;
    if (a.hasAttribute("download") || a.hasAttribute("data-no-router")) return null;
    var u;
    try { u = new URL(a.href, window.location.href); } catch (err) { return null; }
    if (u.origin !== window.location.origin) return null;
    if (u.pathname.indexOf("/api/") === 0 || u.pathname.indexOf("/static/") === 0) return null;
    return u;
  }

  window.MejaRouter = {
    go: function (url) {
      var u = new URL(url, window.location.href);
      if (!supported || u.origin !== window.location.origin) { fallback(u.href); return; }
      navigate(u.href, true);
    },
  };
  Object.defineProperty(window.MejaRouter, "signal", { get: function () { return pageCtl.signal; } });

  if (!supported) return;

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented) return;
    var a = e.target.closest ? e.target.closest("a") : null;
    var u = eligible(a, e);
    if (!u) return;
    var same = u.pathname === window.location.pathname && u.search === window.location.search;
    if (same && u.hash) return; // anchor di halaman yang sama: biarkan browser
    e.preventDefault();
    if (same) { window.scrollTo(0, 0); return; } // link ke halaman yang sedang dibuka: tidak perlu reload
    navigate(u.href, true);
  });

  // Tombol Back/Forward browser
  window.addEventListener("popstate", function () {
    if (keyOf(window.location.href) === currentKey) return; // hanya ganti #hash
    navigate(window.location.href, false);
  });
})();
