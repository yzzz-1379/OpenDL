/* OpenDL Catcher popup.
 *
 * 底栏的 6 个按钮各对应下面一个用注释包起来的功能块：
 *   [功能1] 页面聚合   [功能2] 批量下载   [功能3] 条件选择
 *   [功能4] 暂停嗅探   [功能5] 展开/折叠  [功能6] 媒体控制
 * 不要某个功能时：删 popup.html 里对应的按钮（和面板），再删掉这里对应的整段。
 */

const listEl = document.getElementById("list");
const stateEl = document.getElementById("state");
const tokenEl = document.getElementById("token");
const saveEl = document.getElementById("save");
const takeoverEl = document.getElementById("takeover");
const gearEl = document.getElementById("gear");
const settingsEl = document.getElementById("settings");
const bannerEl = document.getElementById("banner");
const backendEl = document.getElementById("backend");
const gopeedFields = document.getElementById("gopeed-fields");
const gopeedUrlEl = document.getElementById("gopeed-url");
const gopeedTokenEl = document.getElementById("gopeed-token");
const pairBlock = document.getElementById("pair-block");

const ui = {
  tabId: null,
  tabTitle: "",
  items: [],
  aggregate: false,   // [功能1]
  batch: false,       // [功能2]
  selected: new Set(),
  filter: "",         // [功能3]
  typeEnabled: new Map(), // [功能3] 类型过滤：type -> bool，缺省为 true
  collapsedTabs: new Set(), // [功能1] 折叠起来的标签页
};

/* ============================ 通用工具 ============================ */

function humanSize(bytes) {
  if (!bytes) return "未知";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

function fileName(url) {
  try {
    const path = new URL(url).pathname;
    return decodeURIComponent(path.split("/").pop() || "") || url;
  } catch (e) {
    return url;
  }
}

function kindLabel(kind) {
  return { MEDIA: "MEDIA", XHR: "XHR", FETCH: "FETCH", IMG: "IMG", OBJ: "OBJ", OTHER: "OTHER",
           media: "MEDIA", xhr: "XHR", fetch: "FETCH", blob: "BLOB", stream: "STREAM" }[kind] || kind;
}

function humanDuration(seconds) {
  if (!seconds || !isFinite(seconds)) return "—";
  const total = Math.round(seconds);
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function playable(url, mime) {
  if (!/^https?:/i.test(url)) return false;
  if (/mpegurl|dash\+xml/i.test(mime || "")) return false;
  return /^(video|audio)\//i.test(mime || "") || /\.(mp4|webm|ogg|m4a|mp3|wav)(\?|#|$)/i.test(url);
}

function detailRow(label, value) {
  const wrap = document.createElement("div");
  wrap.className = "detail-line";
  const key = document.createElement("span");
  key.className = "detail-key";
  key.textContent = label;
  const val = document.createElement("span");
  val.className = "detail-val";
  val.textContent = value;
  wrap.append(key, val);
  return wrap;
}

function refererOf(item) {
  return item.referer || item.pageUrl || (ui.tabTitle ? ui.tabTitle : "") || "";
}

/* ============================ 详情面板 ============================ */

function buildDetails(item, withVideo = true, target = null) {
  const box = target || document.createElement("div");
  box.className = "details";

  const referer = refererOf(item);
  box.append(detailRow("标题", item.title || fileName(item.url)));
  box.append(detailRow("MIME", item.mime || "未知"));

  let knownSize = Number(item.size) || 0;
  let knownDuration = Number(item.duration) || 0;
  const sizeLine = detailRow("大小", knownSize ? humanSize(knownSize) : "未知（未捕获到响应头）");
  const durationLine = detailRow("时长", knownDuration ? humanDuration(knownDuration) : "—");
  const resolutionLine = detailRow(
    "分辨率",
    item.width && item.height ? `${item.width}×${item.height}` : (item.mime === "audio/*" ? "音频" : "—")
  );
  const bitrateLine = detailRow("码率", "—");
  box.append(sizeLine, durationLine, resolutionLine, bitrateLine);

  const refreshBitrate = () => {
    bitrateLine.lastElementChild.textContent =
      knownSize && knownDuration ? `${((knownSize * 8) / knownDuration / 1000).toFixed(2)} kbps` : "—";
  };
  refreshBitrate();

  const urlLine = document.createElement("div");
  urlLine.className = "detail-url";
  urlLine.textContent = item.url;
  urlLine.title = item.url;
  box.append(urlLine);

  if (playable(item.url, item.mime)) {
    const actions = document.createElement("div");
    actions.className = "detail-actions";

    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "内嵌播放需要站点允许跨站请求";

    if (withVideo) {
      const video = document.createElement("video");
      video.className = "preview";
      video.controls = true;
      video.preload = "metadata";
      video.addEventListener("loadedmetadata", () => {
        if (!knownDuration && Number.isFinite(video.duration)) {
          knownDuration = video.duration;
          durationLine.lastElementChild.textContent = humanDuration(video.duration);
          refreshBitrate();
        }
      });
      video.addEventListener("error", () => {
        video.remove();
        hint.textContent = "内嵌播放被拒（站点校验来源），请用页面内预览。";
        hint.style.color = "#d29922";
      });
      box.append(video);
      chrome.runtime
        .sendMessage({ type: "arm", url: item.url, referer, cookie: item.cookie || "" })
        .then(() => {
          video.src = item.url;
        })
        .catch(() => {
          video.src = item.url;
        });
    }

    const preview = document.createElement("button");
    preview.textContent = "在页面内预览";
    preview.addEventListener("click", (event) => {
      event.stopPropagation();
      chrome.runtime.sendMessage({
        type: "preview-page",
        tabId: ui.tabId,
        frameId: item.frameId ?? 0,
        url: item.url,
      });
      window.close();
    });

    const open = document.createElement("button");
    open.className = "ghost";
    open.textContent = "新标签页打开";
    open.addEventListener("click", async (event) => {
      event.stopPropagation();
      await chrome.runtime
        .sendMessage({ type: "arm", url: item.url, referer, cookie: item.cookie || "" })
        .catch(() => {});
      chrome.tabs.create({ url: item.url });
    });

    actions.append(preview, open, hint);
    box.append(actions);
  }
  return box;
}

/* ============================ 列表渲染 ============================ */

/* [功能3] 资源类型：优先看扩展名，其次看 MIME 子类型 */
const KNOWN_TYPES = new Set([
  "mp4", "m3u8", "mpd", "ts", "m4s", "mkv", "webm", "flv", "mov",
  "mp3", "m4a", "aac", "ogg", "wav", "flac",
  "jpg", "jpeg", "png", "gif", "webp",
  "zip", "rar", "7z", "exe", "pdf", "json",
]);

function typeOf(item) {
  let path = item.url;
  try {
    path = new URL(item.url).pathname;
  } catch (e) {
    /* keep the raw url */
  }
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (KNOWN_TYPES.has(ext)) return ext;
  const mime = (item.mime || "").toLowerCase();
  if (mime.startsWith("video/")) return mime === "video/*" ? "video" : mime.slice(6);
  if (mime.startsWith("audio/")) return mime === "audio/*" ? "audio" : mime.slice(6);
  return "其他";
}

function typeVisible(type) {
  return ui.typeEnabled.has(type) ? ui.typeEnabled.get(type) : true;
}

function visibleItems() {
  let list = ui.items;
  if (ui.filter) {
    try {
      const re = new RegExp(ui.filter, "i");
      list = list.filter((item) => re.test(item.url));
    } catch (e) {
      /* invalid regex: ignore the filter */
    }
  }
  return list.filter((item) => typeVisible(typeOf(item)));
}

function makeSendButton(item) {
  const send = document.createElement("button");
  send.textContent = "下载";
  const blobLike = item.url.startsWith("blob:");
  send.disabled = blobLike;
  if (blobLike) send.title = "blob 流无法直接交给下载器";
  send.addEventListener("click", async (event) => {
    event.stopPropagation();
    await sendOne(item, send);
  });
  return send;
}

async function sendOne(item, button) {
  const label = button.textContent;
  button.disabled = true;
  button.classList.remove("err");
  button.textContent = "…";
  const reply = await chrome.runtime.sendMessage({
    type: "send",
    url: item.url,
    referer: refererOf(item),
    cookie: item.cookie || "",
  });
  if (reply && reply.ok) {
    button.textContent = "已发送";
  } else if (reply && reply.error === "launched") {
    button.textContent = "已启动";
  } else if (reply && reply.error === "pairing-token") {
    button.classList.add("err");
    button.textContent = "配对码 ×";
    button.disabled = false;
    refreshState();
  } else {
    // 连接失败（例如 Gopeed 未启动）也要红底 + 失败，和配对错误同一视觉语言
    button.classList.add("err");
    button.textContent = "失败";
    button.title = (reply && reply.error) || "发送失败";
    button.disabled = false;
    refreshState();
  }
  setTimeout(() => {
    if (button.isConnected) {
      button.textContent = label;
      button.disabled = false;
    }
  }, 2500);
}

function makeCopyButton(item) {
  const copy = document.createElement("button");
  copy.className = "ghost";
  copy.textContent = "复制";
  copy.addEventListener("click", async (event) => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(item.url);
      copy.textContent = "已复制";
      setTimeout(() => (copy.textContent = "复制"), 1200);
    } catch (e) {
      copy.textContent = "失败";
    }
  });
  return copy;
}

function buildRow(item) {
  const wrap = document.createElement("div");
  wrap.className = "item";

  const head = document.createElement("div");
  head.className = "row";
  head.title = "点击展开详情";

  // [功能2] 勾选框只在批量模式下出现
  if (ui.batch) {
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pick";
    pick.checked = ui.selected.has(item.url);
    pick.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleSelect(item.url, pick.checked);
    });
    head.append(pick);
  }

  const kind = document.createElement("span");
  kind.className = "kind " + String(item.kind || "").toLowerCase();
  kind.textContent = kindLabel(item.kind);

  const meta = document.createElement("div");
  meta.className = "meta";
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = fileName(item.url);
  name.title = item.url;
  if (ui.aggregate && item.tabTitle) {
    const badge = document.createElement("span");
    badge.className = "tab-badge";
    badge.textContent = item.tabTitle.slice(0, 40);
    name.append(badge);
  }
  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = `${item.mime || "未知类型"} · ${humanSize(item.size)}`;
  meta.append(name, sub);

  head.append(kind, meta, makeSendButton(item), makeCopyButton(item));

  const panel = document.createElement("div");
  panel.className = "details";
  panel.hidden = true;
  let built = false;

  const ensure = (withVideo) => {
    if (built) return;
    buildDetails(item, withVideo, panel);
    built = true;
  };

  head.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    wrap.classList.toggle("open", opening);
    if (opening) ensure(true);
  });

  wrap.append(head, panel);
  return wrap;
}

function rebuildTypeFilters() {
  const box = document.getElementById("type-filters");
  if (!box) return;
  const counts = new Map();
  for (const item of ui.items) {
    const type = typeOf(item);
    counts.set(type, (counts.get(type) || 0) + 1);
  }
  box.innerHTML = "";
  for (const [type, count] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = typeVisible(type);
    cb.value = type;
    cb.addEventListener("change", () => {
      ui.typeEnabled.set(type, cb.checked);
      render();
    });
    label.append(cb, document.createTextNode(` ${type} (${count})`));
    box.append(label);
  }
}

function render() {
  rebuildTypeFilters();
  const items = visibleItems();
  if (!items.length) {
    listEl.innerHTML = `<div class="empty">${
      ui.items.length ? "没有符合筛选条件的资源" : "当前页面还没有捕获到媒体"
    }</div>`;
    return;
  }
  listEl.innerHTML = "";

  if (ui.aggregate) {
    // 按标签页分组，标题行可点击折叠，只留下想看的那个页面。
    const groups = new Map();
    for (const item of items) {
      const key = item.tabId ?? -1;
      if (!groups.has(key)) {
        groups.set(key, { tabId: key, title: item.tabTitle || "未命名标签页", items: [] });
      }
      groups.get(key).items.push(item);
    }
    for (const group of groups.values()) {
      const collapsed = ui.collapsedTabs.has(group.tabId);

      const head = document.createElement("div");
      head.className = "group-head";
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = collapsed ? "▶" : "▼";
      const label = document.createElement("span");
      label.textContent = `${group.title} · ${group.items.length} 项`;
      head.append(caret, label);
      head.addEventListener("click", () => {
        if (collapsed) ui.collapsedTabs.delete(group.tabId);
        else ui.collapsedTabs.add(group.tabId);
        render();
      });
      listEl.append(head);

      if (collapsed) continue;
      for (const item of group.items) listEl.append(buildRow(item));
    }
    return;
  }

  for (const item of items) listEl.append(buildRow(item));
}

async function loadItems() {
  if (ui.aggregate) {
    const { groups } = await chrome.runtime.sendMessage({ type: "list-all" });
    ui.items = (groups || []).flatMap((group) =>
      group.items.map((item) => ({ ...item, tabTitle: group.tabTitle, tabId: group.tabId }))
    );
  } else {
    const { items } = await chrome.runtime.sendMessage({ type: "list", tabId: ui.tabId });
    ui.items = items || [];
  }
  render();
}

/* ====================== [功能1] 页面聚合 ====================== */
document.getElementById("btn-aggregate").addEventListener("click", async (event) => {
  ui.aggregate = !ui.aggregate;
  event.currentTarget.classList.toggle("active", ui.aggregate);
  await loadItems();
});
/* ==================== [功能1] 结束 ============================ */

/* ====================== [功能2] 批量下载 ====================== */
const batchBtn = document.getElementById("btn-batch");

function toggleSelect(url, checked) {
  if (checked) ui.selected.add(url);
  else ui.selected.delete(url);
  updateBatchCount();
}

function updateBatchCount() {
  const el = document.getElementById("batch-count");
  if (!el) return;
  el.textContent = ui.batch && ui.selected.size ? `已选 ${ui.selected.size}` : "";
}

batchBtn.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const countEl = document.getElementById("batch-count");

  if (!ui.batch) {
    ui.batch = true;
    button.classList.add("active");
    updateBatchCount();
    render();
    return;
  }

  if (!ui.selected.size) {
    // 没选东西时再点一次 = 退出批量模式；按钮文字始终不变。
    ui.batch = false;
    ui.selected.clear();
    button.classList.remove("active");
    updateBatchCount();
    render();
    return;
  }

  const picked = ui.items.filter((item) => ui.selected.has(item.url));
  button.disabled = true;
  let sent = 0;
  for (let index = 0; index < picked.length; index += 1) {
    countEl.textContent = `发送中 ${index + 1}/${picked.length}`;
    const reply = await chrome.runtime.sendMessage({
      type: "send",
      url: picked[index].url,
      referer: refererOf(picked[index]),
      cookie: picked[index].cookie || "",
    });
    if (reply && reply.ok) sent += 1;
  }
  countEl.textContent = `已发送 ${sent}/${picked.length}`;
  button.disabled = false;
  setTimeout(() => {
    ui.batch = false;
    ui.selected.clear();
    button.classList.remove("active");
    updateBatchCount();
    render();
  }, 1500);
});
/* ==================== [功能2] 结束 ============================ */

/* ====================== [功能3] 条件选择 ====================== */
const selectPanel = document.getElementById("panel-select");
const filterInput = document.getElementById("filter");

document.getElementById("btn-select").addEventListener("click", (event) => {
  selectPanel.hidden = !selectPanel.hidden;
  event.currentTarget.classList.toggle("active", !selectPanel.hidden);
});

document.getElementById("sel-all").addEventListener("click", () => {
  if (!ui.batch) {
    ui.batch = true;
    batchBtn.classList.add("active");
  }
  for (const item of visibleItems()) ui.selected.add(item.url);
  updateBatchCount();
  render();
});

document.getElementById("sel-invert").addEventListener("click", () => {
  if (!ui.batch) {
    ui.batch = true;
    batchBtn.classList.add("active");
  }
  for (const item of visibleItems()) {
    if (ui.selected.has(item.url)) ui.selected.delete(item.url);
    else ui.selected.add(item.url);
  }
  updateBatchCount();
  render();
});

document.getElementById("sel-clear").addEventListener("click", () => {
  ui.selected.clear();
  ui.filter = "";
  filterInput.value = "";
  updateBatchCount();
  render();
});

function applyFilter() {
  ui.filter = filterInput.value.trim();
  render();
}
document.getElementById("filter-apply").addEventListener("click", applyFilter);
filterInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") applyFilter();
});
/* ==================== [功能3] 结束 ============================ */

/* ====================== [功能4] 暂停嗅探 ====================== */
const pauseBtn = document.getElementById("btn-pause");

async function syncPause() {
  const state = await chrome.runtime.sendMessage({ type: "state" });
  if (state) {
    pauseBtn.classList.toggle("paused", !!state.paused);
    const label = document.getElementById("pause-label");
    if (label) label.textContent = "嗅探";
  }
}

pauseBtn.addEventListener("click", async () => {
  const state = await chrome.runtime.sendMessage({ type: "state" });
  await chrome.runtime.sendMessage({ type: "pause", value: !(state && state.paused) });
  await syncPause();
});

// 刷新图标：重载当前标签页 = 重新走一遍嗅探。放在按钮内部，所以必须
// stopPropagation，否则会连带触发暂停/继续。
document.getElementById("btn-refresh").addEventListener("click", async (event) => {
  event.stopPropagation();
  if (ui.tabId != null) {
    try {
      await chrome.tabs.reload(ui.tabId);
    } catch (e) {
      /* tab closed */
    }
  }
  window.close();
});
/* ==================== [功能4] 结束 ============================ */

/* ====================== [功能5] 展开/折叠 ====================== */
/* ==================== 已移除 ================================= */

/* ====================== [功能6] 媒体控制 ====================== */
const mediaPanel = document.getElementById("panel-media");

document.getElementById("btn-media").addEventListener("click", (event) => {
  mediaPanel.hidden = !mediaPanel.hidden;
  event.currentTarget.classList.toggle("active", !mediaPanel.hidden);
});

function mediaCommand(action, value) {
  chrome.runtime.sendMessage({ type: "media", tabId: ui.tabId, action, value });
}

document.getElementById("media-toggle").addEventListener("click", () => mediaCommand("toggle"));
document.getElementById("media-mute").addEventListener("click", () => mediaCommand("mute"));
document.getElementById("media-pip").addEventListener("click", () => mediaCommand("pip"));
document.getElementById("media-full").addEventListener("click", () => mediaCommand("full"));
document.getElementById("media-rate").addEventListener("change", (event) => {
  mediaCommand("rate", event.target.value);
});
/* ==================== [功能6] 结束 ============================ */

/* ============================ 配对状态 ============================ */

async function refreshState(token) {
  const ping = await chrome.runtime.sendMessage({
    type: "ping",
    token: token ?? tokenEl.value.trim(),
  });
  const markEl = document.getElementById("pair-mark");

  // Gopeed has no pairing concept: reachable = connected.
  if (backendEl.value === "gopeed") {
    const gopeedMark = document.getElementById("gopeed-mark");
    markEl.textContent = "";
    markEl.className = "mark";
    if (ping && ping.ok) {
      stateEl.textContent = "Gopeed 已连接";
      stateEl.className = "ok";
      if (gopeedMark) {
        gopeedMark.textContent = "√";
        gopeedMark.className = "mark good";
      }
    } else {
      const tokenWrong = ping && (ping.status === 401 || ping.status === 403);
      stateEl.textContent = tokenWrong ? "Gopeed 令牌错误" : "Gopeed 未连接";
      // 令牌错误用红色，未连接用橙色，两者要能一眼区分。
      stateEl.className = tokenWrong ? "bad" : "warn";
      if (gopeedMark) {
        // 三种状态的标记：√ 已连接 / ? 连不上（未必是令牌问题）/ × 令牌错误
        gopeedMark.textContent = tokenWrong ? "×" : "?";
        gopeedMark.className = tokenWrong ? "mark bad" : "mark warn";
      }
    }
    return;
  }

  // OpenDL：三态 —— 已连接 / 配对码错误 / 未连接
  if (ping && ping.ok && ping.auth) {
    stateEl.textContent = "OpenDL 已连接";
    stateEl.className = "ok";
    markEl.textContent = "√";
    markEl.className = "mark good";
  } else if (ping && ping.ok) {
    // 程序在跑，但配对码被拒
    stateEl.textContent = "OpenDL 配对码错误";
    stateEl.className = "bad";
    markEl.textContent = "×";
    markEl.className = "mark bad";
  } else {
    stateEl.textContent = "OpenDL 未连接";
    stateEl.className = "warn";
    markEl.textContent = "?";
    markEl.className = "mark warn";
  }
}

/* ============================ 启动 ============================ */

async function init() {
  const settings = await chrome.runtime.sendMessage({ type: "settings" });
  tokenEl.value = (settings && settings.token) || "";
  takeoverEl.checked = !(settings && settings.takeover === false);
  backendEl.value = settings && settings.backend === "gopeed" ? "gopeed" : "opendl";
  gopeedUrlEl.value = (settings && settings.gopeedUrl) || "http://127.0.0.1:9999";
  gopeedTokenEl.value = (settings && settings.gopeedToken) || "";
  applyBackendUI();
  await refreshState(tokenEl.value);
  await syncPause();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  ui.tabId = tab ? tab.id : null;
  ui.tabTitle = tab ? tab.title || "" : "";
  if (!tab) return;

  const diag = await chrome.runtime.sendMessage({ type: "diag", tabId: tab.id });
  if (diag && diag.webRequest === false) {
    bannerEl.hidden = false;
    bannerEl.textContent =
      "网络捕获未启用：" + (diag.error || "缺少 webRequest 权限") +
      "。请到 edge://extensions 重新加载本扩展并允许新增权限。";
  }
  await loadItems();
}

gearEl.addEventListener("click", async () => {
  settingsEl.hidden = !settingsEl.hidden;
  if (!settingsEl.hidden) await refreshState(tokenEl.value.trim());
});

/* 后端切换：Gopeed / OpenDL */
function applyBackendUI() {
  const backend = backendEl.value === "gopeed" ? "gopeed" : "opendl";
  gopeedFields.hidden = backend !== "gopeed";
  pairBlock.hidden = backend !== "opendl";
}

async function pushBackend() {
  const backend = backendEl.value === "gopeed" ? "gopeed" : "opendl";
  const gopeedUrl = gopeedUrlEl.value.trim() || "http://127.0.0.1:9999";
  const gopeedToken = gopeedTokenEl.value.trim();
  chrome.storage.local.set({ backend, gopeedUrl, gopeedToken }).catch(() => {});
  await chrome.runtime.sendMessage({ type: "set-backend", backend, gopeedUrl, gopeedToken });
  applyBackendUI();
  await refreshState(tokenEl.value.trim());
}

backendEl.addEventListener("change", pushBackend);
document.getElementById("gopeed-save").addEventListener("click", async () => {
  const button = document.getElementById("gopeed-save");
  await pushBackend();
  flashSaved(button);
});

// 保存反馈：图标本身不变，只闪一下绿色，避免"⚙ → ✓ → ⚙"的跳动
function flashSaved(button) {
  if (!button) return;
  button.classList.add("saved");
  setTimeout(() => button.classList.remove("saved"), 900);
}

saveEl.addEventListener("click", async () => {
  const token = tokenEl.value.trim();
  await chrome.storage.local.set({ token });
  flashSaved(saveEl);
  await refreshState(token);
});

takeoverEl.addEventListener("change", () => {
  chrome.storage.local.set({ takeover: takeoverEl.checked });
});

/* ============================ 备份 ============================
 * 导出当前设置为一个 JSON 文件；导入时只接受已知字段，
 * 防止别人塞进来的任意键污染存储。
 * ============================================================= */
const backupBtn = document.getElementById("btn-backup");
const backupMenu = document.getElementById("backup-menu");
const backupTip = document.getElementById("backup-tip");
const fileInput = document.getElementById("file-import");

function backupFlash(text) {
  backupTip.textContent = text;
  setTimeout(() => {
    if (backupTip.textContent === text) backupTip.textContent = "";
  }, 2200);
}

function sanitizeSettings(raw) {
  const clean = {};
  if (!raw || typeof raw !== "object") return clean;
  if (typeof raw.token === "string") clean.token = raw.token.trim().slice(0, 200);
  if (typeof raw.takeover === "boolean") clean.takeover = raw.takeover;
  if (raw.backend === "gopeed" || raw.backend === "opendl") clean.backend = raw.backend;
  if (typeof raw.gopeedUrl === "string") clean.gopeedUrl = raw.gopeedUrl.trim().slice(0, 200);
  if (typeof raw.gopeedToken === "string") clean.gopeedToken = raw.gopeedToken.trim().slice(0, 200);
  return clean;
}

function buildExportPayload(token, takeover, backend, gopeedUrl, gopeedToken) {
  return {
    app: "opendl-catcher",
    version: 2,
    exportedAt: new Date().toISOString(),
    token: typeof token === "string" ? token : "",
    takeover: takeover !== false,
    backend: backend === "gopeed" ? "gopeed" : "opendl",
    gopeedUrl: typeof gopeedUrl === "string" ? gopeedUrl : "",
    gopeedToken: typeof gopeedToken === "string" ? gopeedToken : "",
  };
}

async function exportSettings() {
  const settings = (await chrome.runtime.sendMessage({ type: "settings" })) || {};
  const payload = buildExportPayload(
    settings.token,
    settings.takeover,
    settings.backend,
    settings.gopeedUrl,
    settings.gopeedToken
  );
  const filename = `opendl-catcher-${new Date().toISOString().slice(0, 10)}.json`;
  // 交给浏览器自己的下载器：不依赖 opendl 是否安装。
  // 接管逻辑只处理 http(s)，blob: 不会被它拦走。
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  backupFlash(`已导出 ${filename}`);
}

async function applyImported(raw) {
  const clean = sanitizeSettings(raw);
  if (!Object.keys(clean).length) {
    backupFlash("导入失败：没有可用的设置项");
    return null;
  }
  chrome.storage.local.set(clean).catch(() => {});
  if (typeof clean.token === "string") tokenEl.value = clean.token;
  if (typeof clean.takeover === "boolean") takeoverEl.checked = clean.takeover;
  if (typeof clean.backend === "string") backendEl.value = clean.backend;
  if (typeof clean.gopeedUrl === "string" && clean.gopeedUrl) gopeedUrlEl.value = clean.gopeedUrl;
  if (typeof clean.gopeedToken === "string") gopeedTokenEl.value = clean.gopeedToken;

  applyBackendUI();
  await refreshState(tokenEl.value);
  await syncPause();
  // 让后台把新后端同步进内存态
  if (typeof clean.backend === "string") await pushBackend();
  backupFlash("导入成功");
  return clean;
}

function importSettings(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      applyImported(JSON.parse(String(reader.result)));
    } catch (e) {
      backupFlash("导入失败：文件不是合法 JSON");
    }
  };
  reader.onerror = () => backupFlash("导入失败：无法读取文件");
  reader.readAsText(file);
}

backupBtn.addEventListener("click", () => {
  backupMenu.hidden = !backupMenu.hidden;
  const caret = document.getElementById("backup-caret");
  if (caret) caret.textContent = backupMenu.hidden ? "▸" : "▾";
});
document.getElementById("btn-export").addEventListener("click", exportSettings);
document.getElementById("btn-import").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  importSettings(fileInput.files && fileInput.files[0]);
  fileInput.value = "";
});

// 自动刷新：只在没有展开的行、且不在批量模式时进行，避免打断播放/选择。
setInterval(() => {
  if (document.querySelector(".item.open") || ui.batch) return;
  loadItems().catch(() => {});
}, 2000);

init();
