// Runs inside a Skool classroom page in the owner's logged-in Chrome (via Claude in Chrome's
// javascript tool). It reads only what the owner can already see. Skool's Content-Security-Policy
// blocks connections to this PC, so each lesson is packed into ONE file that Chrome saves to the
// owner's Downloads folder (jarvis-skool-*.json); `bin/jarvis mentor import` files it in the brain
// (see core/jarvis/mentor_capture.py).
//
// Usage from the agent: run `bin/jarvis mentor script` once per browser tab to get the one-time
// install snippet (it stores this file in the tab's sessionStorage), then on every lesson page:
//   eval(sessionStorage.jarvisSkool); await JarvisSkool.<fn>(...)
//
//   JarvisSkool.courses()                 -> [{order, id, name, title, modules}] on the classroom page
//   JarvisSkool.lessons()                 -> [{order, id, title, hasVideo, resources}] on a course page
//   JarvisSkool.captureCourse({courseOrder, courseTitle, from?, only?: [lesson orders]})  -> every (or only some) lessons
//   JarvisSkool.captureLesson(opts)       -> summary of what was saved for the lesson in ?md=<id>
//       opts: {courseOrder, courseTitle, lessonOrder, every: 2, minGap: 30, minCells: 6, maxFrames: 500, width: 1280}
(function () {
  // Everything for one lesson is collected here, then saved as a single download.
  let bundle = null;
  const post = async (path, body) => {
    if (path === "/lesson") bundle.lesson = body;
    else ({ "/frame": bundle.frames, "/image": bundle.images, "/sheet": bundle.sheets })[path].push(body);
    return { ok: true };
  };
  function download(name) {
    const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    return blob.size;
  }
  const fileSlug = (t) => (t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const deep = (root, acc = []) => {
    root.querySelectorAll("*").forEach((el) => { acc.push(el); if (el.shadowRoot) deep(el.shadowRoot, acc); });
    return acc;
  };
  const data = () => JSON.parse(document.getElementById("__NEXT_DATA__").textContent).props.pageProps;
  const unit = (n) => n.course || n;
  const parseResources = (r) => { try { return JSON.parse(r || "[]"); } catch (e) { return []; } };

  // Skool lesson text is "[v2]" + ProseMirror-style JSON. Flatten it to Markdown-ish text.
  function richText(desc) {
    if (!desc) return { text: "", images: [], links: [] };
    let nodes;
    try { nodes = JSON.parse(desc.replace(/^\[v\d+\]/, "")); } catch (e) { return { text: desc, images: [], links: [] }; }
    const images = [], links = [];
    const walk = (n) => {
      if (Array.isArray(n)) return n.map(walk).join("");
      if (!n || typeof n !== "object") return "";
      const kids = walk(n.content || []);
      const marks = n.marks || [];
      let t = n.text || "";
      for (const m of marks) if (m.type === "link" && m.attrs?.href) { links.push(m.attrs.href); t = `[${t}](${m.attrs.href})`; }
      switch (n.type) {
        case "paragraph": return kids + "\n\n";
        case "heading": return "#".repeat(n.attrs?.level || 2) + " " + kids + "\n\n";
        case "bulletList": case "orderedList": return kids + "\n";
        case "listItem": return "- " + kids.trim() + "\n";
        case "hardBreak": return "\n";
        case "image": images.push(n.attrs?.src); return `![${n.attrs?.alt || "image"}](image ${images.length})\n\n`;
        default: return t + kids;
      }
    };
    return { text: walk(nodes).replace(/\n{3,}/g, "\n\n").trim(), images: images.filter(Boolean), links };
  }

  async function ensureVideo(timeoutMs = 12000) {
    let v = deep(document).find((e) => e.tagName === "VIDEO" && e.isConnected);
    if (v) return v;
    // The player only mounts after the poster is clicked. Skool's poster is a div with a background
    // image from image.video.skool.com inside a "skool-mux" container; fall back to the largest image.
    const poster = [...document.querySelectorAll("div")].find((d) => /image\.video\.skool\.com|mux\.com/.test(getComputedStyle(d).backgroundImage))
      || document.querySelector('[class*="skool-mux"]')
      || [...document.querySelectorAll("img")].filter((i) => i.width > 300).sort((a, b) => b.width - a.width)[0];
    if (!poster) return null;
    const r = poster.getBoundingClientRect();
    const target = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) || poster;
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
    }
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      await sleep(400);
      v = deep(document).find((e) => e.tagName === "VIDEO");
      if (v && v.readyState >= 1) return v;
    }
    return v || null;
  }

  async function captions(v) {
    const mv = deep(document).find((e) => e.tagName === "MUX-VIDEO");
    const src = (mv && (mv.src || mv.getAttribute("src"))) || v.currentSrc;
    if (!src || src.startsWith("blob:") && !mv) return [];
    const master = await (await fetch(src)).text();
    const line = master.split("\n").find((l) => l.includes("TYPE=SUBTITLES"));
    if (!line) return [];
    const subUrl = new URL(line.match(/URI="([^"]+)"/)[1], src).href;
    const pl = await (await fetch(subUrl)).text();
    let vtt = "";
    for (const s of pl.split("\n").filter((l) => l && !l.startsWith("#"))) vtt += (await (await fetch(new URL(s, subUrl).href)).text()) + "\n\n";
    const cues = [], seen = new Set();
    const re = /(?:(\d+):)?(\d{2}):(\d{2})\.\d{3} --> [^\n]+\n([\s\S]*?)(?=\n\s*\n|$)/g;
    let m;
    while ((m = re.exec(vtt))) {
      const t = (m[1] ? +m[1] * 3600 : 0) + +m[2] * 60 + +m[3];
      const text = m[4].replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
      const k = t + "|" + text;
      if (text && !seen.has(k)) { seen.add(k); cues.push([t, text]); }
    }
    return cues.sort((a, b) => a[0] - b[0]);
  }

  // Watch the screen: sample every `every` seconds (default 2) and keep a frame whenever anything on
  // screen changed, however small (a filter, a typed number, a popup), plus at least every `minGap`
  // seconds. The presenter's webcam moves constantly, so grid cells that change in most samples
  // (the face cam) are learned during the first samples and ignored when judging "changed".
  // Kept frames are also tiled 3x3 into labelled contact sheets for fast review.
  async function frames(v, ids, o) {
    v.muted = true; v.pause();
    const GW = 64, GH = 36, CELLS = GW * GH;
    const small = document.createElement("canvas"); small.width = GW; small.height = GH;
    const sctx = small.getContext("2d", { willReadFrequently: true });
    const big = document.createElement("canvas");
    const scale = Math.min(1, o.width / v.videoWidth);
    big.width = Math.round(v.videoWidth * scale); big.height = Math.round(v.videoHeight * scale);
    const bctx = big.getContext("2d");
    const TW = 640, TH = Math.round(640 * v.videoHeight / v.videoWidth);
    const sheet = document.createElement("canvas"); sheet.width = TW * 3; sheet.height = TH * 3;
    const shctx = sheet.getContext("2d");
    let tiles = 0, sheetNo = 0, sheetFrom = 0;
    const flushSheet = async (last) => {
      if (!tiles) return;
      sheetNo++;
      await post("/sheet", { ...ids, index: sheetNo, from: sheetFrom, to: last, data: sheet.toDataURL("image/jpeg", 0.7) });
      shctx.clearRect(0, 0, sheet.width, sheet.height); tiles = 0;
    };
    const grid = () => { sctx.drawImage(v, 0, 0, GW, GH); const d = sctx.getImageData(0, 0, GW, GH).data; const g = new Float32Array(CELLS); for (let i = 0; i < CELLS; i++) g[i] = (d[i * 4] + d[i * 4 + 1] + d[i * 4 + 2]) / 765; return g; };
    const busy = new Uint16Array(CELLS), mask = new Uint8Array(CELLS);
    let prevSample = null, lastKeptGrid = null, lastKept = -1e9, samples = 0;
    const kept = [];
    const seek = (t) => new Promise((res) => { const done = () => res(); v.addEventListener("seeked", done, { once: true }); v.currentTime = t; setTimeout(done, 8000); });
    const mmss = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
    // A seek only counts once a new frame is actually painted (a throttled background tab can keep
    // returning the same old frame); frozen captures are flagged in the result for re-capture.
    const painted = () => new Promise((res) => { if (!v.requestVideoFrameCallback) return res(); const to = setTimeout(res, 500); v.requestVideoFrameCallback(() => { clearTimeout(to); res(); }); });
    let identical = 0;
    for (let t = 1; t < v.duration - 0.3 && kept.length < o.maxFrames; t += o.every) {
      await seek(t);
      await painted();
      await sleep(90);
      const g = grid();
      if (prevSample) {
        samples++;
        for (let i = 0; i < CELLS; i++) if (Math.abs(g[i] - prevSample[i]) > 0.03) busy[i]++;
        if (samples === o.learn) for (let i = 0; i < CELLS; i++) mask[i] = busy[i] >= o.learn * 0.6 ? 1 : 0;
      }
      prevSample = g;
      // Sharp changes (a click, a page load, a text overlay appearing) are kept straight away; soft
      // changes spread over the frame (a talking head moving, lighting) at most every `softGap` s.
      let changed = 0, sharp = 0;
      if (lastKeptGrid) for (let i = 0; i < CELLS; i++) {
        if (mask[i]) continue;
        const d = Math.abs(g[i] - lastKeptGrid[i]);
        if (d > 0.06) changed++;
        if (d > 0.2) sharp++;
      }
      if (!lastKeptGrid || sharp >= o.sharpCells || (changed >= o.minCells && t - lastKept >= o.softGap) || t - lastKept >= o.minGap) {
        bctx.drawImage(v, 0, 0, big.width, big.height);
        await post("/frame", { ...ids, t, data: big.toDataURL("image/jpeg", 0.72) });
        if (!tiles) sheetFrom = t;
        const x = (tiles % 3) * TW, y = Math.floor(tiles / 3) * TH;
        shctx.drawImage(v, x, y, TW, TH);
        shctx.fillStyle = "rgba(0,0,0,.75)"; shctx.fillRect(x, y, 110, 34);
        shctx.fillStyle = "#ffeb3b"; shctx.font = "bold 26px Consolas, monospace"; shctx.fillText(mmss(t), x + 8, y + 26);
        tiles++;
        if (tiles === 9) await flushSheet(t);
        if (lastKeptGrid) { let same = true; for (let i = 0; i < CELLS && same; i++) if (Math.abs(g[i] - lastKeptGrid[i]) > 0.004) same = false; identical = same ? identical + 1 : 0; }
        if (identical >= 6) o.frozen = true;
        kept.push(Math.round(t)); lastKept = t; lastKeptGrid = g;
      }
    }
    await flushSheet(lastKept);
    return kept;
  }

  async function imageData(url) {
    const blob = await (await fetch(url)).blob();
    const bmp = await createImageBitmap(blob);
    const c = document.createElement("canvas");
    const s = Math.min(1, 1600 / bmp.width);
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.8);
  }

  window.JarvisSkool = {
    courses() {
      return data().allCourses.map((c, i) => ({ order: i + 1, id: c.id, name: c.name, title: c.metadata.title,
        desc: c.metadata.desc, modules: c.metadata.numModules }));
    },
    lessons() {
      return data().course.children.map(unit).map((m, i) => ({ order: i + 1, id: m.id, title: m.metadata.title,
        hasVideo: !!m.metadata.videoId, resources: parseResources(m.metadata.resources) }));
    },
    // Capture every lesson of the open course, one after another, without reloading the page:
    // each lesson's data comes from its own page HTML, and the player is reached by clicking the
    // lesson in Skool's sidebar. Progress is kept on window.__jarvisCourse for polling.
    async captureCourse(opts) {
      const o = Object.assign({ from: 1 }, opts);
      const lessons = this.lessons();
      const st = window.__jarvisCourse = { total: lessons.length, done: [], current: null, error: null, finished: false };
      for (const l of lessons.filter((x) => x.order >= o.from && (!o.only || o.only.includes(x.order)))) {
        st.current = l.title;
        try {
          const html = await (await fetch(location.pathname + "?md=" + l.id)).text();
          const nd = JSON.parse(new DOMParser().parseFromString(html, "text/html").getElementById("__NEXT_DATA__").textContent).props.pageProps;
          const meta = nd.course.children.map(unit).find((m) => m.id === l.id);
          const link = [...document.querySelectorAll("a")].find((a) => a.textContent.trim() === l.title);
          // Wait for the previous lesson's player to go away, so the old video is never captured again.
          const oldV = deep(document).find((e) => e.tagName === "VIDEO");
          if (link) { link.click(); await sleep(2500); }
          for (let i = 0; i < 40 && oldV && oldV.isConnected; i++) await sleep(250);
          deep(document).filter((e) => e.tagName === "VIDEO").forEach((v) => { if (!new URL(location.href).searchParams.get("md")?.startsWith(l.id.slice(0, 8))) v.pause(); });
          st.done.push(await this.captureLesson({ ...o, lessonOrder: l.order, meta }));
        } catch (e) { st.done.push({ lesson: l.title, error: String(e) }); }
      }
      st.current = null; st.finished = true;
      return st;
    },
    async captureLesson(opts) {
      const o = Object.assign({ every: 2, minGap: 30, minCells: 6, sharpCells: 4, softGap: 8, learn: 20, maxFrames: 500, width: 1280 }, opts);
      const pp = data();
      const md = new URL(location.href).searchParams.get("md");
      const mods = pp.course.children.map(unit);
      const idx = o.meta ? mods.findIndex((m) => m.id === o.meta.id) : mods.findIndex((m) => m.id === md);
      if (idx < 0) throw new Error("open a lesson (?md=<id>) first");
      const m = o.meta || mods[idx];
      const ids = { course_order: o.courseOrder, course_title: o.courseTitle || unit(pp.course).metadata.title,
        lesson_order: o.lessonOrder || idx + 1, lesson_title: m.metadata.title };
      bundle = { format: "jarvis-skool/1", frames: [], images: [], sheets: [], attachments: [] };
      const body = richText(m.metadata.desc);
      const resources = parseResources(m.metadata.resources);
      const v = m.metadata.videoId ? await ensureVideo() : null;
      const cues = v ? await captions(v).catch((e) => { console.warn(e); return []; }) : [];
      await post("/lesson", { ...ids, lesson_id: m.id, url: location.origin + location.pathname + "?md=" + m.id,
        description: body.text, links: body.links, resources, image_count: body.images.length,
        video: v ? { duration: Math.round(v.duration), width: v.videoWidth, height: v.videoHeight } : null,
        cues, captured_at: new Date().toISOString() });
      const imgs = [];
      for (let i = 0; i < body.images.length; i++) {
        try { await post("/image", { ...ids, index: i + 1, name: `image-${i + 1}`, data: await imageData(body.images[i]) }); imgs.push(i + 1); }
        catch (e) { console.warn("image", i + 1, e); }
      }
      // Attached files (PDFs, docs, prompt sheets): Skool hands out a short-lived signed link via
      // POST api2.skool.com/files/<file_id>/download-url; the file itself comes from files.skool.com.
      let files = 0;
      for (const r of resources.filter((x) => x.file_id)) {
        try {
          const url = (await (await fetch(`https://api2.skool.com/files/${r.file_id}/download-url?expire=28800`, { method: "POST", credentials: "include" })).text()).trim();
          const blob = await (await fetch(url)).blob();
          const dataUrl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
          bundle.attachments.push({ ...ids, name: r.file_name || r.title || `file-${files + 1}`, title: r.title, content_type: r.file_content_type || blob.type, data: dataUrl });
          files++;
        } catch (e) { console.warn("attachment", r.title, e); }
      }
      const kept = v ? await frames(v, ids, o) : [];
      const file = `jarvis-skool-${String(ids.course_order).padStart(2, "0")}-${String(ids.lesson_order).padStart(2, "0")}-${fileSlug(ids.lesson_title)}.json`;
      const bytes = download(file);
      bundle = null;
      return { file, mb: +(bytes / 1048576).toFixed(1), lesson: ids.lesson_title, video: v ? Math.round(v.duration) : 0, cues: cues.length,
        transcriptChars: cues.reduce((n, c) => n + c[1].length, 0), frames: kept.length, images: imgs.length, sheets: Math.ceil(kept.length / 9), files,
        resources: resources.length, descriptionChars: body.text.length, frozen: !!o.frozen };
    },
  };
})();
