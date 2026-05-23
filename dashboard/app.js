// Haber Botu Dashboard - cluster + tarih aralik + pagination

const PAGE_SIZE = 20;

const state = {
  news: [],
  sources: [],
  activeSource: null,
  activeLean: "all",
  dateFrom: null,    // Date | null
  dateTo: null,      // Date | null
  activeQuick: 24,   // 0 (Tumu) | 1 | 6 | 24 | 168 | 720 | null (ozel)
  sortBy: "recent",
  expanded: new Set(),
  visibleCount: PAGE_SIZE,
  password: localStorage.getItem("dashboard_password") || "",
};

const LEAN_LABEL = { left: "SOL", neutral: "NÖTR", right: "SAĞ" };
const LEAN_SHORT = { left: "Sol", neutral: "Nötr", right: "Sağ" };

// ----- API -----
async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`GET ${path}: ${r.status}`);
  return r.json();
}

async function apiAuth(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${state.password}` },
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

// ----- Filtreleme -----
function dateRange() {
  // (from, to) Date olarak. Hicbiri yoksa, null donduk -> filtreleme yok.
  if (state.dateFrom || state.dateTo) {
    return [state.dateFrom, state.dateTo];
  }
  if (state.activeQuick === 0) return [null, null];
  if (state.activeQuick == null) return [null, null];
  const to = new Date();
  const from = new Date(Date.now() - state.activeQuick * 3600 * 1000);
  return [from, to];
}

function filteredNews() {
  let items = state.news;
  const [from, to] = dateRange();
  if (from || to) {
    items = items.filter(n => {
      const t = Date.parse(n.ts);
      if (isNaN(t)) return false;
      if (from && t < from.getTime()) return false;
      if (to && t > to.getTime()) return false;
      return true;
    });
  }
  if (state.activeSource) items = items.filter(n => n.source === state.activeSource);
  return items;
}

function clusterStories(items) {
  const map = new Map();
  for (const n of items) {
    const cid = n.cluster_id || `solo:${n.link}`;
    if (!map.has(cid)) map.set(cid, []);
    map.get(cid).push(n);
  }
  const stories = [];
  for (const [cid, members] of map.entries()) {
    members.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const top = members[0];
    const leanCounts = { left: 0, neutral: 0, right: 0 };
    for (const m of members) {
      const l = m.lean && leanCounts.hasOwnProperty(m.lean) ? m.lean : "neutral";
      leanCounts[l]++;
    }
    const total = members.length || 1;
    const latestTs = members.map(m => m.ts).sort().reverse()[0];
    const leanPct = {
      left: Math.round((leanCounts.left / total) * 100),
      neutral: Math.round((leanCounts.neutral / total) * 100),
      right: Math.round((leanCounts.right / total) * 100),
    };
    // dominant lean (en yuksek count)
    const dom = Object.entries(leanCounts).reduce((a, b) => (b[1] > a[1] ? b : a))[0];
    stories.push({ id: cid, top, members, leanCounts, leanPct, latestTs, dom });
  }

  let filtered = stories;
  if (state.activeLean !== "all") {
    filtered = stories.filter(s => s.dom === state.activeLean);
  }

  if (state.sortBy === "recent") {
    filtered.sort((a, b) => (b.latestTs || "").localeCompare(a.latestTs || ""));
  } else if (state.sortBy === "coverage") {
    filtered.sort((a, b) => b.members.length - a.members.length || (b.latestTs || "").localeCompare(a.latestTs || ""));
  } else if (state.sortBy === "score") {
    filtered.sort((a, b) => (b.top.score ?? 0) - (a.top.score ?? 0));
  }
  return filtered;
}

// ----- Render -----
function render() {
  renderSources();
  renderStats();
  renderStories();
}

function renderSources() {
  const ul = document.getElementById("source-list");
  ul.innerHTML = "";
  const filtered = filteredNews();
  const countByLabel = new Map();
  for (const n of filtered) countByLabel.set(n.source, (countByLabel.get(n.source) || 0) + 1);

  const allLi = document.createElement("li");
  allLi.className = "source-item" + (state.activeSource === null ? " active" : "");
  allLi.innerHTML = `<span class="name">Tümü</span><span class="count">${filtered.length}</span>`;
  allLi.onclick = () => { state.activeSource = null; resetPage(); render(); };
  ul.appendChild(allLi);

  for (const src of state.sources) {
    const count = countByLabel.get(src.label) || 0;
    const li = document.createElement("li");
    li.className = "source-item" + (state.activeSource === src.label ? " active" : "");
    li.innerHTML = `
      <span class="name">
        <span class="dot lean-${src.default_lean}"></span>
        ${escape(src.label)}
        ${src.builtin ? "" : '<span class="custom-tag">özel</span>'}
      </span>
      <span class="count">${count}</span>
      ${src.builtin ? "" : `<button class="delete" title="Sil" aria-label="Sil">✕</button>`}
    `;
    li.querySelector(".name").onclick = () => { state.activeSource = src.label; resetPage(); render(); };
    li.querySelector(".count").onclick = () => { state.activeSource = src.label; resetPage(); render(); };
    const del = li.querySelector(".delete");
    if (del) del.onclick = (e) => { e.stopPropagation(); deleteFeed(src.url, src.label); };
    ul.appendChild(li);
  }
  document.getElementById("source-count").textContent = `${state.sources.length} aktif`;
}

function renderStats() {
  const panel = document.getElementById("stats-panel");
  const items = filteredNews();
  const stories = clusterStories(items);
  const leanTotal = { left: 0, neutral: 0, right: 0 };
  for (const n of items) if (leanTotal.hasOwnProperty(n.lean)) leanTotal[n.lean]++;
  const total = items.length || 1;
  panel.innerHTML = `
    <div class="panel-head"><h2>Özet</h2></div>
    <div class="stat-row"><span class="label">Hikayeler</span><span class="value">${stories.length}</span></div>
    <div class="stat-row"><span class="label">Toplam haber</span><span class="value">${items.length}</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-left"></span>Sol</span><span class="value">${Math.round(leanTotal.left/total*100)}%</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-neutral"></span>Nötr</span><span class="value">${Math.round(leanTotal.neutral/total*100)}%</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-right"></span>Sağ</span><span class="value">${Math.round(leanTotal.right/total*100)}%</span></div>
  `;
}

function renderStories() {
  const container = document.getElementById("story-container");
  const all = clusterStories(filteredNews());
  const visible = all.slice(0, state.visibleCount);

  document.getElementById("feed-title").textContent =
    state.activeSource ? `Hikayeler — ${state.activeSource}` : "Hikayeler";
  document.getElementById("feed-count").textContent =
    `${all.length} hikaye · ${filteredNews().length} haber`;

  if (all.length === 0) {
    container.innerHTML = `<div class="empty">Bu filtreyle eşleşen haber yok.<br><span class="small">Tarih aralığını veya yön filtresini değiştirmeyi dene.</span></div>`;
    document.getElementById("load-more-wrap").classList.add("hidden");
    return;
  }

  container.innerHTML = visible.map((s, idx) => renderStoryCard(s, idx === 0)).join("");

  container.querySelectorAll(".story-card").forEach(card => {
    const id = card.dataset.storyId;
    if (state.expanded.has(id)) card.classList.add("expanded");
    const toggle = card.querySelector(".toggle");
    if (toggle) {
      toggle.onclick = () => {
        if (state.expanded.has(id)) state.expanded.delete(id);
        else state.expanded.add(id);
        card.classList.toggle("expanded");
      };
    }
  });

  const lmWrap = document.getElementById("load-more-wrap");
  if (visible.length < all.length) {
    lmWrap.classList.remove("hidden");
    document.getElementById("load-more").textContent =
      `Daha fazla göster (${all.length - visible.length} kaldı)`;
  } else {
    lmWrap.classList.add("hidden");
  }
}

function renderStoryCard(s, isFirst) {
  const top = s.top;
  const topic = topicLabel(top.title, top.summary_tr || "");
  const topicKey = topic.toLowerCase().replace(/ö/g, "o").replace(/ü/g, "u").replace(/ı/g, "i");
  const leanBar = renderLeanBar(s.leanPct);

  const blindspot = s.members.length >= 3 && (
    s.leanPct.left >= 80 || s.leanPct.right >= 80 || s.leanPct.neutral >= 90
  );

  const coverageRows = s.members.map(m => `
    <div class="coverage-row">
      <span class="coverage-source"><span class="dot lean-${m.lean || 'neutral'}"></span>${escape(m.source || "—")}</span>
      <div class="coverage-headline">
        <a href="${escape(m.link)}" target="_blank" rel="noopener">${escape(m.title)}</a>
        ${m.summary_tr ? `<span class="source-summary">${escape(m.summary_tr)}</span>` : ""}
      </div>
      <span class="lean-tag lean-${m.lean || 'neutral'}">${LEAN_SHORT[m.lean] || "Nötr"}</span>
    </div>
  `).join("");

  // source-dots: ilk 5 kaynagin lean dot'lari
  const sourceDots = s.members.slice(0, 5).map(m =>
    `<span class="src-dot lean-${m.lean || 'neutral'}"></span>`
  ).join("");

  return `
    <article class="story-card dom-${s.dom} ${isFirst ? 'featured' : ''}" data-story-id="${escape(s.id)}">
      <div class="story-meta">
        <span class="topic-chip topic-${topicKey}">${escape(topic)}</span>
        <span class="time-ago">${formatTime(s.latestTs)}</span>
        ${blindspot ? '<span class="blindspot-badge">Tek taraflı</span>' : ''}
      </div>
      <h3 class="story-title">
        <a href="${escape(top.link)}" target="_blank" rel="noopener">${escape(top.title)}</a>
      </h3>
      ${top.summary_tr ? `<p class="story-summary">${escape(top.summary_tr)}</p>` : ""}
      ${leanBar}
      <div class="coverage-summary">
        ${s.leanPct.left > 0 ? `<span class="cov-stat"><span class="dot lean-left"></span><span class="pct">${s.leanPct.left}%</span> sol</span>` : ""}
        ${s.leanPct.neutral > 0 ? `<span class="cov-stat"><span class="dot lean-neutral"></span><span class="pct">${s.leanPct.neutral}%</span> nötr</span>` : ""}
        ${s.leanPct.right > 0 ? `<span class="cov-stat"><span class="dot lean-right"></span><span class="pct">${s.leanPct.right}%</span> sağ</span>` : ""}
      </div>
      <div class="story-footer">
        <span class="source-count">${s.members.length} kaynak<span class="source-dots">${sourceDots}</span></span>
        ${s.members.length > 1 ? `<button class="toggle">Tüm başlıklar <span class="toggle-icon">▾</span></button>` : ""}
      </div>
      ${s.members.length > 1 ? `<div class="coverage-list">${coverageRows}</div>` : ""}
    </article>
  `;
}

function renderLeanBar(pct) {
  const segs = [
    { key: "left",    pct: pct.left },
    { key: "neutral", pct: pct.neutral },
    { key: "right",   pct: pct.right },
  ];
  return `
    <div class="lean-bar">
      ${segs.map(s => s.pct > 0
        ? `<div class="lean-bar-segment lean-${s.key}" style="flex:${s.pct}">${s.pct}%</div>`
        : `<div class="lean-bar-segment empty"></div>`
      ).join("")}
    </div>
  `;
}

function topicLabel(title, summary) {
  const blob = (title + " " + summary).toLowerCase();
  if (/\b(fed|ecb|imf|nato|putin|trump|xi |netanyahu|iran|ukrayna|gazze|israil|white house|sanctions|yaptirim)\b/.test(blob)) return "Küresel";
  if (/\b(faiz|enflasyon|tcmb|dolar|euro|borsa|bist|tahvil|butce|hazine|maliye|vergi|buyume|issizlik|petrol|brent|altin)\b/.test(blob)) return "Ekonomi";
  if (/\b(tbmm|meclis|chp|akp|mhp|iyi parti|deva|saadet|erdogan|babacan|ozel|imamoglu|atama|istifa|yargi|mahkeme)\b/.test(blob)) return "Siyaset";
  return "Genel";
}

function resetPage() { state.visibleCount = PAGE_SIZE; }

// ----- Date inputs -----
function dateToLocalInput(d) {
  // Date -> "YYYY-MM-DDTHH:MM" (local timezone)
  if (!d) return "";
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function syncDateInputsFromState() {
  const [from, to] = dateRange();
  document.getElementById("date-from").value = state.dateFrom ? dateToLocalInput(state.dateFrom) : "";
  document.getElementById("date-to").value = state.dateTo ? dateToLocalInput(state.dateTo) : "";
}

// ----- Custom feeds -----
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

// ----- Password -----
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
    return r.status !== 401;
  } catch { return false; }
}

// ----- Helpers -----
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
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} gün önce`;
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 2800);
}

// ----- Events -----
document.getElementById("time-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  document.querySelectorAll("#time-filters .chip").forEach(c => c.classList.remove("active"));
  btn.classList.add("active");
  state.activeQuick = Number(btn.dataset.time);
  state.dateFrom = null;
  state.dateTo = null;
  document.getElementById("date-from").value = "";
  document.getElementById("date-to").value = "";
  resetPage();
  render();
});

document.getElementById("lean-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  document.querySelectorAll("#lean-filters .chip").forEach(c => c.classList.remove("active"));
  btn.classList.add("active");
  state.activeLean = btn.dataset.lean;
  resetPage();
  render();
});

["date-from", "date-to"].forEach(id => {
  document.getElementById(id).addEventListener("change", () => {
    const fromVal = document.getElementById("date-from").value;
    const toVal = document.getElementById("date-to").value;
    state.dateFrom = fromVal ? new Date(fromVal) : null;
    state.dateTo = toVal ? new Date(toVal) : null;
    if (state.dateFrom || state.dateTo) {
      state.activeQuick = null;
      document.querySelectorAll("#time-filters .chip").forEach(c => c.classList.remove("active"));
    }
    resetPage();
    render();
  });
});

document.getElementById("date-clear").addEventListener("click", () => {
  state.dateFrom = null;
  state.dateTo = null;
  document.getElementById("date-from").value = "";
  document.getElementById("date-to").value = "";
  state.activeQuick = 24;
  document.querySelectorAll("#time-filters .chip").forEach(c => c.classList.remove("active"));
  const q = document.querySelector('#time-filters .chip[data-time="24"]');
  if (q) q.classList.add("active");
  resetPage();
  render();
});

document.getElementById("sort-by").addEventListener("change", (e) => {
  state.sortBy = e.target.value;
  resetPage();
  render();
});

document.getElementById("load-more").addEventListener("click", () => {
  state.visibleCount += PAGE_SIZE;
  render();
});

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

// ----- Init -----
loadData();
setInterval(loadData, 5 * 60 * 1000);
