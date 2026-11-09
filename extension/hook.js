/* Runs in the page's own JS context (MAIN world).
 *
 * An extension API cannot read response bodies, so nothing here uses one:
 * these are plain monkey-patches on the page's own objects. Whatever the page
 * can see, this hook can see — that is exactly why it works where a normal
 * content script cannot.
 */
(() => {
  "use strict";
  if (window.__opendlHooked) return;
  window.__opendlHooked = true;

  const MEDIA_RE =
    /\.(m3u8|mpd|mp4|m4s|m4v|m4a|ts|mkv|webm|flv|mov|mp3|aac|ogg|wav|opus|flac)(\?|#|$)/i;
  const CT_RE = /^(video|audio)\/|mpegurl|dash\+xml|octet-stream/i;

  const seen = new Set();

  function report(kind, url, mime, size, extra) {
    if (!url) return;
    url = String(url).slice(0, 8000);
    mime = String(mime || "").slice(0, 120);
    if (url.startsWith("data:") || url.startsWith("mse:")) return;

    const looksMedia = (mime && CT_RE.test(mime)) || MEDIA_RE.test(url);
    if (!looksMedia) return;

    const key = mime + "|" + url.slice(0, 400);
    const known = seen.has(key);
    if (known && !extra) return;
    if (!known) {
      seen.add(key);
      if (seen.size > 800) seen.delete(seen.values().next().value);
    }

    try {
      window.postMessage(
        {
          __opendl: true,
          item: {
            kind,
            url,
            mime,
            size: size || 0,
            title: String((typeof document !== "undefined" && document.title) || "").slice(0, 200),
            // The player often lives in an iframe; the CDN checks that frame's
            // URL as the Referer, not the top-level tab.
            pageUrl: String((typeof location !== "undefined" && location.href) || "").slice(0, 2000),
            ts: Date.now(),
            ...(extra || {}),
          },
        },
        "*"
      );
    } catch (e) {
      /* the page may have replaced window.postMessage; never break it */
    }
  }

  /* ---- XHR ---------------------------------------------------------- */
  try {
    const open = XMLHttpRequest.prototype.open;
    const send = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function (method, url) {
      try {
        this.__opendlUrl = url;
      } catch (e) {}
      return open.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function () {
      const xhr = this;
      try {
        xhr.addEventListener("loadend", () => {
          try {
            const ct = xhr.getResponseHeader("content-type") || "";
            const len = parseInt(xhr.getResponseHeader("content-length") || "0", 10) || 0;
            let real = 0;
            try {
              if (!xhr.responseType || xhr.responseType === "text") {
                real = (xhr.responseText || "").length;
              } else if (xhr.response && xhr.response.byteLength) {
                real = xhr.response.byteLength;
              }
            } catch (e) {}
            report("xhr", xhr.responseURL || xhr.__opendlUrl, ct, len || real);
          } catch (e) {}
        });
      } catch (e) {}
      return send.apply(this, arguments);
    };
  } catch (e) {}

  /* ---- fetch -------------------------------------------------------- */
  try {
    const nativeFetch = window.fetch;
    if (nativeFetch) {
      window.fetch = function (input) {
        const requestUrl = typeof input === "string" ? input : (input && input.url) || "";
        const promise = nativeFetch.apply(this, arguments);
        return promise.then((response) => {
          try {
            const ct = response.headers.get("content-type") || "";
            const len = parseInt(response.headers.get("content-length") || "0", 10) || 0;
            report("fetch", response.url || requestUrl, ct, len);
          } catch (e) {}
          return response;
        });
      };
    }
  } catch (e) {}

  /* ---- Blob / object URLs ------------------------------------------- */
  try {
    const createObjectURL = URL.createObjectURL;
    URL.createObjectURL = function (object) {
      const url = createObjectURL.apply(this, arguments);
      try {
        if (object instanceof Blob) {
          report("blob", url, object.type || "", object.size || 0);
        }
      } catch (e) {}
      return url;
    };
  } catch (e) {}

  /* ---- MediaSource (this is how modern sites deliver video) ---------- */
  try {
    if (window.MediaSource) {
      const addSourceBuffer = MediaSource.prototype.addSourceBuffer;
      MediaSource.prototype.addSourceBuffer = function (mime) {
        try {
          report("stream", "mse://" + String(mime || "video").replace(/[;\s].*$/, ""), mime, 0);
        } catch (e) {}
        return addSourceBuffer.apply(this, arguments);
      };
    }
  } catch (e) {}

  /* ---- Direct <video>/<audio> sources -------------------------------- */
  try {
    // The page has already loaded this media, so its own element knows the
    // real duration and frame size. Reading it needs no request and no
    // headers — which is why this works where re-fetching gets a 403.
    const publishMeta = (element) => {
      try {
        const url = element.currentSrc || element.src || "";
        if (!url) return;
        const duration = Number.isFinite(element.duration) ? element.duration : 0;
        const width = element.videoWidth || 0;
        const height = element.videoHeight || 0;
        if (!duration && !width && !height) return;
        report(
          "media",
          url,
          element.tagName === "AUDIO" ? "audio/*" : "video/*",
          0,
          { duration, width, height }
        );
      } catch (e) {}
    };

    // Pick up media that was already loaded before this script ran (an
    // extension reload does not re-inject into open tabs, so "play again"
    // is not something we can rely on).
    const publishAll = () => {
      try {
        document.querySelectorAll("video, audio").forEach((element) => publishMeta(element));
      } catch (e) {}
    };
    setTimeout(publishAll, 800);
    setTimeout(publishAll, 2500);

    for (const eventName of ["loadedmetadata", "loadeddata", "canplay", "durationchange", "play"]) {
      document.addEventListener(
        eventName,
        (event) => {
          const element = event.target;
          if (element && (element.tagName === "VIDEO" || element.tagName === "AUDIO")) {
            publishMeta(element);
          }
        },
        true
      );
    }
  } catch (e) {}
})();
