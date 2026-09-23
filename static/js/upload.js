(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const status = document.getElementById("dropzoneStatus");
  const fileListBody = document.getElementById("fileListBody");

  function setStatus(msg, isError) {
    status.textContent = msg || "";
    status.classList.toggle("error", !!isError);
  }

  async function uploadFile(file) {
    if (!file) return;
    setStatus(`Memproses ${file.name} ...`, false);

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload gagal");
      setStatus(`Berhasil: ${data.sheets.length} sheet terbaca. Membuka ruang kerja...`, false);
      window.location.href = `/workspace/${data.file_id}`;
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => uploadFile(e.target.files[0]));

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
    const d = new Date(iso);
    return d.toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "numeric" });
  }

  async function loadFileList() {
    try {
      const res = await fetch("/api/files");
      const files = await res.json();
      if (!files.length) return;

      fileListBody.innerHTML = "";
      files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "file-row";
        row.innerHTML = `
          <div>
            <div class="file-name">${f.filename}</div>
            <div class="file-meta">${f.sheet_names.length} sheet · diunggah ${formatDate(f.uploaded_at)}</div>
          </div>
          <span class="file-meta">${f.status}</span>
          <a class="btn" href="/workspace/${f.id}">Buka</a>
          <button class="btn" data-id="${f.id}">Hapus</button>
        `;
        row.querySelector("button").addEventListener("click", async (e) => {
          e.preventDefault();
          await fetch(`/api/files/${f.id}`, { method: "DELETE" });
          loadFileList();
        });
        fileListBody.appendChild(row);
      });
    } catch (err) {
      // biarkan tampilan default "belum ada file"
    }
  }

  loadFileList();
})();
