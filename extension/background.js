/* Service worker: passive network capture (the way Cat Catch does it), the
 * OpenDL handoff, and browser-download takeover.
 *
 * Size and MIME come from the response headers of the request the page already
 * made — `webRequest.onResponseStarted`. Re-fetching the URL ourselves was
 * wrong: an extension-page request carries neither the site's SameSite cookies
 * nor its Referer, so signed CDN links answer 403 and the size was a lie.
 */

const API = "http://127.0.0.1:6688";
const TOKEN_HEADER = "X-OpenDL-Token";
const RULE_ID = 9001;
const MAX_PER_TAB = 300;

const byTab = new Map(); // tabId -> Map(url -> item)
const pendingHeaders = new Map(); // requestId -> {referer, cookie, userAgent}
let captureError = "";

let settings = {
  token: "",
  takeover: true,
  paused: false,
  backend: "opendl",          // "gopeed" | "opendl"
  gopeedUrl: "http://127.0.0.1:9999",
  gopeedToken: "",
};

async function loadSettings() {
  const stored = await chrome.storage.local.get([
    "token", "takeover", "paused", "backend", "gopeedUrl", "gopeedToken",
  ]);
  settings = {
    token: stored.token || "",
    takeover: stored.takeover !== false,
    paused: stored.paused === true,
    backend: stored.backend === "gopeed" ? "gopeed" : "opendl",
    gopeedUrl: stored.gopeedUrl || "http://127.0.0.1:9999",
    gopeedToken: stored.gopeedToken || "",
  };
}
loadSettings();
chrome.storage.onChanged.addListener((changes) => {
  if (changes.token) settings.token = changes.token.newValue || "";
  if (changes.takeover) settings.takeover = changes.takeover.newValue !== false;
  if (changes.paused) settings.paused = changes.paused.newValue === true;
  if (changes.backend) settings.backend = changes.backend.newValue === "gopeed" ? "gopeed" : "opendl";
  if (changes.gopeedUrl) settings.gopeedUrl = changes.gopeedUrl.newValue || "http://127.0.0.1:8888";
  if (changes.gopeedToken) settings.gopeedToken = changes.gopeedToken.newValue || "";
});

function tabStore(tabId) {
  if (!byTab.has(tabId)) byTab.set(tabId, new Map());
  return byTab.get(tabId);
}

function refreshBadge(tabId) {
  const count = tabStore(tabId).size;
  chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#1f7a5c" }).catch(() => {});
}

function isPlaceholderMime(mime) {
  return !mime || mime === "video/*" || mime === "audio/*" || mime.endsWith("/*");
}

function pickMime(incoming, previous) {
  // The page hook only knows the element tag ("video/*"); the response header
  // knows the real type ("video/mp4"). A placeholder must never win.
  if (incoming && !isPlaceholderMime(incoming)) return incoming;
  if (previous && !isPlaceholderMime(previous)) return previous;
  return incoming || previous || "";
}

function mergeItem(tabId, incoming) {
  // Feature 4: while sniffing is paused, drop new finds (existing ones stay).
  if (settings.paused) return;
  const store = tabStore(tabId);
  const previous = store.get(incoming.url) || {};
  store.set(incoming.url, {
    ...previous,
    ...incoming,
    size: incoming.size || previous.size || 0,
    mime: pickMime(incoming.mime, previous.mime),
    duration: incoming.duration || previous.duration || 0,
    width: incoming.width || previous.width || 0,
    height: incoming.height || previous.height || 0,
    title: incoming.title || previous.title || "",
    referer: incoming.referer || previous.referer || "",
    cookie: incoming.cookie || previous.cookie || "",
    frameId: incoming.frameId ?? previous.frameId ?? 0,
  });
  if (store.size > MAX_PER_TAB) store.delete(store.keys().next().value);
  refreshBadge(tabId);
}

function isWorthListing(item) {
  const type = (item.mime || "").toLowerCase();
  if (/^(video|audio)\//.test(type)) return true;
  if (/mpegurl|dash\+xml|octet-stream/.test(type)) return true;
  if (/\.(mp4|m4s|m4v|m4a|ts|mkv|webm|flv|mov|mp3|aac|ogg|wav|opus|flac|m3u8|mpd)(\?|#|$)/i.test(item.url)) return true;
  return (item.size || 0) >= 1024 * 1024;
}

const KIND_BY_TYPE = {
  media: "MEDIA",
  xmlhttprequest: "XHR",
  image: "IMG",
  object: "OBJ",
  other: "OTHER",
};

/* ---- capture: the page's own requests -------------------------------- */
// A missing permission leaves chrome.webRequest undefined. Touching it at top
// level would throw and kill the whole worker — including the message listener
// the popup depends on — so the whole block is conditional.
const CAPTURE_READY = (() => {
  try {
    if (!chrome.webRequest) {
      captureError = "chrome.webRequest 不存在（权限未生效）";
      return false;
    }
    registerCaptureListeners();
    return true;
  } catch (error) {
    captureError = String(error && error.message ? error.message : error);
    return false;
  }
})();

function registerCaptureListeners() {
  const sendHandler = (details) => {
    try {
      const headers = details.requestHeaders || [];
      const pick = (name) => {
        const hit = headers.find((h) => h.name.toLowerCase() === name);
        return hit ? hit.value : "";
      };
      pendingHeaders.set(details.requestId, {
        referer: pick("referer"),
        cookie: pick("cookie"),
        userAgent: pick("user-agent"),
      });
      if (pendingHeaders.size > 500) {
        pendingHeaders.delete(pendingHeaders.keys().next().value);
      }
    } catch (e) {
      /* header access must never break the listener */
    }
  };

  // extraHeaders is needed to see Cookie/Referer but is not accepted by every
  // Chromium build; fall back to a plain registration rather than dying.
  try {
    chrome.webRequest.onSendHeaders.addListener(
      sendHandler,
      { urls: ["<all_urls>"] },
      ["requestHeaders", "extraHeaders"]
    );
  } catch (e) {
    chrome.webRequest.onSendHeaders.addListener(
      sendHandler,
      { urls: ["<all_urls>"] },
      ["requestHeaders"]
    );
  }

chrome.webRequest.onResponseStarted.addListener(
  (details) => {
    try {
      if (details.tabId < 0) return;
      const headers = details.responseHeaders || [];
      const value = (name) => {
        const hit = headers.find((h) => h.name.toLowerCase() === name);
        return hit ? hit.value : "";
      };

      const mime = value("content-type").split(";")[0].trim().toLowerCase();
      let size = parseInt(value("content-length") || "0", 10) || 0;
      const contentRange = value("content-range");
      if (contentRange) {
        const match = /\/(\d+)\s*$/.exec(contentRange);
        if (match) size = Number(match[1]);
      }

      const sent = pendingHeaders.get(details.requestId) || {};
      pendingHeaders.delete(details.requestId);

      const item = {
        kind: KIND_BY_TYPE[details.type] || "OTHER",
        url: details.url,
        mime,
        size,
        referer: sent.referer || "",
        cookie: sent.cookie || "",
        frameId: details.frameId ?? 0,
        ts: Date.now(),
      };
      if (!isWorthListing(item)) return;
      mergeItem(details.tabId, item);
    } catch (e) {
      /* ignore malformed events */
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// Every webRequest event requires a RequestFilter; calling addListener with a
// callback alone throws "No matching signature" and aborts the whole block.
chrome.webRequest.onErrorOccurred.addListener(
  (details) => {
    pendingHeaders.delete(details.requestId);
  },
  { urls: ["<all_urls>"] }
);
}

/* ---- page-context metadata (duration / frame size / title) ------------ */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "captured") {
    const tabId = sender.tab ? sender.tab.id : undefined;
    if (tabId === undefined) return;
    const item = msg.item || {};
    if (!item.url) return;
    mergeItem(tabId, {
      ...item,
      kind: item.kind === "media" ? "MEDIA" : item.kind,
      frameId: sender.frameId ?? item.frameId ?? 0,
    });
    return;
  }

  if (msg.type === "list") {
    const items = [...tabStore(msg.tabId).values()].sort((a, b) => b.ts - a.ts);
    sendResponse({ items });
    return true;
  }

  // Feature 1: every tab that has captures, with its title for labelling.
  if (msg.type === "list-all") {
    const tabIds = [...byTab.keys()];
    Promise.all(
      tabIds.map(async (tabId) => {
        let tabTitle = "";
        try {
          const tab = await chrome.tabs.get(tabId);
          tabTitle = (tab && tab.title) || "";
        } catch (e) {
          /* tab closed */
        }
        return {
          tabId,
          tabTitle,
          items: [...tabStore(tabId).values()].sort((a, b) => b.ts - a.ts),
        };
      })
    ).then((groups) => sendResponse({ groups: groups.filter((g) => g.items.length) }));
    return true;
  }

  // Feature 4: pause / resume capture.
  if (msg.type === "pause") {
    settings.paused = !!msg.value;
    chrome.storage.local.set({ paused: settings.paused }).catch(() => {});
    sendResponse({ ok: true, paused: settings.paused });
    return true;
  }

  if (msg.type === "state") {
    sendResponse({ paused: settings.paused });
    return true;
  }

  // Feature 6: forward a media command to every frame; only frames that own a
  // <video> act on it.
  if (msg.type === "media") {
    chrome.tabs
      .sendMessage(msg.tabId, { type: "opendl-media", action: msg.action, value: msg.value })
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === "clear") {
    tabStore(msg.tabId).clear();
    refreshBadge(msg.tabId);
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === "ping") {
    if (settings.backend === "gopeed") {
      fetch(settings.gopeedUrl.replace(/\/$/, "") + "/api/v1/info", { headers: gopeedHeaders() })
        .then((r) => sendResponse({ ok: r.ok, auth: r.ok, status: r.status, backend: "gopeed" }))
        .catch(() => sendResponse({ ok: false, status: 0, backend: "gopeed" }));
      return true;
    }
    // Send the token with the probe: the server decides `auth` from the header,
    // so a bare ping always reported "not paired" even after saving.
    const token = msg.token !== undefined ? msg.token : settings.token;
    const pingHeaders = {
      [TOKEN_HEADER]: token,
      Origin: "chrome-extension://" + chrome.runtime.id,
    };
    fetch(API + "/ping", { headers: pingHeaders })
      .then((r) => r.json())
      .then((j) => sendResponse({ ok: !!j.ok, auth: !!j.auth, backend: "opendl" }))
      .catch(() => sendResponse({ ok: false, backend: "opendl" }));
    return true;
  }

  if (msg.type === "set-backend") {
    settings.backend = msg.backend === "gopeed" ? "gopeed" : "opendl";
    if (typeof msg.gopeedUrl === "string" && msg.gopeedUrl) settings.gopeedUrl = msg.gopeedUrl;
    if (typeof msg.gopeedToken === "string") settings.gopeedToken = msg.gopeedToken;
    sendResponse({ ok: true, backend: settings.backend });
    return true;
  }

  if (msg.type === "send") {
    handOff(msg.url, msg.referer, msg.cookie).then(sendResponse);
    return true;
  }

  if (msg.type === "preview-page") {
    chrome.tabs
      .sendMessage(msg.tabId, { type: "opendl-preview", url: msg.url }, { frameId: msg.frameId ?? 0 })
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === "settings") {
    sendResponse({
      token: settings.token,
      takeover: settings.takeover,
      paused: settings.paused,
      backend: settings.backend,
      gopeedUrl: settings.gopeedUrl,
      gopeedToken: settings.gopeedToken,
    });
    return true;
  }

  if (msg.type === "diag") {
    sendResponse({ webRequest: CAPTURE_READY, error: captureError, items: tabStore(msg.tabId).size });
    return true;
  }

  if (msg.type === "arm") {
    armHeaders(msg.url, msg.referer, msg.cookie).then((ok) => sendResponse({ ok }));
    return true;
  }
});

/* ---- handoff ---------------------------------------------------------- */
function headersFor() {
  return {
    "Content-Type": "application/json",
    [TOKEN_HEADER]: settings.token,
    Origin: "chrome-extension://" + chrome.runtime.id,
  };
}

async function sendToApp(url, referer, cookie) {
  const response = await fetch(API + "/add", {
    method: "POST",
    headers: headersFor(),
    body: JSON.stringify({ url, referer: referer || "", cookie: cookie || "" }),
  });
  const payload = await response.json().catch(() => ({}));
  return { ok: response.ok && payload.ok === true, status: response.status, error: payload.error };
}

function launchApp(url) {
  chrome.tabs.create({ url: "opendl://add?url=" + encodeURIComponent(url) }).catch(() => {});
}

async function handOff(url, referer, cookie) {
  if (settings.backend === "gopeed") return handOffGopeed(url, referer, cookie);
  try {
    const result = await sendToApp(url, referer, cookie);
    if (result.ok) return { ok: true };
    if (result.status === 403) return { ok: false, error: "pairing-token" };
    return { ok: false, error: result.error || "rejected" };
  } catch (error) {
    launchApp(url);
    return { ok: false, error: "launched" };
  }
}

/* ---- Gopeed backend --------------------------------------------------- */
function gopeedHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (settings.gopeedToken) headers.Authorization = "Bearer " + settings.gopeedToken;
  return headers;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postGopeedTask(url, referer, cookie) {
  const header = {};
  if (referer) header.Referer = referer;
  if (cookie) header.Cookie = cookie;
  const response = await fetch(settings.gopeedUrl.replace(/\/$/, "") + "/api/v1/tasks", {
    method: "POST",
    headers: gopeedHeaders(),
    body: JSON.stringify({ req: { url, extra: { header } }, opts: {} }),
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

async function handOffGopeed(url, referer, cookie) {
  try {
    const { response, payload } = await postGopeedTask(url, referer, cookie);
    if (response.ok && payload.code === 0) return { ok: true, backend: "gopeed" };
    // 401/403 是令牌问题，拉起程序也修不了
    return { ok: false, error: payload.msg || `HTTP ${response.status}`, status: response.status };
  } catch (error) {
    // 连不上：用系统协议 gopeed:// 拉起它，等 API 起来后重试一次
    try {
      await chrome.tabs.create({ url: "gopeed://" });
    } catch (e) {
      /* 没有协议处理器就只能放弃 */
    }
    await sleep(2500);
    try {
      const { response, payload } = await postGopeedTask(url, referer, cookie);
      if (response.ok && payload.code === 0) {
        return { ok: true, backend: "gopeed", launched: true };
      }
      return { ok: false, error: payload.msg || `HTTP ${response.status}` };
    } catch (e) {
      return { ok: false, error: "Gopeed 未连接" };
    }
  }
}

/* ---- browser download takeover ---------------------------------------- */
chrome.downloads.onCreated.addListener(async (item) => {
  if (!settings.takeover) return;
  const url = item.finalUrl || item.url || "";
  if (!/^https?:/i.test(url)) return;
  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch (e) {
    /* already finished or gone */
  }
  await handOff(url, item.referrer || "", "");
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "opendl-link",
    title: "用 OpenDL 下载此链接",
    contexts: ["link", "video", "audio", "image"],
  });
});

chrome.contextMenus.onClicked.addListener((info) => {
  const url = info.linkUrl || info.srcUrl || "";
  if (/^https?:/i.test(url)) handOff(url, info.pageUrl || "", "");
});

/* ---- Referer rule (preview only) -------------------------------------- */
async function setRefererRule(url, referer) {
  if (!chrome.declarativeNetRequest) return false;
  try {
    const host = new URL(url).host;
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [RULE_ID],
      addRules: [
        {
          id: RULE_ID,
          priority: 1,
          action: {
            type: "modifyHeaders",
            requestHeaders: [
              { header: "Referer", operation: "set", value: referer || `https://${host}/` },
            ],
          },
          condition: { requestDomains: [host], resourceTypes: ["media", "xmlhttprequest", "other"] },
        },
      ],
    });
    return true;
  } catch (e) {
    return false;
  }
}

/* Cat Catch replays the captured request headers through a session rule before
 * playing (js/function.js:125-156) — including Cookie, which is what a signed
 * CDN actually checks. Referer alone was not enough. */
async function armHeaders(url, referer, cookie) {
  if (!chrome.declarativeNetRequest) return false;
  const requestHeaders = [];
  if (referer) requestHeaders.push({ header: "Referer", operation: "set", value: referer });
  if (cookie) requestHeaders.push({ header: "Cookie", operation: "set", value: cookie });
  if (!requestHeaders.length) return false;
  try {
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [RULE_ID],
      addRules: [
        {
          id: RULE_ID,
          priority: 1,
          action: { type: "modifyHeaders", requestHeaders },
          condition: {
            urlFilter: url,
            // main_frame too, so "open in a new tab" also carries the headers.
            resourceTypes: ["main_frame", "sub_frame", "media", "xmlhttprequest", "image", "other"],
          },
        },
      ],
    });
    return true;
  } catch (e) {
    return false;
  }
}

/* ---- stale header rules ---------------------------------------------- */
// An earlier build injected a Referer rule; a session rule can outlive a
// reload and would keep breaking media requests. Clear it unconditionally and
// never re-arm it: the site rejects foreign Referers.
try {
  chrome.declarativeNetRequest?.updateSessionRules({ removeRuleIds: [RULE_ID] });
} catch (e) {
  /* not fatal */
}

/* ---- navigation resets the list --------------------------------------- */
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  // A reload reports "loading" without a new URL, so keying on the URL alone
  // would leave stale captures behind after the refresh button.
  if (changeInfo.status === "loading") {
    byTab.delete(tabId);
    chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener((tabId) => byTab.delete(tabId));
