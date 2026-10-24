(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const status = document.getElementById("dropzoneStatus");
  const fileListBody = document.getElementById("fileListBody");

  function t(key) {
    return window.MejaDataI18n ? window.MejaDataI18n.t(key) : key;
  }

  function format(str, vars) {
    return str.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : `{${k}}`));
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function setStatus(msg, isError) {
    status.textContent = msg || "";
    status.classList.toggle("error", !!isError);
  }

  function removeDupConfirm() {
    document.querySelectorAll(".dup-confirm").forEach((el) => el.remove());
  }

  function showDupConfirm(data) {
    removeDupConfirm();
    const box = document.createElement("div");
    box.className = "dup-confirm";
    box.innerHTML =
      `<strong>${escapeHtml(t("workspace.upload.dupTitle"))}</strong>` +
      `<p>${escapeHtml(format(t("workspace.upload.dupMessage"), { name: data.filename, date: formatDate(data.uploaded_at) }))}</p>` +
      `<div class="dup-confirm-actions">` +
      `<button type="button" class="btn btn-primary" data-act="open">${escapeHtml(t("workspace.upload.dupOpen"))}</button>` +
      `<button type="button" class="btn" data-act="cancel">${escapeHtml(t("workspace.upload.dupCancel"))}</button>` +
      `</div>`;
    box.querySelector('[data-act="open"]').addEventListener("click", () => {
      removeDupConfirm();
      if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
      else window.location.href = `/workspace/${data.file_id}`;
    });
    box.querySelector('[data-act="cancel"]').addEventListener("click", removeDupConfirm);
    status.insertAdjacentElement("afterend", box);
  }

  let uploading = false;

  async function uploadFile(file) {
    if (!file || uploading) return;
    uploading = true;
    removeDupConfirm();
    setStatus(format(t("dashboard.status.processing"), { name: file.name }), false);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload gagal");
      if (data.duplicate) {
        setStatus("", false);
        showDupConfirm(data);
        return;
      }
      setStatus(format(t("dashboard.status.success"), { count: data.sheets.length }), false);
      if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
      else window.location.href = `/workspace/${data.file_id}`;
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      uploading = false;
    }
  }

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = ""; // supaya file yang sama bisa dipilih lagi
    uploadFile(file);
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    uploadFile(file);
  });

  function formatDate(iso) {
    const lang = localStorage.getItem("meja-data-lang") || "id";
    const locale = lang === "en" ? "en-US" : "id-ID";
    const d = new Date(iso);
    return d.toLocaleDateString(locale, { day: "2-digit", month: "short", year: "numeric" });
  }

  function confirmDeleteFile(f, row) {
    removeDupConfirm();
    const box = document.createElement("div");
    box.className = "dup-confirm danger";
    box.innerHTML =
      `<strong>${escapeHtml(t("workspace.upload.delTitle"))}</strong>` +
      `<p>${escapeHtml(format(t("workspace.upload.delMessage"), { name: f.filename }))}</p>` +
      `<div class="dup-confirm-actions">` +
      `<button type="button" class="btn btn-danger" data-act="del">${escapeHtml(t("workspace.upload.delConfirm"))}</button>` +
      `<button type="button" class="btn" data-act="cancel">${escapeHtml(t("workspace.upload.delCancel"))}</button>` +
      `</div>`;
    const delBtn = box.querySelector('[data-act="del"]');
    box.querySelector('[data-act="cancel"]').addEventListener("click", removeDupConfirm);
    delBtn.addEventListener("click", async () => {
      delBtn.disabled = true;
      try {
        const res = await fetch(`/api/files/${f.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Gagal menghapus file");
        removeDupConfirm();
        loadFileList();
      } catch (err) {
        removeDupConfirm();
        setStatus(err.message, true);
      }
    });
    row.insertAdjacentElement("afterend", box);
  }

  async function loadFileList() {
    try {
      const res = await fetch("/api/files");
      const files = await res.json();
      removeDupConfirm();
      if (!files.length) {
        fileListBody.innerHTML = `<p class="filelist-empty">${t("dashboard.filelist.empty")}</p>`;
        return;
      }

      fileListBody.innerHTML = "";
      files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "file-row";
        row.innerHTML = `
          <div>
            <div class="file-name">${escapeHtml(f.filename)}</div>
            <div class="file-meta">${format(t("dashboard.filelist.meta"), { count: f.sheet_names.length, date: formatDate(f.uploaded_at) })}</div>
          </div>
          <span class="file-meta">${f.status}</span>
          <a class="btn" href="/workspace/${f.id}">${t("dashboard.filelist.open")}</a>
          <button class="btn" data-id="${f.id}">${t("dashboard.filelist.delete")}</button>
        `;
        row.querySelector("button").addEventListener("click", (e) => {
          e.preventDefault();
          confirmDeleteFile(f, row);
        });
        fileListBody.appendChild(row);
      });
    } catch (err) {
      // biarkan tampilan default "belum ada file"
    }
  }

  loadFileList();
})();
