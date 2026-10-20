/* Meja Data: tab "Rumus".
 * User memblok sel (seret), mengklik header kolom, atau mengklik header baris di tabel data;
 * tab Rumus lalu menampilkan rumus yang cocok beserta pratinjau hasilnya.
 *
 *   MejaFormula.init({ table: "#data-table", container: "#tab-rumus" });
 */
(function (global) {
  "use strict";

  var DEFAULTS = {
    table: "#data-table",          // <table> yang menampilkan working_data
    container: "#tab-rumus",       // elemen isi tab Rumus
    endpoint: "/api/formula-suggest",
    firstExcelRow: 2,              // baris Excel tempat data pertama berada (baris 1 = header)
    headerSkip: 0,                 // jumlah <th> di thead yang bukan kolom data (mis. kolom nomor)
    separator: ",",                // ";" untuk Excel berlokal Indonesia
    debounceMs: 250,
    headerSelect: true,            // klik <th> di thead memilih satu kolom penuh
    rowHeaderSelect: true,         // klik <th> di tbody memilih satu baris penuh
    onSelect: null                 // function(data) dipanggil setelah saran diterima
  };

  var STYLE_ID = "mf-style";
  var CSS = [
    ".mf-table td.mf-sel{background:rgba(37,99,235,.16);outline:1px solid rgba(37,99,235,.55);outline-offset:-1px}",
    ".mf-table.mf-dragging,.mf-table.mf-dragging *{user-select:none;cursor:cell}",
    ".mf-wrap{font-size:14px;line-height:1.45}",
    ".mf-info{margin:0 0 12px;opacity:.75}",
    ".mf-info strong{opacity:1}",
    ".mf-group{margin:0 0 18px}",
    ".mf-group h3{font-size:14px;font-weight:600;margin:0 0 8px}",
    ".mf-card{border:1px solid rgba(128,128,128,.3);border-radius:6px;padding:10px 12px;margin:0 0 8px}",
    ".mf-row{display:flex;gap:8px;align-items:flex-start;justify-content:space-between}",
    ".mf-code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;word-break:break-all;margin:0}",
    ".mf-copy{flex:none;font:inherit;font-size:12px;padding:2px 8px;border:1px solid rgba(128,128,128,.4);border-radius:4px;background:transparent;color:inherit;cursor:pointer}",
    ".mf-copy:focus-visible{outline:2px solid rgba(37,99,235,.8);outline-offset:1px}",
    ".mf-desc{margin:4px 0 0;opacity:.75;font-size:13px}",
    ".mf-result{margin:8px 0 0}",
    ".mf-result b{font-variant-numeric:tabular-nums}",
    ".mf-prev{border-collapse:collapse;margin:8px 0 0;font-size:13px}",
    ".mf-prev caption{text-align:left;opacity:.65;padding-bottom:2px}",
    ".mf-prev th,.mf-prev td{padding:2px 14px 2px 0;text-align:left;font-weight:400;white-space:pre}",
    ".mf-prev td:last-child{font-weight:600}",
    ".mf-msg{opacity:.75;margin:0}",
    ".mf-err{color:#b91c1c;margin:0}"
  ].join("\n");

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement("style");
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function copyText(text, btn) {
    var done = function () {
      var old = btn.textContent;
      btn.textContent = "Tersalin";
      setTimeout(function () { btn.textContent = old; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    } else {
      fallback();
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) { /* abaikan */ }
      ta.remove();
      done();
    }
  }

  function init(userOpts) {
    var o = Object.assign({}, DEFAULTS, userOpts || {});
    var table = document.querySelector(o.table);
    var box = document.querySelector(o.container);
    if (!table || !box) {
      console.warn("MejaFormula: tabel (" + o.table + ") atau wadah tab Rumus (" + o.container + ") tidak ditemukan.");
      return null;
    }
    injectStyle();
    table.classList.add("mf-table");

    var anchor = null, focus = null, dragging = false, timer = null, ctrl = null;

    /* ---------- akses tabel ---------- */
    function bodyRows() { return Array.prototype.slice.call(table.querySelectorAll("tbody tr")); }
    function cellsOf(tr) { return Array.prototype.slice.call(tr.querySelectorAll("td")); }

    function locate(td) {
      var tr = td.closest("tr");
      if (!tr || !tr.parentElement || tr.parentElement.tagName !== "TBODY") return null;
      var c = cellsOf(tr).indexOf(td);
      return c < 0 ? null : { r: tr.sectionRowIndex, c: c };
    }

    function rect() {
      if (!anchor || !focus) return null;
      return {
        r1: Math.min(anchor.r, focus.r), r2: Math.max(anchor.r, focus.r),
        c1: Math.min(anchor.c, focus.c), c2: Math.max(anchor.c, focus.c)
      };
    }

    function dataRowOf(tr, fallback) {
      var d = tr && tr.dataset ? tr.dataset.row : undefined;
      return d !== undefined && d !== "" && !isNaN(Number(d)) ? Number(d) : fallback;
    }

    function paint() {
      Array.prototype.forEach.call(table.querySelectorAll(".mf-sel"), function (n) { n.classList.remove("mf-sel"); });
      var rc = rect();
      if (!rc) return;
      var rows = bodyRows();
      for (var r = rc.r1; r <= rc.r2 && r < rows.length; r++) {
        var tds = cellsOf(rows[r]);
        for (var c = rc.c1; c <= rc.c2 && c < tds.length; c++) tds[c].classList.add("mf-sel");
      }
    }

    function clear() {
      anchor = focus = null;
      dragging = false;
      table.classList.remove("mf-dragging");
      clearTimeout(timer);
      if (ctrl) ctrl.abort();
      paint();
      renderMessage("Blok sel, pilih kolom lewat headernya, atau pilih baris lewat headernya untuk melihat rumus yang cocok.");
    }

    /* ---------- pemilihan ---------- */
    function schedule() {
      paint();
      clearTimeout(timer);
      timer = setTimeout(fetchSuggestions, o.debounceMs);
    }

    function selectColumn(th, extend) {
      var ths = Array.prototype.slice.call(table.querySelectorAll("thead th"));
      var c = ths.indexOf(th) - o.headerSkip;
      var rows = bodyRows();
      if (c < 0 || !rows.length) return;
      var last = rows.length - 1;
      var keep = extend && anchor ? anchor.c : c;
      anchor = { r: 0, c: keep };
      focus = { r: last, c: c };
      schedule();
    }

    function selectRow(th, extend) {
      var tr = th.closest("tr");
      if (!tr) return;
      var n = cellsOf(tr).length;
      if (!n) return;
      var r = tr.sectionRowIndex;
      var keep = extend && anchor ? anchor.r : r;
      anchor = { r: keep, c: 0 };
      focus = { r: r, c: n - 1 };
      schedule();
    }

    function onDown(e) {
      if (e.button !== 0) return;
      var t = e.target.closest ? e.target.closest("td, th") : null;
      if (!t || !table.contains(t)) return;
      if (t.tagName === "TD" && t.closest("tbody")) {
        var p = locate(t);
        if (!p) return;
        if (global.getSelection) global.getSelection().removeAllRanges();
        table.classList.add("mf-dragging");
        dragging = true;
        anchor = e.shiftKey && anchor ? anchor : p;
        focus = p;
        paint();
      } else if (o.headerSelect && t.closest("thead")) {
        selectColumn(t, e.shiftKey);
      } else if (o.rowHeaderSelect && t.tagName === "TH" && t.closest("tbody")) {
        selectRow(t, e.shiftKey);
      }
    }

    function onOver(e) {
      if (!dragging) return;
      var t = e.target.closest ? e.target.closest("td") : null;
      if (!t || !table.contains(t)) return;
      var p = locate(t);
      if (p && (!focus || p.r !== focus.r || p.c !== focus.c)) {
        focus = p;
        paint();
      }
    }

    function onUp() {
      if (!dragging) return;
      dragging = false;
      table.classList.remove("mf-dragging");
      schedule();
    }

    function onKey(e) {
      if (e.key === "Escape" && anchor) clear();
    }

    /* ---------- permintaan ke server ---------- */
    function fetchSuggestions() {
      var rc = rect();
      if (!rc) return;
      var rows = bodyRows();
      if (!rows.length) return;
      var matrix = [];
      for (var r = rc.r1; r <= rc.r2 && r < rows.length; r++) {
        var tds = cellsOf(rows[r]);
        var line = [];
        for (var c = rc.c1; c <= rc.c2; c++) {
          var td = tds[c];
          line.push(td ? (td.dataset.value !== undefined ? td.dataset.value : td.textContent.trim()) : "");
        }
        matrix.push(line);
      }
      if (ctrl) ctrl.abort();
      var mine = (ctrl = new AbortController());
      renderMessage("Menghitung rumus…");
      fetch(o.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rows: matrix,
          start_row: o.firstExcelRow + dataRowOf(rows[rc.r1], rc.r1),
          start_col: rc.c1 + 1,
          sep: o.separator
        }),
        signal: mine.signal
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) throw new Error(data.error || "Server menjawab dengan status " + res.status + ".");
          return data;
        });
      }).then(function (data) {
        if (mine !== ctrl) return;
        render(data);
        if (typeof o.onSelect === "function") o.onSelect(data);
      }).catch(function (err) {
        if (err && err.name === "AbortError") return;
        renderError("Saran rumus gagal dimuat: " + (err && err.message ? err.message : err));
      });
    }

    /* ---------- tampilan tab Rumus ---------- */
    function mount(node) {
      var wrap = el("div", "mf-wrap");
      wrap.appendChild(node);
      box.replaceChildren(wrap);
      return wrap;
    }

    function renderMessage(text) { mount(el("p", "mf-msg", text)); }
    function renderError(text) { mount(el("p", "mf-err", text)); }

    function card(s) {
      var c = el("div", "mf-card");
      var row = el("div", "mf-row");
      row.appendChild(el("p", "mf-code", s.formula));
      var btn = el("button", "mf-copy", "Salin");
      btn.type = "button";
      btn.setAttribute("aria-label", "Salin rumus " + s.name);
      btn.addEventListener("click", function () { copyText(s.formula, btn); });
      row.appendChild(btn);
      c.appendChild(row);
      c.appendChild(el("p", "mf-desc", s.description));

      if (s.kind === "single") {
        var res = el("p", "mf-result");
        res.appendChild(document.createTextNode("Hasil: "));
        res.appendChild(el("b", null, s.result === "" ? "(kosong)" : s.result));
        c.appendChild(res);
      } else if (s.preview && s.preview.length) {
        var t = el("table", "mf-prev");
        t.appendChild(el("caption", null, "Contoh hasil, " + s.preview.length + " baris pertama"));
        s.preview.forEach(function (p) {
          var tr = el("tr");
          tr.appendChild(el("td", null, p.input));
          tr.appendChild(el("td", null, "→"));
          tr.appendChild(el("td", null, p.output));
          t.appendChild(tr);
        });
        c.appendChild(t);
      }
      return c;
    }

    function render(data) {
      var sel = data.selection || {};
      var list = data.suggestions || [];
      var frag = document.createDocumentFragment();

      var info = el("p", "mf-info");
      info.appendChild(el("strong", null, sel.ref || "-"));
      info.appendChild(document.createTextNode(
        "  " + sel.rows + " baris × " + sel.cols + " kolom"
        + (sel.numeric ? ", " + sel.numeric + " angka" : "")
        + (sel.text ? ", " + sel.text + " teks" : "")
        + (sel.date ? ", " + sel.date + " tanggal" : "")
        + (sel.blank ? ", " + sel.blank + " kosong" : "")
      ));
      frag.appendChild(info);

      if (!list.length) {
        frag.appendChild(el("p", "mf-msg", "Belum ada rumus yang cocok untuk pilihan ini."));
      } else {
        var groups = [], index = {};
        list.forEach(function (s) {
          if (!(s.category in index)) { index[s.category] = groups.length; groups.push({ name: s.category, items: [] }); }
          groups[index[s.category]].items.push(s);
        });
        groups.forEach(function (g) {
          var sec = el("section", "mf-group");
          sec.appendChild(el("h3", null, g.name));
          g.items.forEach(function (s) { sec.appendChild(card(s)); });
          frag.appendChild(sec);
        });
      }
      var holder = el("div");
      holder.appendChild(frag);
      mount(holder);
    }

    /* ---------- pasang ---------- */
    table.addEventListener("mousedown", onDown);
    table.addEventListener("mouseover", onOver);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("keydown", onKey);

    // Tabel dirender ulang (upload baru, filter, reset): pilihan lama tidak berlaku lagi.
    var observer = new MutationObserver(function () { if (anchor) clear(); });
    observer.observe(table, { childList: true, subtree: true });

    clear();

    return {
      clear: clear,
      destroy: function () {
        observer.disconnect();
        table.removeEventListener("mousedown", onDown);
        table.removeEventListener("mouseover", onOver);
        document.removeEventListener("mouseup", onUp);
        document.removeEventListener("keydown", onKey);
        table.classList.remove("mf-table", "mf-dragging");
        paint();
      }
    };
  }

  global.MejaFormula = { init: init };
})(window);
