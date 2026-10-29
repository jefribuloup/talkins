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

  async function askConfirm(opts) {
    if (window.MejaConfirm) return window.MejaConfirm.ask(opts);
    return window.confirm([opts.title, opts.detail, opts.message].filter(Boolean).join("\n"));
  }

  // File yang sama sudah pernah diunggah -> tanya user mau membukanya atau tidak
  async function askOpenExisting(data) {
    const ok = await askConfirm({
      variant: "warn",
      title: t("workspace.upload.dupTitle"),
      detail: data.filename,
      message: format(t("workspace.upload.dupMessage"), { date: formatDate(data.uploaded_at) }),
      confirmLabel: t("workspace.upload.dupOpen"),
      cancelLabel: t("workspace.upload.dupCancel"),
    });
    if (!ok) return;
    if (window.MejaRouter) window.MejaRouter.go(`/workspace/${data.file_id}`);
    else window.location.href = `/workspace/${data.file_id}`;
  }

  let uploading = false;

  async function uploadFile(file) {
    if (!file || uploading) return;
    const tooBig = window.MejaHttp && window.MejaHttp.checkUploadSize(file);
    if (tooBig) {
      setStatus(tooBig, true);
      return;
    }
    uploading = true;
    setStatus(format(t("dashboard.status.processing"), { name: file.name }), false);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload gagal");
      if (data.duplicate) {
        setStatus("", false);
        askOpenExisting(data);
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

  async function confirmDeleteFile(f) {
    const ok = await askConfirm({
      variant: "danger",
      title: t("workspace.upload.delTitle"),
      detail: f.filename,
      message: t("workspace.upload.delMessage"),
      confirmLabel: t("workspace.upload.delConfirm"),
      cancelLabel: t("workspace.upload.delCancel"),
    });
    if (!ok) return;
    try {
      const res = await fetch(`/api/files/${f.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Gagal menghapus file");
      loadFileList();
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function loadFileList() {
    try {
      const res = await fetch("/api/files");
      const files = await res.json();
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
          confirmDeleteFile(f);
        });
        fileListBody.appendChild(row);
      });
    } catch (err) {
      // biarkan tampilan default "belum ada file"
    }
  }

  loadFileList();
})();
