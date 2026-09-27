(function () {
  const fileId = document.body.dataset.fileId;
  const api = (path) => `/api/files/${fileId}${path}`;

  const sheetTabs = document.getElementById("sheetTabs");
  const tableHead = document.getElementById("tableHead");
  const tableBody = document.getElementById("tableBody");
  const pagerInfo = document.getElementById("pagerInfo");
  const pagePrev = document.getElementById("pagePrev");
  const pageNext = document.getElementById("pageNext");

  const opAction = document.getElementById("opAction");
  const opParams = document.getElementById("opParams");
  const opApply = document.getElementById("opApply");

  const chatLog = document.getElementById("chatLog");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");

  const state = { sheet: null, page: 1, totalPages: 1 };

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

  async function loadPreview() {
    const res = await fetch(api(`/preview?sheet=${encodeURIComponent(state.sheet)}&page=${state.page}`));
    const data = await res.json();
    state.totalPages = data.total_pages;

    tableHead.innerHTML = data.columns.map((c) => `<th>${c}</th>`).join("");
    tableBody.innerHTML = data.rows
      .map((row) => `<tr>${row.map((v) => `<td>${v === null ? "" : v}</td>`).join("")}</tr>`)
      .join("");

    pagerInfo.textContent = `Hal ${data.page} / ${data.total_pages} · ${data.total_rows} baris`;
    pagePrev.disabled = data.page <= 1;
    pageNext.disabled = data.page >= data.total_pages;
  }

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
    el.textContent = text;
    chatLog.appendChild(el);
    if (meta) {
      const m = document.createElement("div");
      m.className = "chat-msg-meta";
      m.textContent = meta;
      chatLog.appendChild(m);
    }
    chatLog.scrollTop = chatLog.scrollHeight;
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
    appendMessage("assistant", "Berpikir...");
    const thinkingEl = chatLog.lastChild;

    try {
      const res = await fetch(api("/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Gagal mendapat jawaban");
      thinkingEl.textContent = data.answer;
      if (data.sheets_used && data.sheets_used.length) {
        const m = document.createElement("div");
        m.className = "chat-msg-meta";
        m.textContent = "Sheet dianalisis: " + data.sheets_used.join(", ");
        chatLog.appendChild(m);
      }
    } catch (err) {
      thinkingEl.textContent = "Gagal: " + err.message;
    }
  });

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
            window.location.href = "/dashboard";
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
      window.location.href = `/workspace/${data.file_id}`;
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
      window.location.href = `/workspace/${data.file_id}`;
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
