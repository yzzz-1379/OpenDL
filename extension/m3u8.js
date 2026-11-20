/* HLS playlist 解析。
 *
 * 猫抓之所以能显示时长/分辨率/码率，是因为它把 m3u8 当清单解析：
 *   - 主清单（master）里的 #EXT-X-STREAM-INF 带 BANDWIDTH 和 RESOLUTION
 *   - 媒体清单里的 #EXTINF 累加就是总时长
 * 我们只显示了清单文件自身的大小（2.2 KB），所以看起来"什么都识别不出来"。
 *
 * 这里只做纯文本解析，不碰 chrome API，方便在 node 里直接测。
 */

(function (root) {
  "use strict";

  function absUrl(uri, base) {
    try {
      return new URL(uri, base).href;
    } catch (e) {
      return uri;
    }
  }

  function parseAttrs(line) {
    // 形如：BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
    const out = {};
    const re = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/g;
    let m;
    while ((m = re.exec(line))) {
      out[m[1]] = m[2].replace(/^"|"$/g, "");
    }
    return out;
  }

  /**
   * 解析一份 m3u8。
   * @returns {{
   *   master: boolean,
   *   variants: Array<{bandwidth:number,width:number,height:number,url:string,codecs:string}>,
   *   duration: number, segments: number, encrypted: boolean,
   *   targetDuration: number, live: boolean
   * }}
   */
  function parseM3u8(text, baseUrl) {
    const result = {
      master: false,
      variants: [],
      duration: 0,
      segments: 0,
      encrypted: false,
      targetDuration: 0,
      live: false,
    };
    if (!text || typeof text !== "string") return result;

    const lines = text.split(/\r?\n/);
    let pendingVariant = null;

    for (let i = 0; i < lines.length; i += 1) {
      const raw = lines[i].trim();
      if (!raw) continue;

      if (raw.startsWith("#EXT-X-STREAM-INF:")) {
        result.master = true;
        const attrs = parseAttrs(raw.slice("#EXT-X-STREAM-INF:".length));
        const bw = parseInt(attrs.BANDWIDTH || attrs["AVERAGE-BANDWIDTH"] || "0", 10) || 0;
        let width = 0;
        let height = 0;
        if (attrs.RESOLUTION) {
          const m = /^(\d+)x(\d+)$/i.exec(attrs.RESOLUTION);
          if (m) {
            width = Number(m[1]);
            height = Number(m[2]);
          }
        }
        pendingVariant = { bandwidth: bw, width, height, codecs: attrs.CODECS || "", url: "" };
        continue;
      }

      if (raw.startsWith("#")) {
        if (raw.startsWith("#EXTINF:")) {
          const value = parseFloat(raw.slice("#EXTINF:".length));
          if (Number.isFinite(value)) result.duration += value;
          result.segments += 1;
        } else if (raw.startsWith("#EXT-X-TARGETDURATION:")) {
          result.targetDuration = parseFloat(raw.slice("#EXT-X-TARGETDURATION:".length)) || 0;
        } else if (raw.startsWith("#EXT-X-KEY:")) {
          const attrs = parseAttrs(raw.slice("#EXT-X-KEY:".length));
          if (attrs.METHOD && attrs.METHOD.toUpperCase() !== "NONE") result.encrypted = true;
        } else if (raw.startsWith("#EXT-X-ENDLIST")) {
          result.live = false;
        }
        continue;
      }

      // 非注释行：要么是主清单里的变体地址，要么是媒体清单里的分片
      if (pendingVariant) {
        pendingVariant.url = absUrl(raw, baseUrl);
        result.variants.push(pendingVariant);
        pendingVariant = null;
      }
    }

    // 没有 ENDLIST 且没有累加到时长，按直播流处理
    if (!result.master && result.segments > 0 && !/#EXT-X-ENDLIST/.test(text)) {
      result.live = true;
    }
    return result;
  }

  function pickBestVariant(variants) {
    if (!variants || !variants.length) return null;
    return variants.slice().sort((a, b) => {
      if (b.bandwidth !== a.bandwidth) return b.bandwidth - a.bandwidth;
      return b.height * b.width - a.height * a.width;
    })[0];
  }

  /**
   * 把清单信息折算成展示字段。码率缺失时用 分片大小/时长 兜底。
   * @returns {{duration:number,width:number,height:number,bandwidth:number,estBytes:number,encrypted:boolean,live:boolean}}
   */
  function summarize(source, media) {
    const info = Object.assign({}, source || {});
    const playlist = media || {};
    const duration = playlist.duration || info.duration || 0;
    const bandwidth = info.bandwidth || (playlist.bytes && duration ? (playlist.bytes * 8) / duration : 0);
    return {
      duration,
      width: info.width || 0,
      height: info.height || 0,
      bandwidth,
      // 估算体积：比特率 × 时长 ÷ 8
      estBytes: bandwidth && duration ? Math.round((bandwidth * duration) / 8) : 0,
      encrypted: !!playlist.encrypted || !!info.encrypted,
      live: !!playlist.live,
    };
  }

  const api = { parseM3u8, pickBestVariant, summarize };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.OpenDLHls = api;
    // service worker 里 importScripts 之后直接可用
    root.parseM3u8 = parseM3u8;
    root.pickBestVariant = pickBestVariant;
    root.summarizeHls = summarize;
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof self !== "undefined" ? self : null));
