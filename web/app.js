"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const state = {
  cards: [],
  cardById: {},
  series: [],
  maxCount: 10,
  counts: {},
  dirty: false,
  me: null,
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

const cardName = (id) => {
  const c = state.cardById[id];
  return c ? c.name_zh || c.name_en || id : id;
};

/* ---------- 檢視切換 ---------- */

function show(view) {
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== `view-${view}`;
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.view === view);
  if (view === "matches") loadMatches();
  if (view === "clan") loadClan();
  if (view === "villages") renderVillages();
}

for (const b of document.querySelectorAll(".tabs button")) {
  b.addEventListener("click", () => show(b.dataset.view));
}

/* ---------- 登入 ---------- */

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#loginError");
  err.hidden = true;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    await api("/api/players/verify", {
      method: "POST",
      body: JSON.stringify({ tag: $("#tagInput").value, token: $("#tokenInput").value }),
    });
    $("#tokenInput").value = "";
    await boot();
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  location.reload();
});

$("#villagePicker").addEventListener("change", async (e) => {
  await api("/api/me/active", { method: "POST", body: JSON.stringify({ tag: e.target.value }) });
  await loadCollection();
  show("collection");
});

/* ---------- 收藏表 ---------- */

function renderCollection() {
  const body = $("#collectionBody");
  body.textContent = "";

  for (const s of state.series) {
    const card = el("div", "card");
    const title = el("h2", "series-title");
    const sw = el("span", `swatch`);
    sw.style.background = `var(--${s.key})`;
    title.append(sw, el("span", null, `${s.name_zh}卡牌`));
    card.append(title);

    const grid = el("div", "grid");
    for (const c of state.cards.filter((c) => c.series === s.key)) {
      grid.append(renderSlot(c));
    }
    card.append(grid);
    body.append(card);
  }
  updateProgress();
}

function renderSlot(c) {
  const n = state.counts[c.id] || 0;
  const slot = el("div", `slot ${c.series}`);
  slot.dataset.id = c.id;

  const name = el("div", "name" + (c.confirmed ? "" : " unconfirmed"), cardName(c.id));
  if (!c.confirmed) name.title = "名稱尚未與遊戲畫面核對";

  const sel = el("select");
  for (let i = 0; i <= state.maxCount; i++) {
    const o = el("option", null, i === 0 ? "缺" : String(i));
    o.value = String(i);
    if (i === n) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => {
    state.counts[c.id] = Number(sel.value);
    state.dirty = true;
    paintSlot(slot, Number(sel.value));
    updateProgress();
    markDirty();
  });

  slot.append(name, sel);
  paintSlot(slot, n);
  return slot;
}

function paintSlot(slot, n) {
  slot.classList.toggle("missing", n === 0);
  slot.classList.toggle("spare", n >= 2);
}

function updateProgress() {
  const row = $("#seriesProgress");
  row.textContent = "";
  for (const s of state.series) {
    const ids = state.cards.filter((c) => c.series === s.key);
    const have = ids.filter((c) => (state.counts[c.id] || 0) > 0).length;
    row.append(el("div", `pill ${s.key}`, `${s.name_zh} ${have}/${ids.length}`));
  }
  const total = state.cards.length;
  const have = state.cards.filter((c) => (state.counts[c.id] || 0) > 0).length;
  row.append(el("div", "pill total", `共 ${have}/${total}`));
}

function markDirty() {
  $("#saveState").textContent = state.dirty ? "尚未儲存" : "已儲存";
  $("#saveBtn").disabled = !state.dirty;
}

$("#saveBtn").addEventListener("click", async () => {
  $("#saveBtn").disabled = true;
  $("#saveState").textContent = "儲存中…";
  try {
    await api("/api/collection", { method: "PUT", body: JSON.stringify({ counts: state.counts }) });
    state.dirty = false;
    markDirty();
    $("#saveState").textContent = "已儲存";
  } catch (e) {
    $("#saveState").textContent = `儲存失敗：${e.message}`;
    $("#saveBtn").disabled = false;
  }
});

window.addEventListener("beforeunload", (e) => {
  if (state.dirty) e.preventDefault();
});

async function loadCollection() {
  const data = await api("/api/collection");
  state.counts = data.counts || {};
  state.dirty = false;
  renderCollection();
  markDirty();
}

/* ---------- 配對 ---------- */

$("#sameClanOnly").addEventListener("change", loadMatches);

async function loadMatches() {
  const body = $("#matchBody");
  body.textContent = "";
  body.append(el("div", "empty", "計算中…"));

  const same = $("#sameClanOnly").checked ? "1" : "0";
  let data;
  try {
    data = await api(`/api/matches?same_clan=${same}`);
  } catch (e) {
    body.textContent = "";
    body.append(el("div", "empty", `讀取失敗：${e.message}`));
    return;
  }

  $("#syncWarn").hidden = data.clan_sync_ok !== false;
  $("#matchSummary").textContent =
    `已收集 ${data.collected}/${state.cards.length}　缺 ${data.missing.length} 張　` +
    `可送出 ${data.spares.length} 種　資料庫共 ${data.total_players} 人`;

  body.textContent = "";
  if (!data.matches.length) {
    body.append(
      el("div", "empty", data.total_players <= 1
        ? "資料庫裡還沒有其他人。把網址分享給部落成員，等他們建好表就會出現配對。"
        : "目前沒有可成立的交換。等對方更新收藏後再回來看看。")
    );
    return;
  }
  for (const m of data.matches) body.append(renderMatch(m));
}

const KIND_LABEL = { mutual: "互利互換", incoming: "我受益", outgoing: "我幫人" };
const INITIATOR_LABEL = {
  either: "誰先開口都可以",
  me: "要由你在部落聊天室發起",
  them: "要由對方發起",
};

function renderMatch(m) {
  const card = el("div", `card match ${m.kind}`);

  const head = el("div", "match-head");
  head.append(el("span", "who", m.name));
  head.append(el("span", `tag ${m.kind}`, KIND_LABEL[m.kind]));
  if (!m.same_clan) head.append(el("span", "tag offclan", "不同部落"));
  card.append(head);

  const sub = el("p", "hint");
  sub.textContent =
    `${m.clan_name || "無部落"}　${INITIATOR_LABEL[m.initiator]}` +
    (m.gain ? `　可補你 ${m.gain} 張` : "") +
    (m.help ? `　可補對方 ${m.help} 張` : "");
  card.append(sub);

  for (const s of m.series) {
    const box = el("div", "swap");
    const meta = state.series.find((x) => x.key === s.series);
    box.append(el("div", "lbl", `${meta ? meta.name_zh : s.series}　${KIND_LABEL[s.kind]}`));

    box.append(chipRow("你送出", s.i_give, "give"));
    box.append(chipRow("你收到", s.i_get, "get"));
    card.append(box);
  }
  return card;
}

function chipRow(label, ids, cls) {
  const wrap = el("div");
  wrap.append(el("div", "lbl", label));
  const chips = el("div", "chips");
  for (const id of ids) chips.append(el("span", `chip ${cls}`, cardName(id)));
  wrap.append(chips);
  return wrap;
}

/* ---------- 部落總覽 ---------- */

async function loadClan() {
  const body = $("#clanBody");
  body.textContent = "";
  body.append(el("div", "empty", "讀取中…"));
  let data;
  try {
    data = await api("/api/clan/overview");
  } catch (e) {
    body.textContent = "";
    body.append(el("div", "empty", `讀取失敗：${e.message}`));
    return;
  }

  body.textContent = "";
  const card = el("div", "card");
  const table = el("table");
  const thead = el("thead");
  const hr = el("tr");
  for (const h of ["玩家", "部落", "已收集"]) hr.append(el("th", null, h));
  thead.append(hr);
  table.append(thead);

  const tbody = el("tbody");
  for (const p of data.players) {
    const tr = el("tr");
    tr.append(el("td", null, p.name));
    const clan = el("td", null, p.clan_name || "無部落");
    if (!p.same_clan) clan.style.color = "var(--warn)";
    tr.append(clan);
    tr.append(el("td", "num", p.has_data ? `${p.collected}/${p.total}` : "未建表"));
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);
  body.append(card);
}

/* ---------- 村莊管理 ---------- */

function renderVillages() {
  const list = $("#villageList");
  list.textContent = "";
  for (const p of state.me.players) {
    const card = el("div", "card");
    const row = el("div", "village-row");
    const who = el("div", "who");
    who.append(el("div", null, `${p.name}　${p.tag}`));
    who.append(el("div", "hint", p.clan_name || "無部落"));
    row.append(who);
    if (state.me.players.length > 1) {
      const btn = el("button", "ghost", "解除綁定");
      btn.addEventListener("click", async () => {
        if (!confirm(`解除綁定 ${p.name}？該村莊的收藏紀錄會一併刪除。`)) return;
        await api(`/api/players/${encodeURIComponent(p.tag)}`, { method: "DELETE" });
        await boot();
        show("villages");
      });
      row.append(btn);
    }
    card.append(row);
    list.append(card);
  }
}

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#addError");
  err.hidden = true;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    await api("/api/players/verify", {
      method: "POST",
      body: JSON.stringify({ tag: $("#addTag").value, token: $("#addToken").value }),
    });
    $("#addTag").value = "";
    $("#addToken").value = "";
    await boot();
    show("collection");
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 啟動 ---------- */

async function boot() {
  const meta = await api("/api/cards");
  state.cards = meta.cards;
  state.series = meta.series;
  state.maxCount = meta.max_count;
  state.cardById = Object.fromEntries(meta.cards.map((c) => [c.id, c]));

  const me = await api("/api/me");
  state.me = me;

  if (!me.logged_in) {
    $("#topbar").hidden = true;
    show("login");
    return;
  }

  $("#topbar").hidden = false;
  const picker = $("#villagePicker");
  picker.textContent = "";
  for (const p of me.players) {
    const o = el("option", null, `${p.name}（${p.clan_name || "無部落"}）`);
    o.value = p.tag;
    if (p.tag === me.active_tag) o.selected = true;
    picker.append(o);
  }

  await loadCollection();
  show("collection");
}

boot().catch((e) => {
  document.body.innerHTML = `<main><div class="card"><p class="error">啟動失敗：${e.message}</p></div></main>`;
});
