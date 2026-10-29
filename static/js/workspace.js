(function () {
  const fileId = document.body.dataset.fileId;
  const api = (path) => `/api/files/${fileId}${path}`;

  const sheetTabs = document.getElementById("sheetTabs");
  const tableLetters = document.getElementById("tableLetters");
  const tableBody = document.getElementById("tableBody");
  const tableWrap = document.getElementById("tableWrap");
  const panelTabs = document.getElementById("panelTabs");
  const tabViewChat = document.getElementById("tabViewChat");
  const tabViewFormula = document.getElementById("tabViewFormula");
  const tabViewRecap = document.getElementById("tabViewRecap");
  const recapPanel = document.getElementById("recapPanel");
  const formulaPanelEmpty = document.getElementById("formulaPanelEmpty");
  const formulaPanelContent = document.getElementById("formulaPanelContent");
  const pagerInfo = document.getElementById("pagerInfo");

  const opAction = document.getElementById("opAction");
  const opParams = document.getElementById("opParams");
  const opApply = document.getElementById("opApply");

  const chatLog = document.getElementById("chatLog");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");

  // ---------- Mini renderer Markdown (khusus balasan AI) ----------
  // Sengaja tanpa library luar: cukup untuk bold/italic/code/heading/list
  // yang biasa dipakai LLM. HTML di-escape dulu supaya tetap aman (XSS).

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderInlineMd(text) {
    text = text.replace(/`([^`]+?)`/g, "<code>$1</code>");
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__(.+?)__/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
    text = text.replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, "$1<em>$2</em>");
    return text;
  }

  function renderMarkdown(raw) {
    const lines = escapeHtml(raw || "").split("\n");
    const html = [];
    let paraBuf = [];
    let listBuf = [];
    let listType = null;

    const flushList = () => {
      if (listBuf.length) {
        const items = listBuf.map((li) => `<li>${renderInlineMd(li)}</li>`).join("");
        html.push(`<${listType} class="chat-md-list">${items}</${listType}>`);
      }
      listBuf = [];
      listType = null;
    };
    const flushPara = () => {
      if (paraBuf.length) {
        html.push(`<p>${paraBuf.map(renderInlineMd).join("<br>")}</p>`);
      }
      paraBuf = [];
    };

    for (const line of lines) {
      const trimmed = line.trim();
      const headerM = trimmed.match(/^(#{1,6})\s+(.*)$/);
      const bulletM = trimmed.match(/^[-*]\s+(.*)$/);
      const numberM = trimmed.match(/^\d+\.\s+(.*)$/);

      if (headerM) {
        flushPara();
        flushList();
        const level = Math.min(6, headerM[1].length + 2);
        html.push(`<h${level} class="chat-md-heading">${renderInlineMd(headerM[2])}</h${level}>`);
      } else if (bulletM) {
        flushPara();
        if (listType && listType !== "ul") flushList();
        listType = "ul";
        listBuf.push(bulletM[1]);
      } else if (numberM) {
        flushPara();
        if (listType && listType !== "ol") flushList();
        listType = "ol";
        listBuf.push(numberM[1]);
      } else if (trimmed === "") {
        flushPara();
        flushList();
      } else {
        flushList();
        paraBuf.push(line);
      }
    }
    flushPara();
    flushList();
    return html.join("") || "<p></p>";
  }

  // ---------- Grid gaya Excel ----------
  // Tidak ada baris/kolom yang jadi header: semua sel setara. Baris = 1, 2, 3...; kolom = A, B, C...
  // Tabel dirender "virtual": hanya baris/kolom yang terlihat (+ sedikit cadangan) yang jadi DOM.
  // Area kosong di luar data ditampilkan seperti Excel, dan bertambah sendiri saat user scroll
  // mendekati tepi bawah/kanan.
  const ROW_H = 26;          // tinggi tiap baris (px)
  const COL_W = 96;          // lebar standar kolom KOSONG (px)
  const MIN_FILLED_W = 48;   // lebar minimum kolom yang berisi data
  const MAX_FILLED_W = 420;  // lebar maksimum kolom yang berisi data
  const GUTTER_W = 52;       // lebar kolom nomor baris
  const HEAD_H = 26;         // tinggi baris huruf kolom
  const MAX_ROWS = 1048576;  // batas Excel
  const MAX_COLS = 16384;    // batas Excel (kolom XFD)
  const MIN_ROWS = 30;       // minimal baris yang tampil walau data sedikit
  const MIN_COLS = 15;       // minimal kolom yang tampil walau data sedikit
  const PAD_ROWS = 10;       // baris kosong ekstra setelah data terakhir
  const PAD_COLS = 3;        // kolom kosong ekstra setelah data terakhir
  const GROW_ROWS = 50;      // baris kosong yang ditambah tiap user scroll ke tepi bawah
  const GROW_COLS = 10;      // kolom kosong yang ditambah tiap user scroll ke tepi kanan
  const BLOCK = 200;         // jumlah baris data per permintaan ke server
  const OVERSCAN_ROWS = 8;
  const OVERSCAN_COLS = 2;

  const state = {
    sheet: null,
    gen: 0,                    // naik tiap loadPreview -> respons lama dibuang
    meta: null,                // {columns,total_rows,total_cols,formula_columns,formula_cells,row_order_dirty}
    blocks: new Map(),         // indeks blok -> array baris
    loading: new Map(),        // indeks blok -> Promise (sedang dimuat)
    failed: new Set(),         // blok yang gagal dimuat (tidak diulang sampai sheet dimuat ulang)
    viewRows: 0,               // jumlah baris yang sedang ditampilkan (data + kosong)
    viewCols: 0,
    sel: { rects: [], anchor: null }, // pilihan = daftar persegi panjang {r1,c1,r2,c2} (0-based, inklusif)
    precedents: new Set(),     // sel "bahan rumus" yang lagi ditandai: key "r,c"
    colW: [],                  // lebar (px) tiap kolom data; kolom di luar data memakai COL_W
    colX: [0],                 // posisi kiri kumulatif: colX[c] = jumlah lebar kolom 0..c-1
    lastWin: null,
  };

  const dataTable = document.getElementById("dataTable");
  const tableCols = document.getElementById("tableCols");

  // ---------- Sheet tabs ----------

  async function loadSheets() {
    const res = await fetch(api("/sheets"));
    const data = await res.json();
    sheetTabs.innerHTML = "";
    data.sheets.forEach((name, i) => {
      const tab = document.createElement("div");
      tab.className = "sheet-tab" + (i === 0 ? " active" : "");
      tab.textContent = name;
      tab.addEventListener("click", () => selectSheet(name));
      sheetTabs.appendChild(tab);
    });
    if (data.sheets.length) selectSheet(data.sheets[0]);
  }

  function selectSheet(name) {
    state.sheet = name;
    [...sheetTabs.children].forEach((el) => el.classList.toggle("active", el.textContent === name));
    loadPreview();
  }

  // ---------- Tab kanan: Chat | Rumus ----------

  function setPanelTab(name) {
    panelTabs.querySelectorAll(".panel-tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === name);
    });
    tabViewChat.hidden = name !== "chat";
    tabViewFormula.hidden = name !== "formula";
    tabViewRecap.hidden = name !== "recap";
    if (name === "recap" && recap.stale) loadRecap();
    if (name === "chat") chatLog.scrollTop = chatLog.scrollHeight;
  }

  panelTabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".panel-tab");
    if (btn) setPanelTab(btn.dataset.tab);
  });

  // ---------- Huruf kolom ----------

  // 0 -> A, 25 -> Z, 26 -> AA (sama persis dgn penamaan kolom Excel).
  const _letterCache = [];
  function idxToLetters(idx) {
    if (_letterCache[idx]) return _letterCache[idx];
    let n = idx + 1, out = "";
    while (n > 0) {
      const rem = (n - 1) % 26;
      out = String.fromCharCode(65 + rem) + out;
      n = Math.floor((n - 1) / 26);
    }
    return (_letterCache[idx] = out);
  }

  // ---------- Lebar kolom dinamis ----------
  // Kolom yang berisi data melebar mengikuti teks terpanjangnya (batas MIN..MAX_FILLED_W).
  // Kolom yang masih kosong tetap selebar standar (COL_W), seperti Excel.
  let charW = 0;
  function measureCharW() {
    if (charW) return charW;
    charW = 7.5; // cadangan untuk font monospace 12.5px
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      if (ctx) {
        const cs = getComputedStyle(dataTable);
        ctx.font = `${cs.fontSize || "12.5px"} ${cs.fontFamily || "monospace"}`;
        const w = ctx.measureText("0").width;
        if (w > 3 && w < 20) charW = w;
      }
    } catch (e) { /* pakai cadangan */ }
    return charW;
  }

  function computeColWidths(chars) {
    const cw = measureCharW();
    state.colW = (chars || []).map((n) =>
      n > 0 ? Math.min(MAX_FILLED_W, Math.max(MIN_FILLED_W, Math.ceil(n * cw) + 20)) : COL_W
    );
    state.colX = [0];
    state.colW.forEach((w, i) => state.colX.push(state.colX[i] + w));
  }

  const colW = (c) => (c < state.colW.length ? state.colW[c] : COL_W);
  const colLeft = (c) => {
    const n = state.colW.length;
    return c <= n ? state.colX[c] : state.colX[n] + (c - n) * COL_W;
  };
  // indeks kolom yang memuat posisi x (px, relatif awal kolom A)
  function colAt(x) {
    const n = state.colW.length;
    const end = state.colX[n];
    if (x >= end) return n + Math.floor((x - end) / COL_W);
    let lo = 0, hi = n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (state.colX[mid] <= x) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // ---------- Memuat data (per blok baris) ----------

  async function loadPreview() {
    const gen = ++state.gen;
    let data;
    try {
      const res = await fetch(api(`/preview?sheet=${encodeURIComponent(state.sheet)}&row_start=0&row_count=${BLOCK}&meta=1`));
      data = await res.json();
      if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    } catch (err) {
      if (gen === state.gen) alert(err.message);
      return;
    }
    if (gen !== state.gen) return;

    state.meta = data;
    state.blocks = new Map([[0, data.rows || []]]);
    state.loading = new Map();
    state.failed = new Set();
    state.sel = { rects: [], anchor: null };
    state.precedents.clear();
    stats.cache.clear();
    state.lastWin = null;

    // Awal: data + sedikit baris/kolom kosong (seperti Excel). Sisanya muncul saat user scroll.
    computeColWidths(data.col_chars);
    state.viewRows = Math.min(MAX_ROWS, Math.max(MIN_ROWS, data.total_rows + PAD_ROWS));
    state.viewCols = Math.min(MAX_COLS, Math.max(MIN_COLS, data.total_cols + PAD_COLS));
    tableWrap.scrollTop = 0;
    tableWrap.scrollLeft = 0;

    renderGrid(true);
    renderGridInfo();
    renderFormulaPanel();
    updateTabBadge();
    markRecapStale();
  }

  function renderGridInfo() {
    const d = state.meta;
    if (!d) return;
    pagerInfo.textContent = format(t("workspace.grid.info"), { rows: d.total_rows, cols: d.total_cols });
  }

  function fetchBlock(b) {
    if (state.loading.has(b)) return state.loading.get(b);
    const gen = state.gen;
    const p = fetch(
      api(`/preview?sheet=${encodeURIComponent(state.sheet)}&row_start=${b * BLOCK}&row_count=${BLOCK}&meta=0`),
      { noLoader: true }
    )
      .then((res) => res.json().then((d) => ({ ok: res.ok, d })))
      .then(({ ok, d }) => {
        if (gen !== state.gen) return;
        if (ok) state.blocks.set(b, d.rows || []);
        else state.failed.add(b);
      })
      .catch(() => { if (gen === state.gen) state.failed.add(b); })
      .finally(() => { if (gen === state.gen) state.loading.delete(b); });
    state.loading.set(b, p);
    return p;
  }

  // Pastikan blok yang memuat baris r0..r1 sudah ada. Resolve setelah semuanya selesai.
  function ensureBlocks(r0, r1) {
    const m = state.meta;
    if (!m || m.total_rows === 0) return Promise.resolve();
    const last = Math.min(r1, m.total_rows - 1);
    const jobs = [];
    for (let b = Math.floor(Math.max(0, r0) / BLOCK); b <= Math.floor(last / BLOCK); b++) {
      if (state.blocks.has(b) || state.failed.has(b)) continue;
      jobs.push(fetchBlock(b));
    }
    return Promise.all(jobs);
  }

  function rowsCached(r0, r1) {
    for (let b = Math.floor(r0 / BLOCK); b <= Math.floor(r1 / BLOCK); b++) {
      if (!state.blocks.has(b)) return false;
    }
    return true;
  }

  // Nilai sel (r, c) 0-based. null = kosong / di luar data. undefined = barisnya belum dimuat.
  function getVal(r, c) {
    const m = state.meta;
    if (!m || r < 0 || c < 0 || r >= m.total_rows || c >= m.total_cols) return null;
    const blk = state.blocks.get(Math.floor(r / BLOCK));
    if (!blk) return undefined;
    const row = blk[r % BLOCK];
    const v = row ? row[c] : null;
    return v === undefined ? null : v;
  }

  // ---------- Pilihan sel (daftar persegi panjang) ----------

  const rects = () => state.sel.rects;

  function normRect(r1, c1, r2, c2) {
    return { r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) };
  }

  // a dikurangi b -> maksimal 4 persegi panjang
  function subtractRect(a, b) {
    if (b.r1 > a.r2 || b.r2 < a.r1 || b.c1 > a.c2 || b.c2 < a.c1) return [a];
    const out = [];
    if (b.r1 > a.r1) out.push({ r1: a.r1, r2: b.r1 - 1, c1: a.c1, c2: a.c2 });
    if (b.r2 < a.r2) out.push({ r1: b.r2 + 1, r2: a.r2, c1: a.c1, c2: a.c2 });
    const mr1 = Math.max(a.r1, b.r1), mr2 = Math.min(a.r2, b.r2);
    if (b.c1 > a.c1) out.push({ r1: mr1, r2: mr2, c1: a.c1, c2: b.c1 - 1 });
    if (b.c2 < a.c2) out.push({ r1: mr1, r2: mr2, c1: b.c2 + 1, c2: a.c2 });
    return out;
  }

  const isSelected = (r, c) => rects().some((x) => r >= x.r1 && r <= x.r2 && c >= x.c1 && c <= x.c2);
  const rowHasSel = (r) => rects().some((x) => r >= x.r1 && r <= x.r2);
  const colHasSel = (c) => rects().some((x) => c >= x.c1 && c <= x.c2);
  const selCount = () => rects().reduce((n, x) => n + (x.r2 - x.r1 + 1) * (x.c2 - x.c1 + 1), 0);

  // Sel terpilih berurutan baris-demi-baris, maksimal `max` sel (untuk kartu di tab Rumus).
  function selCells(max) {
    const out = [];
    const sorted = [...rects()].sort((a, b) => a.r1 - b.r1 || a.c1 - b.c1);
    for (const x of sorted) {
      for (let r = x.r1; r <= x.r2; r++) {
        for (let c = x.c1; c <= x.c2; c++) {
          out.push([r, c]);
          if (out.length >= max) return out;
        }
      }
    }
    return out;
  }

  function applySelection(rect, e, { anchor, single }) {
    const S = state.sel;
    if (e.shiftKey && single && S.anchor) {
      // Shift+klik: seluruh persegi panjang dari anchor ke sel yang diklik
      S.rects = [normRect(S.anchor.r, S.anchor.c, anchor.r, anchor.c)];
    } else if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd+klik: tambah, atau lepas kalau seluruhnya sudah terpilih
      let rest = [rect];
      S.rects.forEach((s) => { rest = rest.flatMap((x) => subtractRect(x, s)); });
      const covered = rest.length === 0;
      S.rects = S.rects.flatMap((s) => subtractRect(s, rect));
      if (!covered) S.rects.push(rect);
      S.anchor = anchor;
    } else {
      S.rects = [rect];
      S.anchor = anchor;
    }
    state.precedents.clear();
    selectionChanged();
    maybeRevealFormulaTab();
  }

  function clearSelection() {
    state.sel = { rects: [], anchor: null };
    state.precedents.clear();
    selectionChanged();
  }

  function selectionChanged() {
    renderGrid(true);
    renderFormulaPanel();
    updateTabBadge();
  }

  // Auto-pindah ke tab Rumus hanya kalau ada sel terpilih di kolom berumus,
  // supaya user yang sedang asyik di tab Chat tidak "ditarik" tiap klik sel biasa.
  function maybeRevealFormulaTab() {
    const d = state.meta;
    if (!d || !selCount()) return;
    const fcols = d.formula_columns || {};
    if (!Object.keys(fcols).length) return;
    const hit = rects().some((x) => {
      for (let c = x.c1; c <= Math.min(x.c2, x.c1 + 300); c++) if (fcols[idxToLetters(c)]) return true;
      return false;
    });
    if (hit) setPanelTab("formula");
  }

  function updateTabBadge() {
    const formulaTab = panelTabs.querySelector('[data-tab="formula"]');
    let badge = formulaTab.querySelector(".panel-tab-count");
    const n = selCount();
    if (n) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "panel-tab-count";
        formulaTab.appendChild(badge);
      }
      badge.textContent = n;
    } else if (badge) {
      badge.remove();
    }
  }

  // ---------- Render grid virtual ----------

  function renderGrid(force) {
    const m = state.meta;
    if (!m) return;
    const vw = tableWrap.clientWidth || 800;
    const vh = tableWrap.clientHeight || 480;

    // Selalu sediakan cukup baris/kolom kosong supaya layar penuh dan tabel bisa di-scroll.
    state.viewRows = Math.max(state.viewRows, Math.min(MAX_ROWS, Math.ceil((vh - HEAD_H) / ROW_H) + 2));
    while (colLeft(state.viewCols) < vw - GUTTER_W + 2 * COL_W && state.viewCols < MAX_COLS) state.viewCols++;

    const sT = tableWrap.scrollTop, sL = tableWrap.scrollLeft;
    const r0 = Math.min(Math.max(0, Math.floor(sT / ROW_H) - OVERSCAN_ROWS), Math.max(0, state.viewRows - 1));
    const r1 = Math.min(state.viewRows, Math.ceil((sT + vh) / ROW_H) + OVERSCAN_ROWS);
    const c0 = Math.min(Math.max(0, colAt(sL) - OVERSCAN_COLS), Math.max(0, state.viewCols - 1));
    const c1 = Math.min(state.viewCols, colAt(sL + vw) + 1 + OVERSCAN_COLS);

    const key = [r0, r1, c0, c1, state.viewRows, state.viewCols].join();
    if (!force && key === state.lastWin) return;
    state.lastWin = key;

    const fcols = m.formula_columns || {};
    const leftSpacer = c0 > 0;
    const rightSpacer = c1 < state.viewCols;
    const nCols = 1 + (leftSpacer ? 1 : 0) + (c1 - c0) + (rightSpacer ? 1 : 0);

    // colgroup: lebar tetap -> tata letak tabel stabil walau hanya sebagian kolom yang dirender
    let cg = `<col style="width:${GUTTER_W}px">`;
    if (leftSpacer) cg += `<col style="width:${colLeft(c0)}px">`;
    for (let c = c0; c < c1; c++) cg += `<col style="width:${colW(c)}px">`;
    if (rightSpacer) cg += `<col style="width:${colLeft(state.viewCols) - colLeft(c1)}px">`;
    tableCols.innerHTML = cg;
    dataTable.style.width = GUTTER_W + colLeft(state.viewCols) + "px";

    // Baris huruf kolom (A, B, C...). Corner di kiri atas = pilih semua.
    let head = `<th class="row-gutter corner" data-corner="1"></th>`;
    if (leftSpacer) head += `<th class="spacer"></th>`;
    for (let c = c0; c < c1; c++) {
      const letter = idxToLetters(c);
      const tag = fcols[letter] ? `<span class="col-formula-tag">ƒx</span>` : "";
      head += `<th class="col-letter${colHasSel(c) ? " gutter-active" : ""}" data-col="${c}">${letter}${tag}</th>`;
    }
    if (rightSpacer) head += `<th class="spacer"></th>`;
    tableLetters.innerHTML = head;

    // Baris data + baris kosong
    const top = r0 * ROW_H;
    const bottom = (state.viewRows - r1) * ROW_H;
    const html = [];
    if (top > 0) html.push(`<tr class="spacer-row"><td class="spacer" colspan="${nCols}" style="height:${top}px"></td></tr>`);
    for (let r = r0; r < r1; r++) {
      let tr = `<tr><td class="row-gutter${rowHasSel(r) ? " gutter-active" : ""}" data-row="${r}">${r + 1}</td>`;
      if (leftSpacer) tr += `<td class="spacer"></td>`;
      for (let c = c0; c < c1; c++) {
        const v = getVal(r, c);
        const ck = r + "," + c;
        let cls = "";
        if (isSelected(r, c)) cls += " cell-selected";
        if (state.precedents.has(ck)) cls += " cell-precedent";
        tr += `<td${cls ? ` class="${cls.trim()}"` : ""} data-row="${r}" data-col="${c}">${
          v === null || v === undefined ? "" : escapeHtml(String(v))
        }</td>`;
      }
      if (rightSpacer) tr += `<td class="spacer"></td>`;
      html.push(tr + "</tr>");
    }
    if (bottom > 0) html.push(`<tr class="spacer-row"><td class="spacer" colspan="${nCols}" style="height:${bottom}px"></td></tr>`);
    tableBody.innerHTML = html.join("");

    // Baris data yang belum dimuat -> minta ke server, lalu gambar ulang
    if (m.total_rows > 0 && r0 < m.total_rows) {
      const lastData = Math.min(r1 - 1, m.total_rows - 1);
      if (!rowsCached(r0, lastData)) {
        const gen = state.gen;
        ensureBlocks(r0, lastData).then(() => { if (gen === state.gen) renderGrid(true); });
      }
    }
  }

  // Tambah baris/kolom kosong saat user scroll mendekati tepi bawah/kanan.
  function growIfNeeded() {
    const vw = tableWrap.clientWidth || 800;
    const vh = tableWrap.clientHeight || 480;
    let changed = false;
    const needH = HEAD_H + state.viewRows * ROW_H;
    if (tableWrap.scrollTop + vh >= needH - 3 * ROW_H && state.viewRows < MAX_ROWS) {
      state.viewRows = Math.min(MAX_ROWS, state.viewRows + GROW_ROWS);
      changed = true;
    }
    const needW = GUTTER_W + colLeft(state.viewCols);
    if (tableWrap.scrollLeft + vw >= needW - 2 * COL_W && state.viewCols < MAX_COLS) {
      state.viewCols = Math.min(MAX_COLS, state.viewCols + GROW_COLS);
      changed = true;
    }
    return changed;
  }

  let scrollRaf = 0;
  tableWrap.addEventListener("scroll", () => {
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      growIfNeeded();
      renderGrid(false);
    });
  }, { passive: true });

  // Gulir supaya sel (r, c) kelihatan (dipakai saat klik chip "bahan rumus").
  function scrollToCell(r, c) {
    const vw = tableWrap.clientWidth || 800;
    const vh = tableWrap.clientHeight || 480;
    state.viewRows = Math.max(state.viewRows, Math.min(MAX_ROWS, r + PAD_ROWS));
    state.viewCols = Math.max(state.viewCols, Math.min(MAX_COLS, c + PAD_COLS));
    let sL = tableWrap.scrollLeft, sT = tableWrap.scrollTop;
    const x0 = colLeft(c), x1 = x0 + colW(c), y0 = r * ROW_H, y1 = y0 + ROW_H;
    if (x0 < sL) sL = x0;
    else if (x1 > sL + vw - GUTTER_W) sL = x1 - (vw - GUTTER_W);
    if (y0 < sT) sT = y0;
    else if (y1 > sT + vh - HEAD_H) sT = y1 - (vh - HEAD_H);
    renderGrid(true);
    tableWrap.scrollLeft = Math.max(0, sL);
    tableWrap.scrollTop = Math.max(0, sT);
    renderGrid(true);
  }

  // ---------- Klik tabel ----------

  const lastRowIdx = () => Math.max(0, (state.meta ? state.meta.total_rows : 0) - 1);
  const lastColIdx = () => Math.max(0, (state.meta ? state.meta.total_cols : 0) - 1);

  tableLetters.addEventListener("click", (e) => {
    if (!state.meta) return;
    if (e.target.closest("th.corner")) {
      // Corner kiri atas: pilih seluruh area data
      applySelection(normRect(0, 0, lastRowIdx(), lastColIdx()), e, { anchor: { r: 0, c: 0 }, single: false });
      return;
    }
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const c = Number(th.dataset.col);
    // Klik huruf kolom = pilih kolom itu sepanjang data
    applySelection(normRect(0, c, lastRowIdx(), c), e, { anchor: { r: 0, c }, single: false });
  });

  tableBody.addEventListener("click", (e) => {
    if (!state.meta) return;
    const gutter = e.target.closest("td.row-gutter");
    if (gutter) {
      const r = Number(gutter.dataset.row);
      applySelection(normRect(r, 0, r, lastColIdx()), e, { anchor: { r, c: 0 }, single: false });
      return;
    }
    const cell = e.target.closest("td[data-col]");
    if (!cell) return;
    const r = Number(cell.dataset.row);
    const c = Number(cell.dataset.col);
    applySelection(normRect(r, c, r, c), e, { anchor: { r, c }, single: true });
  });

  // Listener global dibatalkan otomatis oleh router saat user pindah page (tanpa reload)
  const pageSignal = window.MejaRouter ? window.MejaRouter.signal : undefined;

  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (e.key === "Escape" && !/INPUT|TEXTAREA|SELECT/.test(tag) && selCount()) clearSelection();
  }, { signal: pageSignal });

  window.addEventListener("resize", () => renderGrid(true), { signal: pageSignal });

  // ---------- Isi tab Rumus ----------

  function fmtVal(v) {
    return v === null || v === undefined || v === "" ? "–" : escapeHtml(String(v));
  }

  function lettersToNum(letters) {
    let n = 0;
    for (const ch of String(letters).toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
    return n;
  }

  // Sel "bahan rumus" (dep.rows 1-based = nomor baris Excel, dep.cols = huruf kolom) -> key "r,c"
  function precedentKeys(dep) {
    if (!dep.rows || !dep.cols.length) return [];
    const keys = [];
    for (let row = dep.rows[0]; row <= dep.rows[1]; row++) {
      dep.cols.forEach((letter) => keys.push(`${row - 1},${lettersToNum(letter) - 1}`));
      if (keys.length > 5000) break;
    }
    return keys;
  }

  function renderDeps(entry) {
    if (!entry.deps || !entry.deps.length) return "";
    const chips = entry.deps
      .map((dep) => {
        const rowsLabel = !dep.rows
          ? t("workspace.formula.depsOutside")
          : dep.rows[0] === dep.rows[1]
          ? format(t("workspace.formula.depsRow"), { r: dep.rows[0] })
          : format(t("workspace.formula.depsRows"), { a: dep.rows[0], b: dep.rows[1] });
        const name = dep.cols.length ? dep.cols.map(escapeHtml).join(", ") : "?";
        const keys = precedentKeys(dep);
        const attr = keys.length ? ` data-keys="${keys.join(";")}"` : " disabled";
        return (
          `<button type="button" class="fp-dep"${attr}>` +
          `<span class="fp-dep-ref">${escapeHtml(dep.ref)}</span>` +
          `<span class="fp-dep-name">${name}</span>` +
          `<span class="fp-dep-rows">${rowsLabel}</span></button>`
        );
      })
      .join("");
    return (
      `<div class="fp-deps-title">${t("workspace.formula.deps")}</div><div class="fp-deps">${chips}</div>` +
      (entry.x ? `<div class="fp-note">${t("workspace.formula.otherSheet")}</div>` : "")
    );
  }

  function renderFormulaPanel() {
    const d = state.meta;
    if (!d || !selCount()) {
      cancelSuggest();
      stats.token++;
      formulaPanelEmpty.hidden = false;
      formulaPanelContent.hidden = true;
      formulaPanelContent.innerHTML = "";
      return;
    }
    formulaPanelEmpty.hidden = true;
    formulaPanelContent.hidden = false;

    const fCells = d.formula_cells || {};
    const fCols = d.formula_columns || {};
    const dirty = !!d.row_order_dirty;
    const cellEntry = (r, letter) => {
      const raw = fCells[String(r)] && fCells[String(r)][letter];
      return raw ? (typeof raw === "string" ? { f: raw, deps: [], x: false } : raw) : null;
    };

    // Jumlah sel berumus dalam pilihan (peta rumus jarang & hanya mencakup baris yang discan saat upload)
    let formulaCount = 0;
    Object.keys(fCells).forEach((rk) => {
      Object.keys(fCells[rk]).forEach((letter) => {
        if (isSelected(Number(rk), lettersToNum(letter) - 1)) formulaCount++;
      });
    });

    const MAX_SHOWN = 120;
    const total = selCount();
    const cells = selCells(MAX_SHOWN);
    const cards = cells.map(([r, c]) => {
      const letter = idxToLetters(c);
      const val = getVal(r, c);
      const entry = cellEntry(r, letter);
      const info = fCols[letter];
      let badge, body;

      if (entry) {
        badge = `<span class="fp-badge fp-badge-formula">${t("workspace.formula.badgeFormula")}</span>`;
        body =
          `<code class="fp-formula">${escapeHtml(entry.f)}</code>` +
          `<div class="fp-value">${t("workspace.formula.result")}: <strong>${fmtVal(val)}</strong></div>` +
          renderDeps(entry);
      } else if (dirty && info) {
        badge = `<span class="fp-badge fp-badge-unknown">${t("workspace.formula.badgeUnknown")}</span>`;
        body =
          `<div class="fp-note">${format(t("workspace.formula.dirtyNote"), {
            col: `<strong>${letter}</strong>`,
            pct: Math.round((info.ratio || 0) * 100),
            sample: `<code>${escapeHtml(info.sample || "")}</code>`,
          })}</div>` +
          `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      } else if (info) {
        badge = `<span class="fp-badge fp-badge-static">${t("workspace.formula.badgeStatic")}</span>`;
        body =
          `<div class="fp-note">${format(t("workspace.formula.staticNote"), { col: `<strong>${letter}</strong>` })}</div>` +
          `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      } else {
        badge = `<span class="fp-badge fp-badge-plain">${t("workspace.formula.badgePlain")}</span>`;
        body = `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      }

      // Kepala kartu = alamat sel seperti di Excel (mis. B4), tanpa "nama kolom"
      return (
        `<div class="fp-cell"><div class="fp-cell-head">` +
        `<span class="fp-letter">${letter}${r + 1}</span>${badge}</div>${body}</div>`
      );
    });

    const noFormulaNote =
      !Object.keys(fCols).length && !dirty
        ? `<div class="fp-note fp-note-top">${t("workspace.formula.noneInSheet")}</div>`
        : "";
    const more =
      total > MAX_SHOWN
        ? `<p class="fp-more">${format(t("workspace.formula.more"), { n: total - MAX_SHOWN })}</p>`
        : "";

    formulaPanelContent.innerHTML =
      `<div class="fp-summary"><span>${format(t("workspace.formula.selected"), { n: total, f: formulaCount })}</span>` +
      (dirty ? `<span class="fp-dirty-flag">${t("workspace.formula.dirtyFlag")}</span>` : "") +
      `</div><div class="fs-stats" id="formulaStats" role="status"></div><div class="fs-wrap" id="formulaSuggest"></div>${noFormulaNote}${cards.join("")}${more}`;

    updateStats();
    scheduleSuggestions();
  }

  // ---------- Statistik pilihan ala status bar Excel (Rata-rata · Hitung · Jumlah) ----------
  // Bekerja untuk pilihan bentuk apa pun, termasuk Ctrl+klik acak. Seperti Excel: hanya angka
  // asli yang dijumlahkan (teks "100" tidak), dan "Hitung" menghitung semua sel yang terisi.
  // Bagian pilihan yang barisnya belum dimuat di browser dihitung server lewat /column-stats
  // (atas SEMUA baris di rentang itu), jadi memilih seluruh kolom tetap akurat.
  const stats = { token: 0, cache: new Map() };

  const isFilled = (v) => !(v === null || v === undefined || (typeof v === "string" && v.trim() === ""));
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const newStats = () => ({ count: 0, numeric: 0, sum: 0, min: null, max: null });

  function addCell(st, v) {
    if (!isFilled(v)) return;
    st.count++;
    if (!isNum(v)) return;
    st.numeric++;
    st.sum += v;
    if (st.min === null || v < st.min) st.min = v;
    if (st.max === null || v > st.max) st.max = v;
  }

  function mergeStats(st, o) {
    st.count += o.count;
    st.numeric += o.numeric;
    st.sum += o.sum;
    if (o.min !== null && (st.min === null || o.min < st.min)) st.min = o.min;
    if (o.max !== null && (st.max === null || o.max > st.max)) st.max = o.max;
  }

  function fmtStat(n) {
    const locale = currentLang() === "en" ? "en-US" : "id-ID";
    return new Intl.NumberFormat(locale, { maximumFractionDigits: 9, useGrouping: false }).format(
      Object.is(n, -0) ? 0 : n
    );
  }

  function renderStats(box, st, note) {
    if (!st.count) {
      box.innerHTML = "";
      return;
    }
    const item = (key, val) =>
      `<span class="fs-stat"><span class="fs-stat-k">${t("workspace.stats." + key)}</span><strong>${val}</strong></span>`;
    let main = "";
    if (st.numeric) main += item("average", fmtStat(st.sum / st.numeric));
    main += item("count", fmtStat(st.count));
    if (st.numeric) main += item("sum", fmtStat(st.sum));
    const extra = st.numeric
      ? item("numCount", fmtStat(st.numeric)) + item("min", fmtStat(st.min)) + item("max", fmtStat(st.max))
      : "";
    box.innerHTML =
      `<div class="fs-stats-box"><div class="fs-stats-main">${main}</div>` +
      (extra ? `<div class="fs-stats-extra">${extra}</div>` : "") +
      `</div>` +
      (note ? `<div class="fp-note">${note}</div>` : "");
  }

  const LOCAL_STATS_MAX_CELLS = 200000;

  async function updateStats() {
    const box = document.getElementById("formulaStats");
    const d = state.meta;
    const token = ++stats.token;
    if (!box || !d) return;
    // Seperti Excel: satu sel saja tidak memunculkan statistik (nilainya sudah tampil di kartu sel).
    if (selCount() < 2) {
      box.innerHTML = "";
      return;
    }

    const total = newStats();
    const remote = []; // bagian pilihan yang dihitung server: {r1, r2, letters[]}
    rects().forEach((x) => {
      // Sel di luar area data pasti kosong -> tidak ikut dihitung
      const r2 = Math.min(x.r2, d.total_rows - 1);
      const c2 = Math.min(x.c2, d.total_cols - 1);
      if (r2 < x.r1 || c2 < x.c1) return;
      const area = (r2 - x.r1 + 1) * (c2 - x.c1 + 1);
      if (rowsCached(x.r1, r2) && area <= LOCAL_STATS_MAX_CELLS) {
        for (let r = x.r1; r <= r2; r++) for (let c = x.c1; c <= c2; c++) addCell(total, getVal(r, c));
      } else {
        const letters = [];
        for (let c = x.c1; c <= c2; c++) letters.push(idxToLetters(c));
        remote.push({ r1: x.r1, r2, letters });
      }
    });

    if (!remote.length) {
      renderStats(box, total);
      return;
    }

    box.innerHTML = `<div class="fp-note">${t("workspace.stats.loading")}</div>`;
    try {
      for (const part of remote) {
        const missing = part.letters.filter((l) => !stats.cache.has(`${l}:${part.r1}:${part.r2}`));
        for (let i = 0; i < missing.length; i += 200) {
          const chunk = missing.slice(i, i + 200);
          const res = await fetch(api("/column-stats"), {
            method: "POST",
            noLoader: true,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sheet: state.sheet, columns: chunk, row_start: part.r1, row_end: part.r2 }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
          chunk.forEach((l) => stats.cache.set(`${l}:${part.r1}:${part.r2}`, data.stats[l]));
        }
        part.letters.forEach((l) => mergeStats(total, stats.cache.get(`${l}:${part.r1}:${part.r2}`)));
      }
    } catch (err) {
      if (token !== stats.token || !box.isConnected) return;
      const partial = newStats();
      rects().forEach((x) => {
        for (let r = x.r1; r <= Math.min(x.r2, x.r1 + 2000); r++) {
          for (let c = x.c1; c <= Math.min(x.c2, x.c1 + 200); c++) {
            const v = getVal(r, c);
            if (v !== undefined) addCell(partial, v);
          }
        }
      });
      renderStats(box, partial, t("workspace.stats.error"));
      return;
    }
    if (token !== stats.token || !box.isConnected) return;
    renderStats(box, total, format(t("workspace.stats.fullNote"), { n: d.total_rows }));
  }

  // ---------- Saran rumus untuk sel yang dipilih (bagian atas tab Rumus) ----------
  // Pilihan dikirim ke /api/formula-suggest; server menghitung rumus yang cocok beserta hasilnya.
  // Nilai diambil dari data mentah (cache blok), bukan teks di DOM, jadi spasi dan desimal tetap utuh.
  const suggest = { timer: null, ctrl: null, token: 0, formulas: [] };
  const SEP_KEY = "meja-data-formula-sep";
  const SUGGEST_MAX_CELLS = 20000; // sama dengan MAX_CELLS di server

  function currentLang() {
    return window.MejaDataI18n ? window.MejaDataI18n.getLang() : "id";
  }

  // Excel berlokal Indonesia memakai ";" sebagai pemisah argumen, Excel Inggris memakai ",".
  // Default mengikuti bahasa antarmuka; user bisa menggantinya lewat pilihan di panel.
  function formulaSep() {
    const saved = localStorage.getItem(SEP_KEY);
    return saved === ";" || saved === "," ? saved : currentLang() === "id" ? ";" : ",";
  }

  // Pilihan harus satu persegi panjang utuh (bukan Ctrl+klik acak). Kolom otomatis berurutan
  // karena kolom = huruf asli sheet, jadi alamat seperti B2:C9 selalu benar.
  async function suggestRequest(d) {
    const rs = rects();
    if (rs.length !== 1) return { error: "workspace.suggest.notRect" };
    const x = rs[0];
    const area = (x.r2 - x.r1 + 1) * (x.c2 - x.c1 + 1);
    if (area > SUGGEST_MAX_CELLS) return { error: "workspace.suggest.tooBig" };
    await ensureBlocks(x.r1, Math.min(x.r2, d.total_rows - 1));
    const rows = [];
    let anyFilled = false;
    for (let r = x.r1; r <= x.r2; r++) {
      const line = [];
      for (let c = x.c1; c <= x.c2; c++) {
        const v = getVal(r, c);
        if (isFilled(v)) anyFilled = true;
        line.push(v === undefined ? null : v);
      }
      rows.push(line);
    }
    if (!anyFilled) return { empty: true };
    return {
      body: {
        rows,
        start_row: x.r1 + 1, // nomor baris Excel (1-based) = angka di gutter
        start_col: x.c1 + 1, // nomor kolom Excel (A = 1)
        sep: formulaSep(),
        lang: currentLang(),
      },
    };
  }

  function cancelSuggest() {
    clearTimeout(suggest.timer);
    if (suggest.ctrl) suggest.ctrl.abort();
    suggest.token++;
  }

  function renderSuggestions(wrap, data) {
    const d = state.meta;
    const items = data.suggestions || [];
    const sel = data.selection || {};
    suggest.formulas = items.map((x) => x.formula);

    const notes = [];
    if (d && d.row_order_dirty) notes.push(t("workspace.suggest.dirty"));
    const sep = formulaSep();

    let html =
      `<div class="fs-head"><span>${t("workspace.suggest.title")}</span>` +
      `<span class="fs-ref">${escapeHtml(sel.ref || "")}</span>` +
      `<label class="fs-sep">${t("workspace.suggest.sep")} ` +
      `<select class="fs-sep-select"><option value=","${sep === "," ? " selected" : ""}>,</option>` +
      `<option value=";"${sep === ";" ? " selected" : ""}>;</option></select></label></div>` +
      (notes.length ? `<div class="fp-note fs-note">${notes.join(" ")}</div>` : "");

    let lastCat = null;
    items.forEach((x, i) => {
      if (x.category !== lastCat) {
        html += `<div class="fs-cat">${escapeHtml(x.category)}</div>`;
        lastCat = x.category;
      }
      let out;
      if (x.kind === "single") {
        out = `<div class="fp-value">${t("workspace.formula.result")}: <strong>${fmtVal(x.result)}</strong></div>`;
      } else {
        out =
          `<table class="fs-prev"><caption>${format(t("workspace.suggest.preview"), { n: x.preview.length })}</caption>` +
          x.preview
            .map((p) => `<tr><td>${escapeHtml(p.input)}</td><td>→</td><td>${escapeHtml(p.output)}</td></tr>`)
            .join("") +
          `</table>`;
      }
      html +=
        `<div class="fp-cell fs-card"><div class="fs-top">` +
        `<code class="fp-formula">${escapeHtml(x.formula)}</code>` +
        `<button type="button" class="btn fs-copy" data-i="${i}">${t("workspace.suggest.copy")}</button></div>` +
        `<div class="fp-note fs-desc">${escapeHtml(x.description)}</div>${out}</div>`;
    });
    wrap.innerHTML = html;
  }

  function scheduleSuggestions() {
    cancelSuggest();
    const wrap = document.getElementById("formulaSuggest");
    const d = state.meta;
    if (!wrap || !d) return;
    const token = suggest.token;
    wrap.innerHTML = `<div class="fp-note">${t("workspace.suggest.loading")}</div>`;
    suggest.timer = setTimeout(async () => {
      const req = await suggestRequest(d);
      if (token !== suggest.token || !wrap.isConnected) return;
      if (req.empty) {
        wrap.innerHTML = "";
        return;
      }
      if (req.error) {
        wrap.innerHTML = `<div class="fp-note fp-note-top">${t(req.error)}</div>`;
        return;
      }
      const ctrl = (suggest.ctrl = new AbortController());
      try {
        const res = await fetch("/api/formula-suggest", {
          method: "POST",
          noLoader: true, // jangan munculkan preloader global tiap klik sel
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req.body),
          signal: ctrl.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
        if (token !== suggest.token || !wrap.isConnected) return;
        renderSuggestions(wrap, data);
      } catch (err) {
        if (err.name === "AbortError" || token !== suggest.token || !wrap.isConnected) return;
        wrap.innerHTML = `<div class="fp-note fp-note-top">${t("workspace.suggest.error")}: ${escapeHtml(err.message)}</div>`;
      }
    }, 200);
  }

  function fallbackCopy(text, done) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* abaikan */ }
    ta.remove();
    done();
  }

  formulaPanelContent.addEventListener("click", (e) => {
    const btn = e.target.closest(".fs-copy");
    if (!btn) return;
    const text = suggest.formulas[Number(btn.dataset.i)];
    if (text === undefined) return;
    const done = () => {
      btn.textContent = t("workspace.suggest.copied");
      setTimeout(() => { btn.textContent = t("workspace.suggest.copy"); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  });

  formulaPanelContent.addEventListener("change", (e) => {
    const sel = e.target.closest(".fs-sep-select");
    if (!sel) return;
    localStorage.setItem(SEP_KEY, sel.value);
    scheduleSuggestions();
  });

  // Klik chip "bahan rumus" -> tandai sel sumbernya di tabel (tanpa mengubah pilihan)
  formulaPanelContent.addEventListener("click", (e) => {
    const chip = e.target.closest(".fp-dep[data-keys]");
    if (!chip) return;
    const keys = chip.dataset.keys.split(";");
    const same = keys.length === state.precedents.size && keys.every((k) => state.precedents.has(k));
    state.precedents.clear();
    if (!same) keys.forEach((k) => state.precedents.add(k));
    formulaPanelContent.querySelectorAll(".fp-dep").forEach((el) => {
      el.classList.toggle("active", !same && el === chip);
    });
    renderGrid(true);
    if (!same) {
      const [r, c] = keys[0].split(",").map(Number);
      scrollToCell(r, c);
    }
  });

  // ---------- Tab Rekap: analisa otomatis sheet (duplikat, format, error, statistik) ----------
  // Semua angka dihitung server dari data; di sini hanya ditampilkan. Klik "Lihat sel" pada sebuah
  // temuan -> sel bermasalahnya dipilih & tabel digulir ke sana.
  const recap = { data: null, stale: true, loading: false, token: 0, reg: {} };

  function recapActive() { return !tabViewRecap.hidden; }

  async function loadRecap() {
    if (!state.sheet) return;
    const token = ++recap.token;
    recap.stale = false;
    recapPanel.innerHTML = `<p class="formula-panel-empty">${t("workspace.recap.loading")}</p>`;
    try {
      const res = await fetch(api(`/recap?sheet=${encodeURIComponent(state.sheet)}&lang=${currentLang()}`), { noLoader: true });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
      if (token !== recap.token) return;
      recap.data = data;
      renderRecap(data);
    } catch (err) {
      if (token !== recap.token) return;
      recap.stale = true;
      recapPanel.innerHTML = `<p class="formula-panel-empty">${t("workspace.recap.error")}: ${escapeHtml(err.message)}</p>`;
    }
  }

  function markRecapStale() {
    recap.stale = true;
    if (recapActive()) loadRecap();
  }

  function recapNum(n) {
    return new Intl.NumberFormat(currentLang() === "en" ? "en-US" : "id-ID", { maximumFractionDigits: 2 }).format(n);
  }

  // ---------- Grafik & tabel ringkasan ala Excel (SVG murni, tanpa library) ----------
  const PALETTE = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47", "#264478", "#9E480E"];

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  function compactNum(n) {
    const a = Math.abs(n);
    if (a >= 1e9) return +(n / 1e9).toFixed(1) + "M";
    if (a >= 1e6) return +(n / 1e6).toFixed(1) + "jt";
    if (a >= 1e3) return +(n / 1e3).toFixed(1) + "rb";
    return String(+n.toFixed(2));
  }

  const clip = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));

  // Grafik kolom (vertikal). items: [{label, value, hit?}]. opts.edges = hanya label tepi (histogram).
  function columnChart(id, items, opts = {}) {
    const W = 360, H = 190, ml = 40, mr = 8, mt = 10, mb = opts.edges ? 22 : 38;
    const pw = W - ml - mr, ph = H - mt - mb;
    const max = niceMax(Math.max(...items.map((x) => x.value), 1));
    const bw = pw / items.length;
    const gap = opts.gap === undefined ? 0.3 : opts.gap;
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const y = mt + ph - (ph * i) / 4;
      g += `<line x1="${ml}" x2="${W - mr}" y1="${y}" y2="${y}" class="rc-grid"/>` +
        `<text x="${ml - 4}" y="${y + 3}" text-anchor="end" class="rc-axis">${compactNum((max * i) / 4)}</text>`;
    }
    items.forEach((x, i) => {
      const h = (ph * x.value) / max, bx = ml + i * bw + (bw * gap) / 2, w = bw * (1 - gap);
      const hit = x.hit ? ` class="rc-hit" data-chart="${id}" data-i="${i}"` : "";
      g += `<rect${hit} x="${bx}" y="${mt + ph - h}" width="${Math.max(w, 1)}" height="${h}" fill="${x.color || PALETTE[0]}"><title>${escapeHtml(x.label)}: ${recapNum(x.value)}</title></rect>`;
      if (!opts.edges) {
        const cx = bx + w / 2, cy = mt + ph + 8;
        g += `<text x="${cx}" y="${cy}" text-anchor="end" transform="rotate(-30 ${cx} ${cy})" class="rc-axis">${escapeHtml(clip(x.label, 9))}</text>`;
      }
    });
    if (opts.edges) {
      g += `<text x="${ml}" y="${H - 6}" class="rc-axis">${escapeHtml(items[0].label)}</text>` +
        `<text x="${W - mr}" y="${H - 6}" text-anchor="end" class="rc-axis">${escapeHtml(items[items.length - 1].labelEnd || "")}</text>`;
    }
    g += `<line x1="${ml}" x2="${W - mr}" y1="${mt + ph}" y2="${mt + ph}" class="rc-axisline"/>`;
    return `<svg class="rc-svg" viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`;
  }

  // Pie / donut + legenda
  function pieChart(id, items, opts = {}) {
    const total = items.reduce((n, x) => n + x.value, 0) || 1;
    const big = !!opts.big; // varian besar untuk slide
    const cx = big ? 120 : 80, cy = big ? 120 : 80, R = big ? 108 : 72, r = big ? 60 : 40;
    let a0 = -Math.PI / 2, paths = "", legend = "";
    items.forEach((x, i) => {
      const frac = x.value / total, a1 = a0 + frac * 2 * Math.PI, large = frac > 0.5 ? 1 : 0;
      const col = PALETTE[i % PALETTE.length];
      const pt = (rad, a) => `${cx + rad * Math.cos(a)} ${cy + rad * Math.sin(a)}`;
      const d = frac >= 0.9999
        ? `M ${cx - R} ${cy} A ${R} ${R} 0 1 1 ${cx + R} ${cy} A ${R} ${R} 0 1 1 ${cx - R} ${cy} M ${cx - r} ${cy} A ${r} ${r} 0 1 0 ${cx + r} ${cy} A ${r} ${r} 0 1 0 ${cx - r} ${cy}`
        : `M ${pt(R, a0)} A ${R} ${R} 0 ${large} 1 ${pt(R, a1)} L ${pt(r, a1)} A ${r} ${r} 0 ${large} 0 ${pt(r, a0)} Z`;
      const hit = x.hit ? ` class="rc-hit" data-chart="${id}" data-i="${i}"` : "";
      paths += `<path${hit} d="${d}" fill="${col}" fill-rule="evenodd"><title>${escapeHtml(x.label)}: ${recapNum(x.value)}</title></path>`;
      legend += big
        ? `<g transform="translate(256 ${34 + i * 28})"><rect width="14" height="14" y="-12" rx="3" fill="${col}"/>` +
          `<text x="22" y="0" class="rc-axis rc-legend-t" style="font-size:15px">${escapeHtml(clip(x.label, 14))} · ${Math.round(frac * 100)}%</text></g>`
        : `<g transform="translate(168 ${14 + i * 17})"><rect width="10" height="10" y="-9" fill="${col}"/>` +
          `<text x="15" y="0" class="rc-axis rc-legend-t">${escapeHtml(clip(x.label, 16))} · ${Math.round(frac * 100)}%</text></g>`;
      a0 = a1;
    });
    const H = big ? Math.max(240, 48 + items.length * 28) : Math.max(160, 24 + items.length * 17);
    return `<svg class="rc-svg" viewBox="0 0 ${big ? 440 : 360} ${H}" role="img">${paths}${legend}</svg>`;
  }

  // Grafik garis (tren). points: [{label, values:[...]}], names: nama tiap seri
  function lineChart(points, names) {
    const W = 360, H = 200, ml = 44, mr = 10, mt = 22, mb = 24;
    const pw = W - ml - mr, ph = H - mt - mb;
    const max = niceMax(Math.max(...points.flatMap((p) => p.values), 1));
    const X = (i) => ml + (pw * i) / Math.max(points.length - 1, 1);
    const Y = (v) => mt + ph - (ph * v) / max;
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const y = mt + ph - (ph * i) / 4;
      g += `<line x1="${ml}" x2="${W - mr}" y1="${y}" y2="${y}" class="rc-grid"/>` +
        `<text x="${ml - 4}" y="${y + 3}" text-anchor="end" class="rc-axis">${compactNum((max * i) / 4)}</text>`;
    }
    names.forEach((nm, s) => {
      const col = PALETTE[s % PALETTE.length];
      g += `<polyline fill="none" stroke="${col}" stroke-width="2" points="${points.map((p, i) => `${X(i)},${Y(p.values[s])}`).join(" ")}"/>`;
      points.forEach((p, i) => {
        g += `<circle cx="${X(i)}" cy="${Y(p.values[s])}" r="2.6" fill="${col}"><title>${escapeHtml(p.label)} · ${escapeHtml(nm)}: ${recapNum(p.values[s])}</title></circle>`;
      });
      g += `<g transform="translate(${ml + s * 90} 10)"><rect width="10" height="3" y="-3" fill="${col}"/><text x="14" y="1" class="rc-axis">${escapeHtml(nm)}</text></g>`;
    });
    const ticks = [0, Math.floor((points.length - 1) / 2), points.length - 1];
    [...new Set(ticks)].forEach((i) => {
      g += `<text x="${X(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}" class="rc-axis">${escapeHtml(points[i].label)}</text>`;
    });
    return `<svg class="rc-svg" viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`;
  }

  // Grafik kategori yang bisa diganti jenisnya (Kolom / Batang / Pie)
  function categoryItems(cat) {
    const items = cat.top.map((x) => ({ label: x.value, value: x.count, hit: x.targets.length > 0, targets: x.targets }));
    if (cat.others > 0) items.push({ label: t("workspace.recap.pivot.others"), value: cat.others, hit: false, targets: [] });
    return items;
  }

  function drawCategory(ci, type) {
    const cat = recap.data.categories[ci];
    const box = document.getElementById("rcc-" + ci);
    if (!cat || !box) return;
    const id = "cat" + ci, items = categoryItems(cat);
    recap.reg[id] = items;
    let body;
    if (type === "pie") body = pieChart(id, items);
    else if (type === "bar") {
      const max = Math.max(...items.map((x) => x.value), 1);
      body = `<div class="rc-bars">` + items.map((x, i) =>
        `<div class="rc-bar-row rc-bar-wide${x.hit ? " rc-hit" : ""}" data-chart="${id}" data-i="${i}"><span class="rc-bar-k">${escapeHtml(clip(x.label, 14))}</span>` +
        `<span class="rc-bar"><span class="rc-bar-fill" style="width:${(100 * x.value) / max}%;background:${PALETTE[i % PALETTE.length]}"></span></span>` +
        `<span class="rc-bar-v">${x.value}</span></div>`).join("") + `</div>`;
    } else body = columnChart(id, items.map((x, i) => ({ ...x, color: PALETTE[i % PALETTE.length] })));
    box.innerHTML = body;
    box.parentElement.querySelectorAll(".rc-toggle button").forEach((b) => b.classList.toggle("active", b.dataset.type === type));
  }

  function toggleHtml(ci) {
    return `<div class="rc-toggle">` + ["column", "bar", "pie"].map((ty) =>
      `<button type="button" data-ci="${ci}" data-type="${ty}">${t("workspace.recap.type." + ty)}</button>`).join("") + `</div>`;
  }

  // Tabel ringkasan ala PivotTable: data bars (Hitung & Jumlah) + skala warna (Rata-rata).
  // Kolom nilai/skor/persentase hanya menampilkan Rata-rata (menjumlahkan IPK tidak bermakna).
  function pivotHtml(pv, pi) {
    const bar = (v, max, col) => {
      const w = max > 0 ? Math.max(0, Math.min(100, (100 * v) / max)) : 0;
      return `background:linear-gradient(90deg,${col} ${w}%,transparent ${w}%)`;
    };
    const avgOnly = (m) => m.role === "score" || m.role === "percent";
    const rows = pv.rows;
    const maxCount = Math.max(...rows.map((r) => r.count), 1);
    const maxSum = pv.measures.map((_, j) => Math.max(...rows.map((r) => r.m[j].sum), 1));
    const avgs = pv.measures.map((_, j) => rows.map((r) => r.m[j].avg).filter((v) => v !== null));
    const minAvg = avgs.map((a) => (a.length ? Math.min(...a) : 0));
    const maxAvg = avgs.map((a) => (a.length ? Math.max(...a) : 0));
    let h = `<tr><th>${escapeHtml(pv.col)}</th><th>${t("workspace.recap.pivot.count")}</th>` +
      pv.measures.map((m) =>
        (avgOnly(m) ? "" : `<th>${t("workspace.recap.pivot.sum")} ${escapeHtml(m.col)}</th>`) +
        `<th>${t("workspace.recap.pivot.avg")} ${escapeHtml(m.col)}</th>`).join("") + `</tr>`;
    const avgCell = (a, j) => {
      const span = maxAvg[j] - minAvg[j];
      const alpha = a === null ? 0 : 0.08 + (span > 0 ? (0.42 * (a - minAvg[j])) / span : 0.2);
      return `<td style="background:rgba(237,125,49,${alpha.toFixed(2)})">${a === null ? "–" : recapNum(a)}</td>`;
    };
    rows.forEach((r) => {
      const hit = r.src ? ` class="rc-hit" data-pivot="${r.src.join(",")}"` : "";
      h += `<tr${hit}><td class="rc-pk">${escapeHtml(r.label === null ? t("workspace.recap.pivot.others") : r.label)}</td>` +
        `<td style="${bar(r.count, maxCount, "rgba(68,114,196,.35)")}">${recapNum(r.count)}</td>` +
        r.m.map((m, j) =>
          (avgOnly(pv.measures[j]) ? "" : `<td style="${bar(m.sum, maxSum[j], "rgba(112,173,71,.35)")}">${recapNum(m.sum)}</td>`) +
          avgCell(m.avg, j)).join("") + `</tr>`;
    });
    const tot = pv.total;
    h += `<tr class="rc-total"><td>${t("workspace.recap.pivot.total")}</td><td>${recapNum(tot.count)}</td>` +
      tot.m.map((m, j) => (avgOnly(pv.measures[j]) ? "" : `<td>${recapNum(m.sum)}</td>`) +
        `<td>${m.avg === null ? "–" : recapNum(m.avg)}</td>`).join("") + `</tr>`;
    return `<div class="fp-cell rc-tablewrap"><table class="rc-table rc-pivot" data-pi="${pi}">${h}</table></div>`;
  }

  function renderRecap(d) {
    const s = d.summary;
    const C = 2 * Math.PI * 26;
    const tone = s.score >= 85 ? "good" : s.score >= 65 ? "fair" : "poor";
    let h = `<div class="rc-toolbar"><button type="button" class="btn rc-refresh">${t("workspace.recap.refresh")}</button></div>`;

    h += `<div class="fp-cell rc-summary"><svg class="rc-ring rc-${tone}" viewBox="0 0 64 64" aria-hidden="true">` +
      `<circle cx="32" cy="32" r="26" class="rc-ring-bg"/>` +
      `<circle cx="32" cy="32" r="26" class="rc-ring-fg" stroke-dasharray="${(s.score / 100) * C} ${C}" transform="rotate(-90 32 32)"/>` +
      `<text x="32" y="37" text-anchor="middle" class="rc-ring-n">${s.score}</text></svg>` +
      `<div class="rc-summary-text"><strong>${t("workspace.recap.score")}: ${escapeHtml(s.grade)}</strong>` +
      `<span>${recapNum(s.rows)} × ${recapNum(s.cols)}</span>` +
      `<span>${recapNum(s.filled)} ${t("workspace.recap.filled")} · ${recapNum(s.empty)} ${t("workspace.recap.empty")} (${s.fill_pct}%)</span></div></div>`;

    // Jenis data terdeteksi + peran tiap kolom + kartu ringkas (KPI)
    const ds = d.dataset;
    if (ds) {
      h += `<div class="rc-title">${t("workspace.recap.dataset")}</div><div class="fp-cell rc-dataset">` +
        `<div class="rc-dataset-head"><strong>${escapeHtml(ds.label)}</strong>` +
        `<span class="rc-conf rc-conf-${ds.confidence}">${t("workspace.recap.conf." + ds.confidence)}</span></div>` +
        ds.why.map((w) => `<div class="fp-note">${escapeHtml(w)}</div>`).join("") +
        `<div class="rc-roles">` + ds.roles.map((r) =>
          `<span class="rc-role rc-role-${r.role}" title="${escapeHtml(r.role_label)}"><b>${escapeHtml(r.col)}</b>` +
          `${r.label ? " " + escapeHtml(r.label) : ""} · ${escapeHtml(r.role_label)}</span>`).join("") + `</div></div>`;
      if (ds.kpis && ds.kpis.length) {
        h += `<div class="rc-kpis">` + ds.kpis.map((k) =>
          `<div class="rc-kpi"><div class="rc-kpi-v">${typeof k.value === "number" ? recapNum(k.value) : escapeHtml(k.value)}</div>` +
          `<div class="rc-kpi-l">${escapeHtml(k.label)}</div>${k.sub ? `<div class="rc-kpi-s">${escapeHtml(k.sub)}</div>` : ""}</div>`).join("") + `</div>`;
      }
    }

    h += `<div class="rc-title">${t("workspace.recap.conclusion")}</div><div class="fp-cell rc-conclusion">` +
      d.conclusion.map((x) => `<p>${escapeHtml(x)}</p>`).join("") + `</div>`;

    h += `<div class="rc-title">${t("workspace.recap.findings")}</div>`;
    d.findings.forEach((f, i) => {
      const btn = f.targets && f.targets.length
        ? `<button type="button" class="btn rc-show" data-i="${i}">${t("workspace.recap.show")}</button>` : "";
      h += `<div class="fp-cell rc-finding"><div class="fp-cell-head">` +
        `<span class="rc-sev rc-sev-${f.severity}">${t("workspace.recap.sev." + f.severity)}</span>` +
        `<strong class="rc-ftitle">${escapeHtml(f.title)}</strong>` +
        (f.count ? `<span class="rc-count">${recapNum(f.count)}</span>` : "") + `</div>` +
        `<div class="fp-note">${escapeHtml(f.detail)}</div>${btn}</div>`;
    });

    recap.reg = {};
    if (d.column_fill.length) {
      h += `<div class="rc-title">${t("workspace.recap.fill")}</div><div class="fp-cell rc-bars">` +
        d.column_fill.map((c) =>
          `<div class="rc-bar-row"><span class="rc-bar-k">${escapeHtml(c.col)}</span>` +
          `<span class="rc-bar"><span class="rc-bar-fill${c.pct < 50 ? " rc-low" : ""}" style="width:${c.pct}%"></span></span>` +
          `<span class="rc-bar-v">${c.pct}%</span></div>`).join("") + `</div>`;
    }

    // Grafik komposisi kategori (jenis grafik bisa diganti) -- klik bagian grafik = sorot selnya di tabel
    d.categories.forEach((c, ci) => {
      h += `<div class="rc-title">${t("workspace.recap.categories")} · ${escapeHtml(c.col)}</div>` +
        `<div class="fp-cell rc-chartcard">${toggleHtml(ci)}<div class="rc-chart" id="rcc-${ci}"></div>` +
        `<div class="fp-note">${t("workspace.recap.click")}</div></div>`;
    });

    // Histogram sebaran angka
    (d.histograms || []).forEach((hg, hi) => {
      const id = "hist" + hi;
      const items = hg.bins.map((b) => ({ label: recapNum(b.lo), labelEnd: recapNum(b.hi), value: b.count, hit: b.targets.length > 0, targets: b.targets }));
      recap.reg[id] = items;
      h += `<div class="rc-title">${t("workspace.recap.hist")} · ${escapeHtml(hg.col)}</div>` +
        `<div class="fp-cell rc-chartcard">${columnChart(id, items, { edges: true, gap: 0.06 })}` +
        `<div class="fp-note">${t("workspace.recap.click")}</div></div>`;
    });

    // Grafik garis tren
    if (d.trend) {
      const tr = d.trend;
      h += `<div class="rc-title">${t("workspace.recap.trend." + tr.granularity)} · ${escapeHtml(tr.date_col)}</div>` +
        `<div class="fp-cell rc-chartcard">${lineChart(tr.points, tr.cols)}</div>`;
    }

    // Tabel ringkasan (pivot)
    (d.pivots || []).forEach((pv, pi) => {
      h += `<div class="rc-title">${t("workspace.recap.pivot")} · ${escapeHtml(pv.col)}</div>${pivotHtml(pv, pi)}`;
    });

    if (d.numeric.length) {
      h += `<div class="rc-title">${t("workspace.recap.numeric")}</div><div class="fp-cell rc-tablewrap"><table class="rc-table"><tr>` +
        ["", "min", "average", "workspace.recap.median", "max", "workspace.recap.outliers"]
          .map((k) => `<th>${k === "" ? "" : t(k.includes(".") ? k : "workspace.stats." + k)}</th>`).join("") + `</tr>` +
        d.numeric.map((n) =>
          `<tr><td>${escapeHtml(n.col)}</td><td>${recapNum(n.min)}</td><td>${recapNum(n.mean)}</td>` +
          `<td>${recapNum(n.median)}</td><td>${recapNum(n.max)}</td><td>${n.outliers}</td></tr>`).join("") +
        `</table></div>`;
    }

    if (d.dates.length) {
      h += `<div class="rc-title">${t("workspace.recap.dates")}</div><div class="fp-cell">` +
        d.dates.map((x) => `<p class="rc-date"><strong>${escapeHtml(x.col)}</strong>: ${escapeHtml(x.min)} → ${escapeHtml(x.max)} (${x.n})</p>`).join("") + `</div>`;
    }
    recapPanel.innerHTML = h;
    d.categories.forEach((_, ci) => drawCategory(ci, "column"));
  }

  function selectTargets(targets) {
    if (!targets || !targets.length || !state.meta) return;
    state.sel = {
      rects: targets.map(([r1, c1, r2, c2]) => ({ r1, c1, r2, c2 })),
      anchor: { r: targets[0][0], c: targets[0][1] },
    };
    state.precedents.clear();
    selectionChanged();
    scrollToCell(targets[0][0], targets[0][1]);
  }

  recapPanel.addEventListener("click", (e) => {
    if (e.target.closest(".rc-refresh")) { loadRecap(); return; }
    const tog = e.target.closest(".rc-toggle button");
    if (tog && recap.data) { drawCategory(Number(tog.dataset.ci), tog.dataset.type); return; }
    const piv = e.target.closest("tr[data-pivot]");
    if (piv && recap.data) {
      const [ci, gi] = piv.dataset.pivot.split(",").map(Number);
      selectTargets(recap.data.categories[ci].top[gi].targets);
      return;
    }
    const hit = e.target.closest("[data-chart]");
    if (hit) {
      const item = (recap.reg[hit.dataset.chart] || [])[Number(hit.dataset.i)];
      if (item) selectTargets(item.targets);
      return;
    }
    const btn = e.target.closest(".rc-show");
    if (!btn || !recap.data) return;
    const f = recap.data.findings[Number(btn.dataset.i)];
    if (f) selectTargets(f.targets);
  });

  // ---------- Toolbar manipulasi ----------

  const PARAM_FORMS = {
    drop_na: () => `<input data-k="columns" placeholder="Kolom (opsional, mis. A, C)">`,
    drop_duplicates: () => `<input data-k="columns" placeholder="Kolom (opsional, mis. A, C)">`,
    sort: () => `
      <input data-k="column" placeholder="Kolom (mis. B)">
      <select data-k="ascending">
        <option value="true">Naik (A-Z / kecil-besar)</option>
        <option value="false">Turun (Z-A / besar-kecil)</option>
      </select>`,
    filter_rows: () => `
      <input data-k="column" placeholder="Kolom (mis. B)">
      <select data-k="condition">
        <option value="eq">sama dengan</option>
        <option value="neq">tidak sama dengan</option>
        <option value="gt">lebih besar dari</option>
        <option value="lt">lebih kecil dari</option>
        <option value="contains">mengandung teks</option>
      </select>
      <input data-k="value" placeholder="Nilai">`,
    cast_numeric: () => `<input data-k="columns" placeholder="Kolom (mis. D, E)">`,
    drop_column: () => `<input data-k="columns" placeholder="Kolom yang dihapus (mis. C, F)">`,
  };

  opAction.addEventListener("change", () => {
    const builder = PARAM_FORMS[opAction.value];
    opParams.innerHTML = builder ? builder() : "";
  });

  function collectParams() {
    const out = {};
    opParams.querySelectorAll("[data-k]").forEach((el) => {
      out[el.dataset.k] = el.value;
    });
    if (out.columns !== undefined) {
      out.columns = out.columns.trim() ? out.columns.split(",").map((s) => s.trim()) : [];
    }
    if (out.ascending !== undefined) out.ascending = out.ascending === "true";
    return out;
  }

  opApply.addEventListener("click", async () => {
    const action = opAction.value;
    if (!action || !state.sheet) return;
    const op = { action, sheet: state.sheet, ...collectParams() };
    opApply.disabled = true;
    try {
      const res = await fetch(api("/manipulate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(op),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Gagal menerapkan aksi");
      await loadPreview();
    } catch (err) {
      alert(err.message);
    } finally {
      opApply.disabled = false;
    }
  });

  document.getElementById("btnReset").addEventListener("click", async () => {
    if (!confirm("Kembalikan salinan kerja ke kondisi awal upload?")) return;
    await fetch(api("/reset"), { method: "POST" });
    loadPreview();
  });

  // ---------- Chat ----------

  function appendMessage(role, text, meta) {
    const el = document.createElement("div");
    el.className = "chat-msg chat-msg-" + role;
    if (role === "assistant") {
      el.innerHTML = renderMarkdown(text);
    } else {
      el.textContent = text;
    }
    chatLog.appendChild(el);
    if (meta) {
      const m = document.createElement("div");
      m.className = "chat-msg-meta";
      m.textContent = meta;
      chatLog.appendChild(m);
    }
    chatLog.scrollTop = chatLog.scrollHeight;
    return el;
  }

  async function loadChatHistory() {
    const res = await fetch(api("/chat/history"));
    const rows = await res.json();
    rows.forEach((r) => appendMessage(r.role === "user" ? "user" : "assistant", r.content));
  }

  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    appendMessage("user", message);
    chatInput.value = "";
    appendMessage("assistant", "");
    const thinkingEl = chatLog.lastChild;
    // preloader khusus chat: spinner kecil + teks "Berpikir..." di dalam bubble
    // (teks ikut diterjemahkan kalau user ganti bahasa selama menunggu)
    thinkingEl.classList.add("chat-thinking");
    thinkingEl.innerHTML =
      '<span class="chat-spin" aria-hidden="true"></span>' +
      '<span data-i18n="workspace.chat.thinking">' + t("workspace.chat.thinking") + "</span>";
    thinkingEl.setAttribute("role", "status");
    chatLog.scrollTop = chatLog.scrollHeight;

    try {
      const res = await fetch(api("/chat"), {
        method: "POST",
        noLoader: true, // chat punya preloader sendiri, jangan tampilkan preloader global
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, lang: window.MejaDataI18n ? window.MejaDataI18n.getLang() : "id" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || t("workspace.chat.errorAnswer"));
      thinkingEl.classList.remove("chat-thinking");
      thinkingEl.removeAttribute("role");
      thinkingEl.innerHTML = renderMarkdown(data.answer);
      if (data.sheets_used && data.sheets_used.length) {
        const m = document.createElement("div");
        m.className = "chat-msg-meta";
        m.dataset.sheets = data.sheets_used.join(", ");
        renderSheetsMeta(m);
        chatLog.appendChild(m);
        chatLog.scrollTop = chatLog.scrollHeight;
      }
    } catch (err) {
      thinkingEl.classList.remove("chat-thinking");
      thinkingEl.removeAttribute("role");
      thinkingEl.textContent = t("workspace.chat.errorPrefix") + ": " + err.message;
    }
  });

  function renderSheetsMeta(el) {
    el.textContent = format(t("workspace.chat.sheetsUsed"), { sheets: el.dataset.sheets });
  }

  // Ganti bahasa: render ulang semua teks yang dibuat lewat JS (bukan data-i18n statis)
  window.addEventListener("mejadata:langchange", () => {
    chatLog.querySelectorAll(".chat-msg-meta[data-sheets]").forEach(renderSheetsMeta);
    renderGridInfo();
    markRecapStale();
    renderFormulaPanel();
  }, { signal: pageSignal });

  // ---------- Slide: penampil deck gaya Gamma/PowerPoint ----------
  // Deck disusun server dari data asli (grafik & angka akurat), lalu AI memoles judul/poin secara
  // terpisah. Semua teks bisa diklik & diedit; "Unduh PPTX" memakai hasil editan itu.
  const slideModal = document.getElementById("slideModal");
  const slideResult = document.getElementById("slideResult");
  const deckViewer = document.getElementById("deckViewer");
  const deckStage = document.getElementById("deckStage");
  const deckFrame = document.getElementById("deckFrame");
  const deckThumbs = document.getElementById("deckThumbs");
  const deckThemes = document.getElementById("deckThemes");
  const deckAi = document.getElementById("deckAi");
  const SW = 1280, SH = 720;
  const deckState = { deck: null, idx: 0, theme: "aurora", edited: new Set(), gen: 0 };

  document.getElementById("btnSlides").addEventListener("click", () => { slideResult.textContent = ""; slideModal.classList.add("open"); });
  document.getElementById("slideClose").addEventListener("click", () => slideModal.classList.remove("open"));

  const getPath = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
  function setPath(o, path, v) {
    const ks = path.split(".");
    const last = ks.pop();
    const target = ks.reduce((a, k) => a[k], o);
    target[last] = v;
  }

  const editable = (path, tag, cls, text) =>
    `<${tag} class="${cls}" contenteditable="true" spellcheck="false" data-path="${path}">${escapeHtml(text)}</${tag}>`;

  function withPalette(hexes, fn) {
    const saved = PALETTE.slice();
    PALETTE.splice(0, PALETTE.length, ...hexes.map((h) => "#" + h));
    try { return fn(); } finally { PALETTE.splice(0, PALETTE.length, ...saved); }
  }

  function slideChart(sl, th) {
    return withPalette(th.palette, () => {
      const items = sl.labels.map((lb, i) => ({
        label: lb, value: sl.series[0].values[i],
        color: sl.mono ? "#" + th.accent : undefined,
      }));
      if (sl.mono && items.length) items[items.length - 1].labelEnd = sl.label_end || "";
      if (sl.kind === "pie") return pieChart("deck", items, { big: true });
      if (sl.kind === "line") {
        return lineChart(sl.labels.map((lb, i) => ({ label: lb, values: sl.series.map((s) => s.values[i]) })),
          sl.series.map((s) => s.name));
      }
      return columnChart("deck", items.map((x, i) => ({ ...x, color: x.color || PALETTE[i % PALETTE.length] })),
        { edges: !!sl.mono, gap: sl.mono ? 0.08 : 0.3 });
    });
  }

  function slideHtml(sl, i, th) {
    const p = `slides.${i}`;
    const bullets = (sl.bullets || []).map((b, j) => `<li>${editable(`${p}.bullets.${j}`, "span", "sl-b", b)}</li>`).join("");
    const head = (title) => `<div class="sl-head"><i></i>${editable(`${p}.title`, "h2", "sl-h2", title)}</div>`;
    switch (sl.type) {
      case "title":
        return `<div class="sl sl-title"><div class="sl-orb o1"></div><div class="sl-orb o2"></div><div class="sl-tbody">` +
          `${editable("title", "h1", "sl-h1", deckState.deck.title)}<div class="sl-bar"></div>` +
          `${editable("subtitle", "p", "sl-sub", deckState.deck.subtitle)}` +
          `<div class="sl-chips">${(sl.chips || []).map((c) => `<span>${escapeHtml(c)}</span>`).join("")}</div></div></div>`;
      case "kpis":
        return `<div class="sl">${head(sl.title)}<div class="sl-kpis k${Math.min(sl.items.length, 6)}">` +
          sl.items.map((k, j) => `<div class="sl-kpi" style="--c:#${th.palette[j % th.palette.length]}">` +
            `<div class="sl-kv" style="font-size:${String(k.value).length > 16 ? 30 : String(k.value).length > 11 ? 38 : 52}px">${escapeHtml(k.value)}</div>` +
            `<div class="sl-kl">${escapeHtml(k.label)}</div>${k.sub ? `<div class="sl-ks">${escapeHtml(k.sub)}</div>` : ""}</div>`).join("") +
          `</div></div>`;
      case "chart":
        return `<div class="sl">${head(sl.title)}<div class="sl-split"><div class="sl-card sl-chart">${slideChart(sl, th)}</div>` +
          `<div class="sl-card sl-side"><ul>${bullets}</ul></div></div></div>`;
      case "table": {
        const maxes = sl.columns.map((_, c) => Math.max(...sl.raw.map((r) => (typeof r[c] === "number" ? r[c] : 0)), 0));
        const rows = sl.rows.map((r, ri) => `<tr>` + r.map((v, c) => {
          const raw = sl.raw[ri][c];
          const w = (sl.bars || []).includes(c) && typeof raw === "number" && maxes[c] > 0 ? Math.max(0, Math.min(100, (100 * raw) / maxes[c])) : 0;
          return `<td${c > 0 ? ` class="n" style="--w:${w}%"` : ""}>${escapeHtml(v)}</td>`;
        }).join("") + `</tr>`).join("");
        return `<div class="sl">${head(sl.title)}<div class="sl-card sl-tablecard"><table class="sl-table"><tr>` +
          sl.columns.map((c, ci) => `<th${ci > 0 ? ' class="n"' : ""}>${escapeHtml(c)}</th>`).join("") + `</tr>${rows}<tr class="tot">` +
          sl.total.map((v, ci) => `<td${ci > 0 ? ' class="n"' : ""}>${escapeHtml(v)}</td>`).join("") + `</tr></table></div>` +
          `<ul class="sl-cap">${bullets}</ul></div>`;
      }
      case "findings": {
        const C = 2 * Math.PI * 70, tone = sl.score >= 85 ? "#22C55E" : sl.score >= 65 ? "#F59E0B" : "#EF4444";
        return `<div class="sl">${head(sl.title)}<div class="sl-split find"><div class="sl-card sl-score">` +
          `<svg viewBox="0 0 180 180"><circle cx="90" cy="90" r="70" class="bg"/><circle cx="90" cy="90" r="70" stroke="${tone}" ` +
          `stroke-dasharray="${(sl.score / 100) * C} ${C}" transform="rotate(-90 90 90)" class="fg"/>` +
          `<text x="90" y="104" text-anchor="middle" fill="${tone}">${sl.score}</text></svg><b style="color:${tone}">${escapeHtml(sl.grade)}</b></div>` +
          `<div class="sl-finds">` + sl.items.map((f) => `<div class="sl-card sl-find"><span class="pill ${f.severity}">${escapeHtml(f.label)}</span>` +
            `<div><b>${escapeHtml(f.title)}</b><small>${escapeHtml(f.detail)}</small></div></div>`).join("") + `</div></div></div>`;
      }
      default:
        return `<div class="sl">${head(sl.title)}<div class="sl-closing">` + (sl.bullets || []).map((b, j) =>
          `<div class="sl-card sl-end"><span style="background:#${th.palette[j % th.palette.length]}">${j + 1}</span>` +
          editable(`${p}.bullets.${j}`, "p", "sl-b", b) + `</div>`).join("") + `</div></div>`;
    }
  }

  function applyTheme(el) {
    const th = deckState.deck.themes[deckState.theme];
    const v = { bg: th.bg, bg2: th.bg2, fg: th.fg, muted: th.muted, accent: th.accent, accent2: th.accent2, card: th.card };
    Object.entries(v).forEach(([k, c]) => el.style.setProperty("--sl-" + k, "#" + c));
  }

  function renderDeck() {
    const d = deckState.deck;
    if (!d) return;
    const th = d.themes[deckState.theme];
    applyTheme(deckStage);
    deckStage.innerHTML = slideHtml(d.slides[deckState.idx], deckState.idx, th);
    deckThumbs.innerHTML = d.slides.map((sl, i) =>
      `<button type="button" class="deck-thumb${i === deckState.idx ? " active" : ""}" data-i="${i}"><span class="thumb-stage" data-i="${i}"></span><em>${i + 1}</em></button>`).join("");
    deckThumbs.querySelectorAll(".thumb-stage").forEach((el) => {
      applyTheme(el);
      el.innerHTML = slideHtml(d.slides[Number(el.dataset.i)], Number(el.dataset.i), th).replace(/contenteditable="true"/g, "");
    });
    deckThemes.innerHTML = Object.entries(d.themes).map(([k, x]) =>
      `<button type="button" class="deck-theme${k === deckState.theme ? " active" : ""}" data-theme="${k}" title="${escapeHtml(x.name)}" ` +
      `style="background:linear-gradient(135deg,#${x.bg2} 50%,#${x.accent} 50%)"></button>`).join("");
    document.getElementById("deckPos").textContent = `${deckState.idx + 1} / ${d.slides.length}`;
    document.getElementById("deckTitle").textContent = d.title;
    fitDeck();
  }

  function fitDeck() {
    const host = deckFrame.parentElement;
    const k = Math.min(host.clientWidth / SW, host.clientHeight / SH) || 0.5;
    deckFrame.style.width = SW * k + "px";
    deckFrame.style.height = SH * k + "px";
    deckStage.style.transform = `scale(${k})`;
  }

  function goSlide(i) {
    const n = deckState.deck.slides.length;
    deckState.idx = Math.max(0, Math.min(n - 1, i));
    renderDeck();
  }

  function setAiStatus(key) { deckAi.textContent = key ? t(key) : ""; deckAi.dataset.state = key || ""; }

  async function polishDeck() {
    const gen = ++deckState.gen;
    setAiStatus("workspace.slide.ai.working");
    try {
      const res = await fetch(api("/export/slides/polish"), {
        method: "POST", noLoader: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck: deckState.deck }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
      if (gen !== deckState.gen) return;
      deckState.deck.slides.forEach((sl, i) => {
        const o = data.slides && data.slides[sl.id];
        if (!o) return;
        if (o.title && !deckState.edited.has(`slides.${i}.title`)) sl.title = o.title;
        if (o.bullets && o.bullets.length && !deckState.edited.has(`slides.${i}.bullets`) && sl.type !== "kpis" && sl.type !== "findings") sl.bullets = o.bullets;
      });
      // Jangan menggambar ulang saat user sedang mengetik di slide (fokus akan hilang)
      if (deckStage.contains(document.activeElement) && document.activeElement.isContentEditable) deckState.pendingRender = true;
      else renderDeck();
      setAiStatus("workspace.slide.ai.done");
    } catch (err) {
      if (gen === deckState.gen) setAiStatus("workspace.slide.ai.fail");
    }
  }

  function openDeck(deck) {
    deckState.deck = deck;
    deckState.theme = deck.theme || "aurora";
    deckState.idx = 0;
    deckState.edited = new Set();
    slideModal.classList.remove("open");
    deckViewer.hidden = false;
    document.body.classList.add("deck-open");
    renderDeck();
    polishDeck();
  }

  function closeDeck() {
    deckState.gen++;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    deckViewer.hidden = true;
    document.body.classList.remove("deck-open");
  }

  document.getElementById("slideGenerate").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const topic = document.getElementById("slideTopic").value.trim();
    slideResult.textContent = t("workspace.slide.building");
    btn.disabled = true;
    try {
      const res = await fetch(api("/export/slides"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, sheet: state.sheet, lang: currentLang() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Gagal membuat slide");
      openDeck(data.deck);
    } catch (err) {
      slideResult.textContent = t("workspace.chat.errorPrefix") + ": " + err.message;
    } finally {
      btn.disabled = false;
    }
  });

  deckThumbs.addEventListener("click", (e) => {
    const b = e.target.closest(".deck-thumb");
    if (b) goSlide(Number(b.dataset.i));
  });
  deckThemes.addEventListener("click", (e) => {
    const b = e.target.closest(".deck-theme");
    if (!b) return;
    deckState.theme = b.dataset.theme;
    deckState.deck.theme = b.dataset.theme;
    renderDeck();
  });
  document.getElementById("deckPrev").addEventListener("click", () => goSlide(deckState.idx - 1));
  document.getElementById("deckNext").addEventListener("click", () => goSlide(deckState.idx + 1));
  document.getElementById("deckClose").addEventListener("click", closeDeck);
  document.getElementById("deckPresent").addEventListener("click", () => {
    const host = document.getElementById("deckMain");
    if (host.requestFullscreen) host.requestFullscreen().catch(() => {});
  });

  // Edit teks langsung di slide -> disimpan ke deck (dipakai juga saat ekspor PPTX)
  deckStage.addEventListener("input", (e) => {
    const el = e.target.closest("[data-path]");
    if (!el) return;
    const path = el.dataset.path;
    setPath(deckState.deck, path, el.textContent);
    deckState.edited.add(path);
    if (/\.bullets\.\d+$/.test(path)) deckState.edited.add(path.replace(/\.\d+$/, ""));
    if (path === "title") { deckState.deck.slides[0].title = el.textContent; document.getElementById("deckTitle").textContent = el.textContent; }
  });
  deckStage.addEventListener("focusout", () => {
    if (!deckState.pendingRender) return;
    setTimeout(() => {
      const a = document.activeElement;
      if (a && deckStage.contains(a) && a.isContentEditable) return;
      deckState.pendingRender = false;
      renderDeck();
    }, 0);
  });
  deckStage.addEventListener("blur", (e) => {
    if (e.target.closest && e.target.closest("[data-path]")) {
      const thumb = deckThumbs.querySelector(`.deck-thumb[data-i="${deckState.idx}"] .thumb-stage`);
      if (thumb) thumb.innerHTML = slideHtml(deckState.deck.slides[deckState.idx], deckState.idx, deckState.deck.themes[deckState.theme]).replace(/contenteditable="true"/g, "");
    }
  }, true);

  document.getElementById("deckExport").addEventListener("click", async (e) => {
    const btn = e.currentTarget, label = btn.textContent;
    btn.disabled = true;
    btn.textContent = t("workspace.slide.exporting");
    try {
      const res = await fetch(api("/export/slides.pptx"), {
        method: "POST", noLoader: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck: deckState.deck, theme: deckState.theme }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "HTTP " + res.status);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (deckState.deck.title || "slide").replace(/[^\w.-]+/g, "_").slice(0, 60) + ".pptx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  document.addEventListener("keydown", (e) => {
    if (deckViewer.hidden) return;
    const typing = e.target.closest && e.target.closest("[contenteditable]");
    if (e.key === "Escape" && !document.fullscreenElement) { closeDeck(); return; }
    if (typing) return;
    if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); goSlide(deckState.idx + 1); }
    else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); goSlide(deckState.idx - 1); }
    else if (e.key === "f" || e.key === "F") document.getElementById("deckPresent").click();
  }, { signal: pageSignal });
  window.addEventListener("resize", () => { if (!deckViewer.hidden) fitDeck(); }, { signal: pageSignal });
  document.addEventListener("fullscreenchange", () => { if (!deckViewer.hidden) setTimeout(fitDeck, 60); }, { signal: pageSignal });

  // ---------- Modal unggah file ----------

  const uploadModal = document.getElementById("uploadModal");
  const wsDropzone = document.getElementById("wsDropzone");
  const wsFileInput = document.getElementById("wsFileInput");
  const wsDropzoneStatus = document.getElementById("wsDropzoneStatus");
  const wsFileHistory = document.getElementById("wsFileHistory");
  const wsUrlInput = document.getElementById("wsUrlInput");
  const wsUrlSubmit = document.getElementById("wsUrlSubmit");
  const wsUrlStatus = document.getElementById("wsUrlStatus");

  function t(key) {
    return window.MejaDataI18n ? window.MejaDataI18n.t(key) : key;
  }

  function format(str, vars) {
    return str.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : `{${k}}`));
  }

  function setWsStatus(msg, isError) {
    wsDropzoneStatus.textContent = msg || "";
    wsDropzoneStatus.classList.toggle("error", !!isError);
  }

  function formatDate(iso) {
    const lang = localStorage.getItem("meja-data-lang") || "id";
    const locale = lang === "en" ? "en-US" : "id-ID";
    const d = new Date(iso);
    return d.toLocaleDateString(locale, { day: "2-digit", month: "short", year: "numeric" });
  }

  // ---------- Dialog konfirmasi (melayang di luar popup, lihat confirm.js) ----------

  // Fallback ke confirm() bawaan browser kalau confirm.js gagal dimuat
  async function askConfirm(opts) {
    if (window.MejaConfirm) return window.MejaConfirm.ask(opts);
    return window.confirm([opts.title, opts.detail, opts.message].filter(Boolean).join("\n"));
  }

  function openExistingFile(data) {
    if (data.file_id === fileId) {
      uploadModal.classList.remove("open");
      return;
    }
    if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
    else window.location.href = `/workspace/${data.file_id}`;
  }

  // File yang sama sudah pernah diunggah -> tanya user mau membukanya atau tidak
  async function askOpenExisting(data) {
    const isCurrent = data.file_id === fileId;
    const ok = await askConfirm({
      variant: "warn",
      title: t("workspace.upload.dupTitle"),
      detail: data.filename,
      message: format(t(isCurrent ? "workspace.upload.dupMessageCurrent" : "workspace.upload.dupMessage"), {
        date: formatDate(data.uploaded_at),
      }),
      confirmLabel: t("workspace.upload.dupOpen"),
      cancelLabel: t("workspace.upload.dupCancel"),
    });
    if (ok) openExistingFile(data);
  }

  // Konfirmasi hapus: tidak ada yang terhapus sebelum user menekan "Ya, hapus".
  async function confirmDeleteFile(f) {
    const isCurrent = f.id === fileId;
    const ok = await askConfirm({
      variant: "danger",
      title: t("workspace.upload.delTitle"),
      detail: f.filename,
      message: t(isCurrent ? "workspace.upload.delMessageCurrent" : "workspace.upload.delMessage"),
      confirmLabel: t("workspace.upload.delConfirm"),
      cancelLabel: t("workspace.upload.delCancel"),
    });
    if (!ok) return;

    try {
      const res = await fetch(`/api/files/${f.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Gagal menghapus file");
    } catch (err) {
      setWsStatus(err.message, true);
      return;
    }
    if (!isCurrent) {
      loadWsFileHistory(); // file lain yang dihapus: tetap di halaman ini
      return;
    }
    // File yang sedang dibuka dihapus: pindah ke file lain kalau ada, baru ke ruang unggah
    let target = "/dashboard";
    try {
      const rest = await (await fetch("/api/files")).json();
      if (rest.length) target = `/workspace/${rest[0].id}`;
    } catch (err) {
      // fallback ke /dashboard
    }
    if (window.MejaRouter) window.MejaRouter.go(target);
    else window.location.href = target;
  }

  async function loadWsFileHistory() {
    try {
      const res = await fetch("/api/files");
      const files = await res.json();
      if (!files.length) {
        wsFileHistory.innerHTML = `<p class="filelist-empty">${t("dashboard.filelist.empty")}</p>`;
        return;
      }
      wsFileHistory.innerHTML = "";
      files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "file-row" + (f.id === fileId ? " active" : "");
        row.innerHTML = `
          <div>
            <div class="file-name">${escapeHtml(f.filename)}</div>
            <div class="file-meta">${format(t("dashboard.filelist.meta"), { count: f.sheet_names.length, date: formatDate(f.uploaded_at) })}</div>
          </div>
          <a class="btn" href="/workspace/${f.id}">${f.id === fileId ? t("workspace.upload.current") : t("dashboard.filelist.open")}</a>
          <button class="btn" data-id="${f.id}">${t("dashboard.filelist.delete")}</button>
        `;
        row.querySelector("button").addEventListener("click", (e) => {
          e.preventDefault();
          confirmDeleteFile(f);
        });
        wsFileHistory.appendChild(row);
      });
    } catch (err) {
      // biarkan tampilan default "belum ada file"
    }
  }

  document.getElementById("btnUpload").addEventListener("click", () => {
    setWsStatus("", false);
    setWsUrlStatus("", false);
    uploadModal.classList.add("open");
    loadWsFileHistory();
  });
  document.getElementById("uploadModalClose").addEventListener("click", () => {
    uploadModal.classList.remove("open");
  });

  document.getElementById("uploadTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".upload-tab");
    if (!btn) return;
    document.querySelectorAll(".upload-tab").forEach((el) => el.classList.toggle("active", el === btn));
    document.getElementById("uploadPaneFile").hidden = btn.dataset.tab !== "file";
    document.getElementById("uploadPaneLink").hidden = btn.dataset.tab !== "link";
  });

  function setWsUrlStatus(msg, isError) {
    wsUrlStatus.textContent = msg || "";
    wsUrlStatus.classList.toggle("error", !!isError);
  }

  async function uploadFromUrl() {
    const url = wsUrlInput.value.trim();
    if (!url || wsUrlSubmit.disabled) return;
    setWsUrlStatus(t("workspace.upload.linkProcessing"), false);
    wsUrlSubmit.disabled = true;
    try {
      const res = await fetch("/api/upload-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Gagal memproses link");
      if (data.duplicate) {
        setWsUrlStatus("", false);
        askOpenExisting(data);
        return;
      }
      setWsUrlStatus(format(t("dashboard.status.success"), { count: data.sheets.length }), false);
      if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
      else window.location.href = `/workspace/${data.file_id}`;
    } catch (err) {
      setWsUrlStatus(err.message, true);
    } finally {
      wsUrlSubmit.disabled = false;
    }
  }

  wsUrlSubmit.addEventListener("click", uploadFromUrl);
  wsUrlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") uploadFromUrl();
  });

  let wsUploading = false;

  async function uploadNewFile(file) {
    if (!file || wsUploading) return;
    const tooBig = window.MejaHttp && window.MejaHttp.checkUploadSize(file);
    if (tooBig) {
      setWsStatus(tooBig, true);
      return;
    }
    wsUploading = true;
    setWsStatus(format(t("dashboard.status.processing"), { name: file.name }), false);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload gagal");
      if (data.duplicate) {
        setWsStatus("", false);
        askOpenExisting(data);
        return;
      }
      setWsStatus(format(t("dashboard.status.success"), { count: data.sheets.length }), false);
      if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
      else window.location.href = `/workspace/${data.file_id}`;
    } catch (err) {
      setWsStatus(err.message, true);
    } finally {
      wsUploading = false;
    }
  }

  wsDropzone.addEventListener("click", () => wsFileInput.click());
  wsFileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    // reset supaya memilih file yang sama lagi tetap memicu event "change"
    e.target.value = "";
    uploadNewFile(file);
  });

  ["dragenter", "dragover"].forEach((evt) =>
    wsDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      wsDropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    wsDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      wsDropzone.classList.remove("dragover");
    })
  );
  wsDropzone.addEventListener("drop", (e) => {
    uploadNewFile(e.dataTransfer.files[0]);
  });

  // ---------- Init ----------

  loadSheets();
  loadChatHistory();
})();
