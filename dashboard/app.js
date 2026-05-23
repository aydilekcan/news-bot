// Haber Botu Dashboard - vanilla JS

const state = {
  news: [],             // [{title, link, source, summary_tr, lean, score, ts}, ...]
  sources: [],          // [{label, default_lean, builtin, url?}, ...]
  activeSource: null,   // null = tum kaynaklar
  activeLean: "all",    // all|left|neutral|right
  password: localStorage.getItem("dashboard_password") || "",
};

const LEAN_LABEL = { left: "Sol", neutral: "Nötr", right: "Sağ" };

// --- API ---
async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
  return r.json();
}

async function apiAuth(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${state.password}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) {
    state.password = "";
    localStorage.removeItem("dashboard_password");
    showPasswordModal();
    throw new Error("unauthorized");
  }
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${method} ${path}: ${r.status} ${txt}`);
  }
  return r.json();
}

// --- Veri ---
async function loadData() {
  try {
    const data = await apiGet("/api/data");
    state.news = data.news || [];
    state.sources = data.sources || [];
    document.getElementById("last-updated").textContent =
      `Son haber: ${data.updated_at ? formatTime(data.updated_at) : "henüz veri yok"}`;
    render();
  } catch (e) {
    toast("Veri yüklenemedi: " + e.message, "error");
  }
}

// --- Render ---
function render() {
  renderSources();
  renderNews();
}

function newsForSource(label) {
  return state.news.filter(n => n.source === label);
}

function renderSources() {
  const ul = document.getElementById("source-list");
  ul.innerHTML = "";

  // "Tümü"
  const allLi = document.createElement("li");
  allLi.className = "source-item" + (state.activeSource === null ? " active" : "");
  allLi.innerHTML = `
    <span class="name">📊 Tümü</span>
    <span class="count">${state.news.length}</span>
  `;
  allLi.onclick = () => { state.activeSource = null; render(); };
  ul.appendChild(allLi);

  for (const src of state.sources) {
    const count = newsForSource(src.label).length;
    const li = document.createElement("li");
    li.className = "source-item" + (state.activeSource === src.label ? " active" : "");
    li.innerHTML = `
      <span class="name">
        <span class="dot lean-${src.default_lean}"></span>
        ${escape(src.label)}
        ${src.builtin ? "" : '<span class="custom-tag">özel</span>'}
      </span>
      <span class="count">${count}</span>
      ${src.builtin ? "" : `<button class="delete" title="Sil" data-url="${escape(src.url)}">✕</button>`}
    `;
    li.querySelector(".name").onclick = () => { state.activeSource = src.label; render(); };
    li.querySelector(".count").onclick = () => { state.activeSource = src.label; render(); };
    const del = li.querySelector(".delete");
    if (del) {
      del.onclick = (e) => {
        e.stopPropagation();
        deleteFeed(src.url, src.label);
      };
    }
    ul.appendChild(li);
  }

  document.getElementById("source-count").textContent = `${state.sources.length} kaynak`;
}

function renderNews() {
  const container = document.getElementById("news-container");
  const title = document.getElementById("news-title");

  let items = state.news;
  if (state.activeSource) items = items.filter(n => n.source === state.activeSource);
  if (state.activeLean !== "all") items = items.filter(n => n.lean === state.activeLean);

  items = [...items].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));

  title.textContent = state.activeSource ? `Haberler — ${state.activeSource}` : "Tüm Haberler";
  document.getElementById("news-count").textContent = `${items.length} haber`;

  if (items.length === 0) {
    container.innerHTML = `<div class="empty">Bu filtreyle eşleşen haber yok.</div>`;
    return;
  }

  container.innerHTML = items.map(n => `
    <article class="news-card">
      <div class="news-head">
        <span class="source-badge"><span class="dot lean-${n.lean || 'neutral'}"></span>${escape(n.source || "—")}</span>
        <span class="lean-badge lean-${n.lean || 'neutral'}">${LEAN_LABEL[n.lean] || "Nötr"}</span>
        <span class="news-time">${formatTime(n.ts)}</span>
      </div>
      <h3 class="news-title"><a href="${escape(n.link)}" target="_blank" rel="noopener">${escape(n.title)}</a></h3>
      ${n.summary_tr ? `<p class="news-summary">${escape(n.summary_tr)}</p>` : ""}
      <div class="news-meta">
        <span class="score">Skor: ${n.score ?? "?"}</span>
        <a href="${escape(n.link)}" target="_blank" rel="noopener">Habere git →</a>
      </div>
    </article>
  `).join("");
}

// --- Lean filter chips ---
document.querySelectorAll(".lean-chip").forEach(chip => {
  chip.onclick = () => {
    document.querySelectorAll(".lean-chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    state.activeLean = chip.dataset.lean;
    render();
  };
});

// --- Feed ekle / sil ---
async function addFeed({ url, label, default_lean }) {
  if (!state.password) {
    pendingAction = () => addFeed({ url, label, default_lean });
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("POST", "/api/feeds", { url, label, default_lean });
    toast(`"${label || url}" eklendi`, "success");
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Eklenemedi: " + e.message, "error");
  }
}

async function deleteFeed(url, label) {
  if (!confirm(`"${label}" kaynak silinsin mi?`)) return;
  if (!state.password) {
    pendingAction = () => deleteFeed(url, label);
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("DELETE", "/api/feeds", { url });
    toast(`"${label}" silindi`, "success");
    if (state.activeSource === label) state.activeSource = null;
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Silinemedi: " + e.message, "error");
  }
}

// --- Sifre modal ---
let pendingAction = null;

function showPasswordModal() {
  document.getElementById("password-modal").classList.remove("hidden");
  document.getElementById("password-input").focus();
}

function hidePasswordModal() {
  document.getElementById("password-modal").classList.add("hidden");
  document.getElementById("password-input").value = "";
  document.getElementById("password-error").classList.add("hidden");
}

async function tryPassword(pw) {
  try {
    const r = await fetch("/api/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${pw}` },
      body: JSON.stringify({ url: "__ping__" }),
    });
    if (r.status === 401) return false;
    return true;
  } catch {
    return false;
  }
}

// --- Helpers ---
function escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatTime(iso) {
  if (!iso) return "?";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const diff = (new Date() - d) / 1000;
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 3000);
}

// --- Events ---
document.getElementById("add-feed-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = document.getElementById("feed-url").value.trim();
  const label = document.getElementById("feed-label").value.trim();
  const default_lean = document.getElementById("feed-lean").value;
  if (!url) return;
  document.getElementById("feed-url").value = "";
  document.getElementById("feed-label").value = "";
  addFeed({ url, label, default_lean });
});

document.getElementById("refresh").addEventListener("click", loadData);

document.getElementById("lock-btn").addEventListener("click", () => {
  state.password = "";
  localStorage.removeItem("dashboard_password");
  toast("Çıkış yapıldı.", "success");
});

document.getElementById("cancel-password").addEventListener("click", () => {
  hidePasswordModal();
  pendingAction = null;
});

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = document.getElementById("password-input").value;
  const ok = await tryPassword(pw);
  if (ok) {
    state.password = pw;
    localStorage.setItem("dashboard_password", pw);
    hidePasswordModal();
    const action = pendingAction;
    pendingAction = null;
    if (action) action();
  } else {
    document.getElementById("password-error").classList.remove("hidden");
  }
});

// --- Init ---
loadData();
setInterval(loadData, 5 * 60 * 1000);
