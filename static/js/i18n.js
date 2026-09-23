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
  }

  window.MejaDataI18n = {
    dict: DICT,
    t(key, lang) {
      const l = lang || localStorage.getItem(STORAGE_KEY) || "id";
      return (DICT[l] || DICT.id)[key] || key;
    },
    apply: applyLang,
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
