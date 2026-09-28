(function () {
  const STORAGE_KEY = "meja-data-lang";

  const DICT = {
    id: {
      "nav.home": "Beranda",
      "nav.dashboard": "Dashboard",
      "footer.tagline": "Ngobrol dengan file Excel-mu, tanpa menyentuh file aslinya.",
      "footer.stack": "Flask · Neon Postgres · Gemini Flash / Groq",

      "index.hero.title": "Ajak ngobrol<br>file Excel-mu.",
      "index.hero.lede": "Upload satu atau banyak sheet, biar sistem yang rapikan datanya duluan. Tanya apa saja soal isinya, otak-atik angkanya, lihat hasilnya jadi slide — file asli di komputermu tidak pernah tersentuh sama sekali.",
      "index.hero.cta": "Buka Dashboard",
      "index.section.howitworks": "Cara kerja",
      "index.feature1.label": "Sheet apa saja",
      "index.feature1.text": "Single sheet atau puluhan sheet sekaligus, termasuk file dengan judul/logo di atas tabel — sistem mendeteksi baris header-nya sendiri.",
      "index.feature2.label": "Salinan kerja, bukan file kamu",
      "index.feature2.text": "Setiap upload dibuatkan salinan di cloud. Yang kamu edit, filter, atau bersihkan adalah salinan itu — file lokalmu tetap seperti semula.",
      "index.feature3.label": "Ngobrol bebas",
      "index.feature3.text": "Tanya pola, minta ringkasan, minta diubah jadi outline slide — bukan sekadar preview tabel statis.",

      "dashboard.title": "Ruang unggah",
      "dashboard.sub": "Setiap file yang kamu unggah dibuatkan salinan kerja di cloud.",
      "dashboard.dropzone.title": "Tarik file .xlsx ke sini",
      "dashboard.dropzone.sub": "atau klik untuk memilih dari komputer",
      "dashboard.filelist.title": "File kamu di sesi ini",
      "dashboard.filelist.empty": "Belum ada file. Upload di atas untuk mulai.",
      "dashboard.filelist.open": "Buka",
      "dashboard.filelist.delete": "Hapus",
      "dashboard.status.processing": "Memproses {name} ...",
      "common.loading": "Memuat data...",
      "dashboard.status.success": "Berhasil: {count} sheet terbaca. Membuka ruang kerja...",
      "dashboard.filelist.meta": "{count} sheet · diunggah {date}",

      "workspace.btnUpload": "+ Unggah file",
      "workspace.upload.title": "Unggah file Excel",
      "workspace.upload.historyTitle": "File yang pernah diunggah",
      "workspace.upload.current": "Sedang dibuka",
      "workspace.upload.tabFile": "File",
      "workspace.upload.tabLink": "Link spreadsheet",
      "workspace.upload.linkPlaceholder": "Tempel link Google Sheets (publik) atau URL file .xlsx...",
      "workspace.upload.linkSubmit": "Proses link",
      "workspace.upload.linkProcessing": "Memproses link...",
      "workspace.btnReset": "Reset ke asli",
      "workspace.btnSlides": "Jadikan slide",
      "workspace.btnDownload": "Unduh salinan",
      "workspace.toolbar.placeholder": "Pilih aksi...",
      "workspace.toolbar.dropna": "Buang baris kosong",
      "workspace.toolbar.dedupe": "Buang duplikat",
      "workspace.toolbar.sort": "Urutkan kolom",
      "workspace.toolbar.filter": "Filter baris",
      "workspace.toolbar.castnumeric": "Ubah ke angka",
      "workspace.toolbar.dropcolumn": "Hapus kolom",
      "workspace.toolbar.apply": "Terapkan",
      "workspace.chat.placeholder": "Tanya soal datamu...",
      "workspace.chat.send": "Kirim",
      "workspace.tabs.chat": "Chat",
      "workspace.tabs.formula": "Rumus",
      "workspace.formula.empty": "Klik sel di tabel untuk melihat rumus dan alur datanya di sini. Shift+klik untuk rentang, Ctrl/Cmd+klik untuk beberapa sel, klik judul kolom atau nomor baris untuk memilih satu kolom/baris penuh.",
      "workspace.formula.selected": "{n} sel dipilih · {f} rumus",
      "workspace.formula.noneInSheet": "Tidak ada rumus terdeteksi di sheet ini. Rumus hanya terbaca dari file .xlsx/.xlsm (format .xls lama tidak menyimpannya).",
      "workspace.formula.badgeFormula": "Rumus",
      "workspace.formula.badgeStatic": "Nilai statis",
      "workspace.formula.badgePlain": "Data biasa",
      "workspace.formula.badgeUnknown": "Tidak pasti",
      "workspace.formula.result": "Hasil",
      "workspace.formula.value": "Nilai",
      "workspace.formula.deps": "Bahan rumus (klik untuk tandai di tabel)",
      "workspace.formula.depsRow": "baris {r}",
      "workspace.formula.depsRows": "baris {a}–{b}",
      "workspace.formula.depsOutside": "di luar area data",
      "workspace.formula.otherSheet": "Rumus ini juga merujuk sheet lain.",
      "workspace.formula.staticNote": "Kolom {col} umumnya berisi rumus, tapi sel ini nilai manual/statis.",
      "workspace.formula.dirtyNote": "Data sheet ini sudah diurutkan/difilter, jadi posisi baris berubah dan rumus per-sel tidak bisa dipastikan. Kolom {col} aslinya {pct}% berisi rumus (mis. {sample}). Gunakan Reset untuk kembali ke data asli.",
      "workspace.formula.more": "+{n} sel lain tidak ditampilkan — pilih rentang yang lebih kecil.",
      "workspace.formula.dirtyFlag": "data sudah dimanipulasi",
      "workspace.chat.thinking": "Berpikir...",
      "workspace.chat.sheetsUsed": "Sheet dianalisis: {sheets}",
      "workspace.chat.errorPrefix": "Gagal",
      "workspace.chat.errorAnswer": "Gagal mendapat jawaban",
      "workspace.pager.info": "Hal {page} / {total} · {rows} baris",
      "workspace.chat.intro": "Halo, tanya apa saja soal data yang kamu unggah — pola, ringkasan, atau minta diproses jadi bentuk lain.",
      "workspace.pager.prev": "‹ Sebelumnya",
      "workspace.pager.next": "Selanjutnya ›",
      "workspace.slide.title": "Outline slide dari data ini",
      "workspace.slide.topicPlaceholder": "Topik slide, mis. 'Ringkasan penjualan Q3'",
      "workspace.slide.generate": "Buat outline",
      "workspace.slide.close": "Tutup",
    },
    en: {
      "nav.home": "Home",
      "nav.dashboard": "Dashboard",
      "footer.tagline": "Chat with your Excel files, without ever touching the originals.",
      "footer.stack": "Flask · Neon Postgres · Gemini Flash / Groq",

      "index.hero.title": "Chat with<br>your Excel files.",
      "index.hero.lede": "Upload one sheet or dozens, and the system tidies the data first. Ask anything about it, tweak the numbers, turn results into slides — the file on your computer is never touched.",
      "index.hero.cta": "Open Dashboard",
      "index.section.howitworks": "How it works",
      "index.feature1.label": "Any sheet layout",
      "index.feature1.text": "One sheet or dozens at once, even files with a title or logo sitting above the table — the header row is detected automatically.",
      "index.feature2.label": "A working copy, not your file",
      "index.feature2.text": "Every upload gets a cloud copy. Whatever you edit, filter, or clean is that copy — your local file stays exactly as it was.",
      "index.feature3.label": "Chat freely",
      "index.feature3.text": "Ask about patterns, request summaries, turn it into a slide outline — not just a static table preview.",

      "dashboard.title": "Upload space",
      "dashboard.sub": "Every file you upload gets a working copy in the cloud.",
      "dashboard.dropzone.title": "Drag an .xlsx file here",
      "dashboard.dropzone.sub": "or click to choose one from your computer",
      "dashboard.filelist.title": "Your files this session",
      "dashboard.filelist.empty": "No files yet. Upload one above to get started.",
      "dashboard.filelist.open": "Open",
      "dashboard.filelist.delete": "Delete",
      "dashboard.status.processing": "Processing {name} ...",
      "common.loading": "Loading data...",
      "dashboard.status.success": "Success: {count} sheet(s) read. Opening workspace...",
      "dashboard.filelist.meta": "{count} sheet(s) · uploaded {date}",

      "workspace.btnUpload": "+ Upload file",
      "workspace.upload.title": "Upload an Excel file",
      "workspace.upload.historyTitle": "Previously uploaded files",
      "workspace.upload.current": "Currently open",
      "workspace.upload.tabFile": "File",
      "workspace.upload.tabLink": "Spreadsheet link",
      "workspace.upload.linkPlaceholder": "Paste a public Google Sheets link or a .xlsx file URL...",
      "workspace.upload.linkSubmit": "Process link",
      "workspace.upload.linkProcessing": "Processing link...",
      "workspace.btnReset": "Reset to original",
      "workspace.btnSlides": "Turn into slides",
      "workspace.btnDownload": "Download copy",
      "workspace.toolbar.placeholder": "Choose an action...",
      "workspace.toolbar.dropna": "Drop empty rows",
      "workspace.toolbar.dedupe": "Drop duplicates",
      "workspace.toolbar.sort": "Sort by column",
      "workspace.toolbar.filter": "Filter rows",
      "workspace.toolbar.castnumeric": "Convert to numbers",
      "workspace.toolbar.dropcolumn": "Drop column",
      "workspace.toolbar.apply": "Apply",
      "workspace.chat.placeholder": "Ask about your data...",
      "workspace.chat.send": "Send",
      "workspace.tabs.chat": "Chat",
      "workspace.tabs.formula": "Formulas",
      "workspace.formula.empty": "Click a cell in the table to see its formula and data flow here. Shift+click for a range, Ctrl/Cmd+click for multiple cells, click a column title or row number to select a whole column/row.",
      "workspace.formula.selected": "{n} cells selected · {f} formulas",
      "workspace.formula.noneInSheet": "No formulas detected in this sheet. Formulas are only readable from .xlsx/.xlsm files (legacy .xls does not store them).",
      "workspace.formula.badgeFormula": "Formula",
      "workspace.formula.badgeStatic": "Static value",
      "workspace.formula.badgePlain": "Plain data",
      "workspace.formula.badgeUnknown": "Uncertain",
      "workspace.formula.result": "Result",
      "workspace.formula.value": "Value",
      "workspace.formula.deps": "Formula inputs (click to mark in table)",
      "workspace.formula.depsRow": "row {r}",
      "workspace.formula.depsRows": "rows {a}–{b}",
      "workspace.formula.depsOutside": "outside the data area",
      "workspace.formula.otherSheet": "This formula also references another sheet.",
      "workspace.formula.staticNote": "Column {col} mostly contains formulas, but this cell is a manual/static value.",
      "workspace.formula.dirtyNote": "This sheet was sorted/filtered, so row positions changed and per-cell formulas can't be confirmed. Column {col} originally had {pct}% formulas (e.g. {sample}). Use Reset to return to the original data.",
      "workspace.formula.more": "+{n} more cells not shown — select a smaller range.",
      "workspace.formula.dirtyFlag": "data was manipulated",
      "workspace.chat.thinking": "Thinking...",
      "workspace.chat.sheetsUsed": "Sheets analyzed: {sheets}",
      "workspace.chat.errorPrefix": "Failed",
      "workspace.chat.errorAnswer": "Failed to get an answer",
      "workspace.pager.info": "Page {page} / {total} · {rows} rows",
      "workspace.chat.intro": "Hi — ask anything about the data you uploaded: patterns, summaries, or ask to turn it into another format.",
      "workspace.pager.prev": "‹ Previous",
      "workspace.pager.next": "Next ›",
      "workspace.slide.title": "Slide outline from this data",
      "workspace.slide.topicPlaceholder": "Slide topic, e.g. 'Q3 sales summary'",
      "workspace.slide.generate": "Generate outline",
      "workspace.slide.close": "Close",
    },
  };

  function applyLang(lang) {
    const dict = DICT[lang] || DICT.id;

    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (!(key in dict)) return;
      if (el.hasAttribute("data-i18n-html")) {
        el.innerHTML = dict[key];
      } else {
        el.textContent = dict[key];
      }
    });

    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key in dict) el.setAttribute("placeholder", dict[key]);
    });

    const langCurrent = document.getElementById("langCurrent");
    if (langCurrent) langCurrent.textContent = lang.toUpperCase();

    document.documentElement.setAttribute("lang", lang);

    // Beri tahu skrip lain (mis. workspace.js) supaya teks yang dibuat lewat JS ikut diterjemahkan.
    window.dispatchEvent(new CustomEvent("mejadata:langchange", { detail: { lang } }));
  }

  window.MejaDataI18n = {
    dict: DICT,
    t(key, lang) {
      const l = lang || localStorage.getItem(STORAGE_KEY) || "id";
      return (DICT[l] || DICT.id)[key] || key;
    },
    apply: applyLang,
    getLang() {
      return localStorage.getItem(STORAGE_KEY) || "id";
    },
  };

  const saved = localStorage.getItem(STORAGE_KEY) || "id";
  applyLang(saved);

  const btn = document.getElementById("langToggle");
  if (btn) {
    btn.addEventListener("click", () => {
      const current = localStorage.getItem(STORAGE_KEY) || "id";
      const next = current === "id" ? "en" : "id";
      localStorage.setItem(STORAGE_KEY, next);
      applyLang(next);
    });
  }
})();
