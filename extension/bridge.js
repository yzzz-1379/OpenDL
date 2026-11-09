/* Isolated-world relay: the page context cannot talk to chrome.*, so hook.js
 * posts a window message and this bridge forwards it into the service worker.
 */
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.__opendl !== true || !data.item) return;
  try {
    chrome.runtime.sendMessage({ type: "captured", item: data.item });
  } catch (e) {
    /* extension reloaded; nothing useful to do here */
  }
});

/* Preview lives in the page, not the popup: a request from the page carries
 * the site's own SameSite cookies and Referer, which an extension page cannot. */
function openPreview(url) {
  const previous = document.getElementById("__opendl_preview__");
  if (previous) previous.remove();

  const box = document.createElement("div");
  box.id = "__opendl_preview__";
  box.style.cssText =
    "position:fixed;top:16px;right:16px;width:420px;z-index:2147483647;background:#111;" +
    "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.55);padding:8px;font:12px system-ui;color:#eee";

  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:6px";
  bar.textContent = "OpenDL 预览";

  const close = document.createElement("button");
  close.textContent = "✕";
  close.style.cssText =
    "background:#333;color:#fff;border:0;border-radius:4px;cursor:pointer;padding:2px 8px";
  close.onclick = () => box.remove();
  bar.appendChild(close);

  const video = document.createElement("video");
  video.src = url;
  video.controls = true;
  video.autoplay = true;
  video.style.cssText = "width:100%;border-radius:6px;background:#000";

  box.append(bar, video);
  document.documentElement.appendChild(box);
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "opendl-preview") return;
  openPreview(msg.url);
});

/* ===== [功能6] 媒体控制 =================================================
 * 页面里可能有多个 <video>（广告、预览、正片）。优先控制正在播放的，
 * 其次控制面积最大的那个。
 * 删除此功能：删掉本段即可。
 * ===================================================================== */
function pickMediaElement() {
  const nodes = Array.from(document.querySelectorAll("video, audio")).filter(
    (element) => element.currentSrc || element.src
  );
  if (!nodes.length) return null;
  nodes.sort((a, b) => {
    const playing = Number(b.paused === false) - Number(a.paused === false);
    if (playing) return playing;
    return (b.videoWidth || 0) * (b.videoHeight || 0) - (a.videoWidth || 0) * (a.videoHeight || 0);
  });
  return nodes[0];
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "opendl-media") return;
  const media = pickMediaElement();
  if (!media) return;
  switch (msg.action) {
    case "toggle":
      if (media.paused) media.play().catch(() => {});
      else media.pause();
      break;
    case "mute":
      media.muted = !media.muted;
      break;
    case "rate":
      media.playbackRate = Number(msg.value) || 1;
      break;
    case "pip":
      if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
      else if (media.requestPictureInPicture) media.requestPictureInPicture().catch(() => {});
      break;
    case "fullscreen": {
      // requestFullscreen() needs a user gesture, and a click inside the popup
      // is not one for the page — the browser rejects it silently. This
      // pseudo-fullscreen needs no gesture and works from a message.
      if (media.dataset.opendlFull === "1") {
        media.style.cssText = media.dataset.opendlPrevStyle || "";
        delete media.dataset.opendlFull;
        delete media.dataset.opendlPrevStyle;
        break;
      }
      media.dataset.opendlPrevStyle = media.style.cssText || "";
      media.dataset.opendlFull = "1";
      media.style.position = "fixed";
      media.style.inset = "0";
      media.style.width = "100vw";
      media.style.height = "100vh";
      media.style.zIndex = "2147483647";
      media.style.background = "#000";
      break;
    }
    default:
      break;
  }
});
