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
  const formulaPanelEmpty = document.getElementById("formulaPanelEmpty");
  const formulaPanelContent = document.getElementById("formulaPanelContent");
  const pagerInfo = document.getElementById("pagerInfo");
  const pagePrev = document.getElementById("pagePrev");
  const pageNext = document.getElementById("pageNext");

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

  const state = {
    sheet: null,
    page: 1,
    totalPages: 1,
    currentPreview: null,      // respons /preview terakhir (dipakai tab Rumus)
    selectedCells: new Set(),  // key "r,c" (r = baris di halaman ini, c = index kolom)
    selectionAnchor: null,     // {r, c} untuk Shift+klik rentang
    precedents: new Set(),     // sel "bahan rumus" yang lagi ditandai
  };

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
    state.page = 1;
    [...sheetTabs.children].forEach((el) => el.classList.toggle("active", el.textContent === name));
    loadPreview();
  }

  // ---------- Tabel + pager ----------

  // ---------- Tab kanan: Chat | Rumus ----------

  function setPanelTab(name) {
    panelTabs.querySelectorAll(".panel-tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === name);
    });
    tabViewChat.hidden = name !== "chat";
    tabViewFormula.hidden = name !== "formula";
    if (name === "chat") chatLog.scrollTop = chatLog.scrollHeight;
  }

  panelTabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".panel-tab");
    if (btn) setPanelTab(btn.dataset.tab);
  });

  // ---------- Tabel + pemilihan sel (gaya Excel) ----------

  async function loadPreview() {
    const res = await fetch(api(`/preview?sheet=${encodeURIComponent(state.sheet)}&page=${state.page}`));
    const data = await res.json();
    state.totalPages = data.total_pages;
    state.currentPreview = data;
    state.selectedCells.clear();
    state.precedents.clear();
    state.selectionAnchor = null;

    const formulaCols = data.formula_columns || {};
    const offset = (data.page - 1) * data.page_size;

    // Header cuma satu baris: huruf kolom A, B, C... (huruf di sheet ASLI, jadi cocok dgn rumus
    // seperti =B4*C4). Nama kolom hasil deteksi TIDAK lagi jadi header terpisah: di spreadsheet
    // ia hanya sel biasa di baris pertama, jadi ditampilkan sebagai baris data pertama di bawah.
    // Nama kolom tetap dipakai di balik layar (toolbar, chat, tab Rumus) dan muncul sbg tooltip.
    // Klik huruf = pilih satu kolom penuh. ƒx = kolom ini berisi rumus.
    tableLetters.innerHTML =
      `<th class="row-gutter"></th>` +
      data.columns
        .map((c, j) => {
          const tag = formulaCols[c] ? `<span class="col-formula-tag">ƒx</span>` : "";
          return `<th class="col-letter" data-col="${j}" title="${escapeHtml(c)}">${colLetter(data, c, j)}${tag}</th>`;
        })
        .join("");

    // Baris header asli (mis. baris 1) ditampilkan sbg baris pertama tabel, hanya di halaman 1.
    // Kolom yg namanya dibuat otomatis (kolom_N) berarti selnya kosong di sheet asli.
    const headerRowHtml =
      data.page === 1
        ? `<tr class="header-as-row"><td class="row-gutter" data-row="-1">${rowNumber(data, -1)}</td>` +
          data.columns
            .map((c, j) => `<td data-row="-1" data-col="${j}">${escapeHtml(headerCellValue(data, j))}</td>`)
            .join("") +
          `</tr>`
        : "";

    // Body: nomor baris (klik = pilih baris penuh) + sel data.
    tableBody.innerHTML = headerRowHtml + data.rows
      .map(
        (row, r) =>
          `<tr><td class="row-gutter" data-row="${r}">${rowNumber(data, offset + r)}</td>` +
          row
            .map((v, j) => `<td data-row="${r}" data-col="${j}">${v === null ? "" : escapeHtml(String(v))}</td>`)
            .join("") +
          `</tr>`
      )
      .join("");

    renderSelectionHighlight();
    renderFormulaPanel();

    renderPager(data);
    pagePrev.disabled = data.page <= 1;
    pageNext.disabled = data.page >= data.total_pages;
  }

  // Nomor baris seperti di Excel/Spreadsheet: header = baris pertama (atau baris header asli
  // kalau ada judul di atasnya), jadi data pertama = header_row + 1 (umumnya 2).
  // pos = posisi 0-based baris data di tabel (lintas halaman).
  function rowNumber(d, pos) {
    return pos + 1 + (d && d.header_row ? d.header_row : 1);
  }

  // Baris header asli diperlakukan sbg baris ke-1 tabel dengan indeks r = -1 (hanya di halaman 1),
  // jadi ikut sistem seleksi yang sama dgn sel data: klik, Shift+klik, Ctrl+klik, pilih baris/kolom.
  function firstRow(d) {
    return d && d.page === 1 ? -1 : 0;
  }
  // Nama kolom hasil deteksi = isi sel baris header. Nama otomatis (kolom_N) berarti sel kosong.
  function headerCellValue(d, c) {
    const name = d.columns[c];
    return /^kolom_\d+$/.test(name) ? "" : name;
  }
  function cellValue(d, r, c) {
    return r < 0 ? headerCellValue(d, c) : d.rows[r][c];
  }

  // 0 -> A, 25 -> Z, 26 -> AA. Dipakai sebagai cadangan kalau sheet belum punya huruf asli
  // (file yang diunggah sebelum fitur ini) -> huruf berdasarkan urutan kolom yang tampil.
  function idxToLetters(idx) {
    let n = idx + 1, out = "";
    while (n > 0) {
      const rem = (n - 1) % 26;
      out = String.fromCharCode(65 + rem) + out;
      n = Math.floor((n - 1) / 26);
    }
    return out;
  }

  function colLetter(d, name, j) {
    return (d.column_letters && d.column_letters[name]) || idxToLetters(j);
  }

  function renderPager(d) {
    pagerInfo.textContent = format(t("workspace.pager.info"), {
      page: d.page,
      total: d.total_pages,
      rows: d.total_rows,
    });
  }

  function applySelection(cellKeys, e, { anchor, single }) {
    if (e.shiftKey && single && state.selectionAnchor) {
      // Shift+klik: seluruh persegi panjang dari anchor ke sel yang diklik
      const a = state.selectionAnchor;
      state.selectedCells.clear();
      for (let r = Math.min(a.r, anchor.r); r <= Math.max(a.r, anchor.r); r++) {
        for (let c = Math.min(a.c, anchor.c); c <= Math.max(a.c, anchor.c); c++) {
          state.selectedCells.add(`${r},${c}`);
        }
      }
    } else if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd+klik: tambah/lepas dari pilihan yang ada
      const allIn = cellKeys.every((k) => state.selectedCells.has(k));
      cellKeys.forEach((k) => (allIn ? state.selectedCells.delete(k) : state.selectedCells.add(k)));
      state.selectionAnchor = anchor;
    } else {
      state.selectedCells.clear();
      cellKeys.forEach((k) => state.selectedCells.add(k));
      state.selectionAnchor = anchor;
    }
    state.precedents.clear();
    renderSelectionHighlight();
    renderFormulaPanel();
    maybeRevealFormulaTab();
  }

  function clearSelection() {
    state.selectedCells.clear();
    state.precedents.clear();
    state.selectionAnchor = null;
    renderSelectionHighlight();
    renderFormulaPanel();
  }

  // Auto-pindah ke tab Rumus hanya kalau ada sel terpilih yang berada di kolom berumus,
  // supaya user yang sedang asyik di tab Chat tidak "ditarik" tiap klik sel biasa.
  function maybeRevealFormulaTab() {
    const d = state.currentPreview;
    if (!d || !state.selectedCells.size) return;
    const fcols = d.formula_columns || {};
    const hit = [...state.selectedCells].some((k) => {
      const [r, c] = k.split(",").map(Number);
      return r >= 0 && fcols[d.columns[c]];
    });
    if (hit) setPanelTab("formula");
  }

  const onHeaderClick = (e) => {
    const th = e.target.closest("th[data-col]");
    if (!th || !state.currentPreview) return;
    const c = Number(th.dataset.col);
    const d = state.currentPreview;
    const keys = [];
    for (let r = firstRow(d); r < d.rows.length; r++) keys.push(`${r},${c}`);
    applySelection(keys, e, { anchor: { r: firstRow(d), c }, single: false });
  };
  tableLetters.addEventListener("click", onHeaderClick);

  tableBody.addEventListener("click", (e) => {
    if (!state.currentPreview) return;
    const gutter = e.target.closest("td.row-gutter");
    if (gutter) {
      if (gutter.dataset.row === undefined) return; // gutter baris header (bukan data)
      const r = Number(gutter.dataset.row);
      const keys = state.currentPreview.columns.map((_, c) => `${r},${c}`);
      applySelection(keys, e, { anchor: { r, c: 0 }, single: false });
      return;
    }
    const cell = e.target.closest("td[data-col]");
    if (!cell) return;
    const r = Number(cell.dataset.row);
    const c = Number(cell.dataset.col);
    applySelection([`${r},${c}`], e, { anchor: { r, c }, single: true });
  });

  // Listener global dibatalkan otomatis oleh router saat user pindah page (tanpa reload)
  const pageSignal = window.MejaRouter ? window.MejaRouter.signal : undefined;

  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (e.key === "Escape" && !/INPUT|TEXTAREA|SELECT/.test(tag) && state.selectedCells.size) clearSelection();
  }, { signal: pageSignal });

  function renderSelectionHighlight() {
    const d = state.currentPreview;
    if (!d) return;
    const sel = state.selectedCells;
    const rowsN = d.rows.length;
    const r0 = firstRow(d);

    tableBody.querySelectorAll("td[data-col]").forEach((td) => {
      const key = `${td.dataset.row},${td.dataset.col}`;
      td.classList.toggle("cell-selected", sel.has(key));
      td.classList.toggle("cell-precedent", state.precedents.has(key));
    });
    tableBody.querySelectorAll("td.row-gutter").forEach((td) => {
      const r = td.dataset.row;
      td.classList.toggle("gutter-active", d.columns.some((_, c) => sel.has(`${r},${c}`)));
    });
    [tableLetters].forEach((row) => {
      row.querySelectorAll("th[data-col]").forEach((th) => {
        const c = th.dataset.col;
        let all = rowsN > 0;
        for (let r = r0; r < rowsN && all; r++) all = sel.has(`${r},${c}`);
        th.classList.toggle("gutter-active", all);
      });
    });

    // penanda jumlah sel terpilih di tab Rumus
    const formulaTab = panelTabs.querySelector('[data-tab="formula"]');
    let badge = formulaTab.querySelector(".panel-tab-count");
    if (sel.size) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "panel-tab-count";
        formulaTab.appendChild(badge);
      }
      badge.textContent = sel.size;
    } else if (badge) {
      badge.remove();
    }
  }

  // ---------- Isi tab Rumus ----------

  function fmtVal(v) {
    return v === null || v === undefined || v === "" ? "–" : escapeHtml(String(v));
  }

  function precedentKeys(dep, d) {
    const offset = (d.page - 1) * d.page_size;
    if (!dep.rows || !dep.cols.length) return [];
    const keys = [];
    for (let row = dep.rows[0]; row <= dep.rows[1]; row++) {
      const r = row - 1 - offset;
      if (r < 0 || r >= d.rows.length) continue;
      dep.cols.forEach((name) => {
        const c = d.columns.indexOf(name);
        if (c >= 0) keys.push(`${r},${c}`);
      });
    }
    return keys;
  }

  function renderDeps(entry, d) {
    if (!entry.deps || !entry.deps.length) return "";
    const chips = entry.deps
      .map((dep) => {
        const rowsLabel = !dep.rows
          ? t("workspace.formula.depsOutside")
          : dep.rows[0] === dep.rows[1]
          ? format(t("workspace.formula.depsRow"), { r: rowNumber(d, dep.rows[0] - 1) })
          : format(t("workspace.formula.depsRows"), { a: rowNumber(d, dep.rows[0] - 1), b: rowNumber(d, dep.rows[1] - 1) });
        const name = dep.cols.length ? dep.cols.map(escapeHtml).join(", ") : "?";
        const keys = precedentKeys(dep, d);
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
    const d = state.currentPreview;
    if (!d || !state.selectedCells.size) {
      formulaPanelEmpty.hidden = false;
      formulaPanelContent.hidden = true;
      formulaPanelContent.innerHTML = "";
      return;
    }
    formulaPanelEmpty.hidden = true;
    formulaPanelContent.hidden = false;

    const cells = [...state.selectedCells]
      .map((k) => k.split(",").map(Number))
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);

    const fCells = d.formula_cells || {};
    const fCols = d.formula_columns || {};
    const dirty = !!d.row_order_dirty;
    const offset = (d.page - 1) * d.page_size;
    const cellEntry = (r, col) => {
      const raw = fCells[String(r)] && fCells[String(r)][col];
      return raw ? (typeof raw === "string" ? { f: raw, deps: [], x: false } : raw) : null;
    };

    let formulaCount = 0;
    cells.forEach(([r, c]) => { if (cellEntry(r, d.columns[c])) formulaCount++; });

    const MAX_SHOWN = 120;
    const cards = cells.slice(0, MAX_SHOWN).map(([r, c]) => {
      const col = d.columns[c];
      const isHeaderCell = r < 0;
      const val = cellValue(d, r, c);
      const entry = isHeaderCell ? null : cellEntry(r, col);
      const info = isHeaderCell ? null : fCols[col];
      let badge, body;

      if (entry) {
        badge = `<span class="fp-badge fp-badge-formula">${t("workspace.formula.badgeFormula")}</span>`;
        body =
          `<code class="fp-formula">${escapeHtml(entry.f)}</code>` +
          `<div class="fp-value">${t("workspace.formula.result")}: <strong>${fmtVal(val)}</strong></div>` +
          renderDeps(entry, d);
      } else if (dirty && info) {
        badge = `<span class="fp-badge fp-badge-unknown">${t("workspace.formula.badgeUnknown")}</span>`;
        body =
          `<div class="fp-note">${format(t("workspace.formula.dirtyNote"), {
            col: `<strong>${escapeHtml(col)}</strong>`,
            pct: Math.round((info.ratio || 0) * 100),
            sample: `<code>${escapeHtml(info.sample || "")}</code>`,
          })}</div>` +
          `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      } else if (info) {
        badge = `<span class="fp-badge fp-badge-static">${t("workspace.formula.badgeStatic")}</span>`;
        body =
          `<div class="fp-note">${format(t("workspace.formula.staticNote"), { col: `<strong>${escapeHtml(col)}</strong>` })}</div>` +
          `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      } else {
        badge = `<span class="fp-badge fp-badge-plain">${t("workspace.formula.badgePlain")}</span>`;
        body = `<div class="fp-value">${t("workspace.formula.value")}: <strong>${fmtVal(val)}</strong></div>`;
      }

      return (
        `<div class="fp-cell"><div class="fp-cell-head">` +
        `<span class="fp-letter">${colLetter(d, col, c)}</span>` +
        `<span class="fp-col">${escapeHtml(col)}</span>` +
        `<span class="fp-row">#${rowNumber(d, offset + r)}</span>${badge}</div>${body}</div>`
      );
    });

    const noFormulaNote =
      !Object.keys(fCols).length && !dirty
        ? `<div class="fp-note fp-note-top">${t("workspace.formula.noneInSheet")}</div>`
        : "";
    const more =
      cells.length > MAX_SHOWN
        ? `<p class="fp-more">${format(t("workspace.formula.more"), { n: cells.length - MAX_SHOWN })}</p>`
        : "";

    formulaPanelContent.innerHTML =
      `<div class="fp-summary"><span>${format(t("workspace.formula.selected"), { n: cells.length, f: formulaCount })}</span>` +
      (dirty ? `<span class="fp-dirty-flag">${t("workspace.formula.dirtyFlag")}</span>` : "") +
      `</div>${noFormulaNote}${cards.join("")}${more}`;
  }

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
    renderSelectionHighlight();
    if (!same) {
      const first = tableBody.querySelector("td.cell-precedent");
      if (first && first.scrollIntoView) first.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    }
  });

  pagePrev.addEventListener("click", () => { state.page--; loadPreview(); });
  pageNext.addEventListener("click", () => { state.page++; loadPreview(); });

  // ---------- Toolbar manipulasi ----------

  const PARAM_FORMS = {
    drop_na: () => `<input data-k="columns" placeholder="Kolom (opsional, pisah koma)">`,
    drop_duplicates: () => `<input data-k="columns" placeholder="Kolom (opsional, pisah koma)">`,
    sort: () => `
      <input data-k="column" placeholder="Nama kolom">
      <select data-k="ascending">
        <option value="true">Naik (A-Z / kecil-besar)</option>
        <option value="false">Turun (Z-A / besar-kecil)</option>
      </select>`,
    filter_rows: () => `
      <input data-k="column" placeholder="Nama kolom">
      <select data-k="condition">
        <option value="eq">sama dengan</option>
        <option value="neq">tidak sama dengan</option>
        <option value="gt">lebih besar dari</option>
        <option value="lt">lebih kecil dari</option>
        <option value="contains">mengandung teks</option>
      </select>
      <input data-k="value" placeholder="Nilai">`,
    cast_numeric: () => `<input data-k="columns" placeholder="Kolom (pisah koma)">`,
    drop_column: () => `<input data-k="columns" placeholder="Kolom yang dihapus (pisah koma)">`,
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
      state.page = 1;
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
    state.page = 1;
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
    if (state.currentPreview) renderPager(state.currentPreview);
    renderFormulaPanel();
  }, { signal: pageSignal });

  // ---------- Modal slide ----------

  const slideModal = document.getElementById("slideModal");
  const slideResult = document.getElementById("slideResult");

  document.getElementById("btnSlides").addEventListener("click", () => slideModal.classList.add("open"));
  document.getElementById("slideClose").addEventListener("click", () => slideModal.classList.remove("open"));

  document.getElementById("slideGenerate").addEventListener("click", async () => {
    const topic = document.getElementById("slideTopic").value.trim();
    slideResult.innerHTML = "Membuat outline...";
    try {
      const res = await fetch(api("/export/slides"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Gagal membuat outline");
      slideResult.innerHTML = data.slides
        .map((s) => `<div class="slide-item"><h4>${s.title}</h4><ul>${(s.bullets || []).map((b) => `<li>${b}</li>`).join("")}</ul></div>`)
        .join("");
    } catch (err) {
      slideResult.innerHTML = `<p>Gagal: ${err.message}</p>`;
    }
  });

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
            <div class="file-name">${f.filename}</div>
            <div class="file-meta">${format(t("dashboard.filelist.meta"), { count: f.sheet_names.length, date: formatDate(f.uploaded_at) })}</div>
          </div>
          <a class="btn" href="/workspace/${f.id}">${f.id === fileId ? t("workspace.upload.current") : t("dashboard.filelist.open")}</a>
          <button class="btn" data-id="${f.id}">${t("dashboard.filelist.delete")}</button>
        `;
        row.querySelector("button").addEventListener("click", async (e) => {
          e.preventDefault();
          await fetch(`/api/files/${f.id}`, { method: "DELETE" });
          if (f.id === fileId) {
            if (window.MejaRouter) window.MejaRouter.go("/dashboard");
            else window.location.href = "/dashboard";
            return;
          }
          loadWsFileHistory();
        });
        wsFileHistory.appendChild(row);
      });
    } catch (err) {
      // biarkan tampilan default "belum ada file"
    }
  }

  document.getElementById("btnUpload").addEventListener("click", () => {
    setWsStatus("", false);
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
    if (!url) return;
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

  async function uploadNewFile(file) {
    if (!file) return;
    setWsStatus(format(t("dashboard.status.processing"), { name: file.name }), false);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload gagal");
      setWsStatus(format(t("dashboard.status.success"), { count: data.sheets.length }), false);
      if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
      else window.location.href = `/workspace/${data.file_id}`;
    } catch (err) {
      setWsStatus(err.message, true);
    }
  }

  wsDropzone.addEventListener("click", () => wsFileInput.click());
  wsFileInput.addEventListener("change", (e) => uploadNewFile(e.target.files[0]));

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
