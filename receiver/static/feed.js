/* Byit Channels - read-only WhatsApp channel feed. No framework; spring motion lives in feed.css. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app"), list = $("#channels"), feed = $("#feed"), inner = $("#feedInner");
  const S = { scope: "all", channels: [], jid: null, oldest: 0, newest: 0, hasMore: false, loading: false,
              kind: "", q: "", seen: new Set(), lastSender: null, lastTs: 0, lastDay: "", firstDay: "" };

  // ---------- helpers ----------
  async function api(url, opts) {
    const r = await fetch(url, opts);
    if (r.redirected && r.url.includes("/login")) { location.href = "/login"; throw new Error("signed out"); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || r.statusText);
    return body;
  }
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hue = s => { let h = 0; for (const ch of String(s)) h = (h * 31 + ch.codePointAt(0)) % 360; return h; };
  const initials = name => {
    const w = String(name || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase();
  };
  const avatar = (el, name, key) => { el.textContent = initials(name); el.style.setProperty("--h", hue(key || name)); };
  const d = ts => new Date(ts * 1000);
  const timeOf = ts => d(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dayKey = ts => d(ts).toDateString();
  function dayLabel(ts) {
    const t = d(ts), now = new Date(), y = new Date(); y.setDate(now.getDate() - 1);
    if (t.toDateString() === now.toDateString()) return "Today";
    if (t.toDateString() === y.toDateString()) return "Yesterday";
    const sameYear = t.getFullYear() === now.getFullYear();
    return t.toLocaleDateString("en-GB", { weekday: sameYear ? "long" : undefined, day: "numeric", month: "long", year: sameYear ? undefined : "numeric" });
  }
  function listTime(ts) {
    if (!ts) return "";
    const lbl = dayLabel(ts);
    return lbl === "Today" ? timeOf(ts) : lbl === "Yesterday" ? lbl : d(ts).toLocaleDateString("en-GB", { day: "2-digit", month: "2-digit", year: "2-digit" });
  }
  const size = n => !n ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  const ext = (name, kind) => { const m = /\.([a-z0-9]{2,5})$/i.exec(name || ""); return (m ? m[1] : kind === "pdf" ? "pdf" : "file").toUpperCase(); };
  function format(text) {
    let h = esc(text);
    h = h.replace(/```([\s\S]+?)```/g, "<code>$1</code>")
         .replace(/(^|[\s(])\*(?!\s)([^*\n]+?)\*(?=[\s).,!?:;]|$)/g, "$1<b>$2</b>")
         .replace(/(^|[\s(])_(?!\s)([^_\n]+?)_(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")
         .replace(/(^|[\s(])~(?!\s)([^~\n]+?)~(?=[\s).,!?:;]|$)/g, "$1<s>$2</s>")
         .replace(/\bhttps?:\/\/[^\s<]+[^\s<.,:;"')\]]/g, u => `<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
    if (S.q) {
      const re = new RegExp(`(${S.q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})(?![^<]*>)`, "gi");
      h = h.replace(re, "<mark>$1</mark>");
    }
    return h;
  }

  // ---------- channel list ----------
  async function loadChannels() {
    try { S.channels = await api(`/api/feed/channels?scope=${S.scope}`); } catch (e) { return; }
    renderChannels();
  }
  function renderChannels() {
    const q = $("#channelSearch").value.trim().toLowerCase();
    const items = S.channels.filter(c => !q || c.name.toLowerCase().includes(q));
    if (!items.length) {
      list.innerHTML = `<li class="list-empty">${S.channels.length ? "No channels match." :
        S.scope === "ticked" ? "No inventory chats ticked yet. Tick them on the Chats page." :
        "No messages yet. Run “Sync chats” and “Pull history” on the pipeline page."}</li>`;
      return;
    }
    const prevIds = new Set([...list.children].map(li => li.dataset.jid));
    list.innerHTML = "";
    items.forEach((c, i) => {
      const li = document.createElement("li");
      li.className = "channel-item" + (c.jid === S.jid ? " active" : "");
      li.dataset.jid = c.jid;
      if (!prevIds.has(c.jid)) li.style.setProperty("--i", Math.min(i, 14)); else li.style.animation = "none";
      const icon = /\.(xlsx?|csv)$/i.test(c.preview) ? "📊 " : /\.pdf$/i.test(c.preview) ? "📄 " : c.preview === "Photo" ? "📷 " : "";
      li.innerHTML = `<div class="avatar"></div><div class="name">${esc(c.name)}${c.in_scope ? '<span class="badge">●</span>' : ""}</div>
        <div class="time">${listTime(c.last_ts)}</div><div class="prev">${icon}${esc(c.preview)}</div>`;
      avatar($(".avatar", li), c.name, c.jid);
      li.onclick = () => openChannel(c.jid);
      list.appendChild(li);
    });
  }
  $("#channelSearch").addEventListener("input", renderChannels);
  $("#scopeSeg").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    [...$("#scopeSeg").querySelectorAll("button")].forEach((x, i) => { x.setAttribute("aria-selected", x === b); if (x === b) $("#scopeSeg").dataset.i = i; });
    S.scope = b.dataset.scope; list.innerHTML = ""; loadChannels();
  });

  // ---------- channel ----------
  function resetFeed() {
    inner.innerHTML = ""; S.seen.clear(); S.oldest = S.newest = 0; S.hasMore = false;
    S.lastSender = null; S.lastTs = 0; S.lastDay = ""; S.firstDay = "";
  }
  async function openChannel(jid) {
    const c = S.channels.find(x => x.jid === jid) || { jid, name: jid, count: 0, media: 0 };
    S.jid = jid; history.replaceState(null, "", "#" + encodeURIComponent(jid));
    [...list.children].forEach(li => li.classList.toggle("active", li.dataset.jid === jid));
    $("#emptyState").hidden = true;
    const ch = $("#channel"); ch.hidden = false; ch.classList.remove("enter"); void ch.offsetWidth; ch.classList.add("enter");
    app.classList.add("open");
    avatar($("#chAvatar"), c.name, c.jid);
    $("#chName").textContent = c.name;
    const pl = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    $("#chMeta").textContent = `${c.kind === "private" ? "Private chat" : "Group"} · ${pl(c.count, "post")}` +
      (c.media ? ` · ${pl(c.media, "attachment")}` : "");
    resetFeed();
    inner.innerHTML = skeleton();
    await loadPage(true);
  }
  const skeleton = () => Array.from({ length: 5 }, (_, i) =>
    `<div class="post" style="--i:${i};width:${[62, 48, 70, 40, 56][i]}%;height:${[64, 180, 72, 64, 96][i]}px;position:relative;overflow:hidden"><div class="shimmer"></div></div>`).join("");

  async function loadPage(initial) {
    if (S.loading) return; S.loading = true;
    const jid = S.jid;
    const qs = new URLSearchParams({ jid, limit: initial ? 40 : 60 });
    if (!initial && S.oldest) qs.set("before", S.oldest);
    if (S.q) qs.set("q", S.q); if (S.kind) qs.set("kind", S.kind);
    let res;
    try { res = await api(`/api/feed/messages?${qs}`); }
    catch (e) { S.loading = false; if (initial) inner.innerHTML = `<div class="loader">Couldn't load this channel: ${esc(e.message)}</div>`; return; }
    if (jid !== S.jid) { S.loading = false; return; }
    S.hasMore = res.has_more;
    if (initial) {
      inner.innerHTML = "";
      if (!res.messages.length) inner.innerHTML = `<div class="loader">${S.q || S.kind ? "Nothing matches this filter." : "No posts yet."}</div>`;
      appendPosts(res.messages);
      requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
    } else {
      const before = feed.scrollHeight;
      prependPosts(res.messages);
      feed.scrollTop += feed.scrollHeight - before;
    }
    S.loading = false;
  }

  function postEl(m, cont, i) {
    const el = document.createElement("article");
    el.className = "post" + (cont ? " cont" : "");
    el.dataset.id = m.id; el.style.setProperty("--i", i); el.style.setProperty("--h", hue(m.sender || "?"));
    let html = "";
    if (!cont && m.sender) html += `<div class="who">${esc(m.sender)}</div>`;
    html += `<div class="body"></div>`;
    if (m.text) html += `<div class="text" dir="auto">${format(m.text)}</div>`;
    html += `<div class="meta">${timeOf(m.ts)}</div>`;
    el.innerHTML = html;
    renderAttachment($(".body", el), m);
    return el;
  }
  function dayEl(ts) { const el = document.createElement("div"); el.className = "day"; el.textContent = dayLabel(ts); el.dataset.day = dayKey(ts); return el; }

  function appendPosts(msgs) {
    msgs.forEach((m, i) => {
      if (S.seen.has(m.id)) return; S.seen.add(m.id);
      const k = dayKey(m.ts);
      if (k !== S.lastDay) { inner.appendChild(dayEl(m.ts)); S.lastDay = k; S.lastSender = null; if (!S.firstDay) S.firstDay = k; }
      const cont = m.sender === S.lastSender && m.ts - S.lastTs < 300;
      inner.appendChild(postEl(m, cont, Math.min(i, 16)));
      S.lastSender = m.sender; S.lastTs = m.ts;
      S.newest = Math.max(S.newest, m.ts); S.oldest = S.oldest ? Math.min(S.oldest, m.ts) : m.ts;
    });
    observeMedia();
  }
  function prependPosts(msgs) {
    const frag = document.createDocumentFragment();
    let day = "", sender = null, last = 0;
    msgs.forEach(m => {
      if (S.seen.has(m.id)) return; S.seen.add(m.id);
      const k = dayKey(m.ts);
      if (k !== day) { frag.appendChild(dayEl(m.ts)); day = k; sender = null; }
      const cont = m.sender === sender && m.ts - last < 300;
      const el = postEl(m, cont, 0); el.style.animation = "fade .4s both";
      frag.appendChild(el); sender = m.sender; last = m.ts;
      S.oldest = S.oldest ? Math.min(S.oldest, m.ts) : m.ts;
    });
    const firstSep = inner.querySelector(".day");
    if (firstSep && firstSep.dataset.day === day) firstSep.remove();   // merge the day boundary
    inner.prepend(frag);
    observeMedia();
  }

  // ---------- attachments ----------
  function renderAttachment(box, m) {
    box.innerHTML = "";
    if (m.kind === "text") return;
    if (m.kind === "video") { box.innerHTML = `<div class="chip-note">🎬 Video${m.file_name ? " · " + esc(m.file_name) : ""} — open it in WhatsApp</div>`; return; }
    if (m.kind === "image") {
      const wrap = document.createElement("div");
      if (m.file) {
        wrap.className = "media";
        wrap.innerHTML = `<div class="shimmer"></div><img alt="" loading="lazy" decoding="async" src="/sources/file/${m.file.id}">`;
        const img = $("img", wrap);
        img.onload = () => { img.classList.add("ready"); $(".shimmer", wrap)?.remove(); };
        img.onerror = () => { wrap.innerHTML = `<div class="unavailable">Image couldn't be shown</div>`; wrap.classList.add("placeholder"); };
        wrap.onclick = () => openViewer({ ...m.file, kind: "image" });
      } else {
        wrap.className = "media placeholder";
        if (m.media === "failed") wrap.innerHTML = unavailable(m);
        else { wrap.innerHTML = `<div class="shimmer"></div>`; wrap.dataset.pending = m.id; }
      }
      box.appendChild(wrap); bindRetry(box, m); return;
    }
    const kind = m.file ? m.file.kind : m.kind;
    const name = (m.file && m.file.file_name) || m.file_name || "Attachment";
    const doc = document.createElement("div");
    doc.className = "doc" + (m.file ? " clickable" : "");
    const label = { workbook: "Spreadsheet", csv: "CSV", pdf: "PDF document" }[kind] || "Document";
    doc.innerHTML = `<div class="doc-icon ${kind}">${esc(ext(name, kind))}</div>
      <div><div class="doc-name" dir="auto">${esc(name)}</div><div class="doc-sub">${label}${m.file && m.file.size ? " · " + size(m.file.size) : ""}</div>
      ${!m.file && m.media === "failed" ? `<div class="unavailable">No longer on WhatsApp's servers · <button class="btn-text retry">Retry</button></div>` : ""}</div>
      <div class="doc-actions">${m.file ? `${["workbook", "csv", "pdf"].includes(kind) ? '<button class="btn-text open">Open</button>' : ""}
        <a class="icon-btn" href="/sources/file/${m.file.id}?download=1" download aria-label="Download" onclick="event.stopPropagation()">
        <svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></a>` : ""}</div>`;
    if (!m.file && m.media !== "failed") { doc.dataset.pending = m.id; doc.insertAdjacentHTML("beforeend", '<div class="progress"></div>'); }
    if (m.file && ["workbook", "csv", "pdf"].includes(kind)) doc.onclick = () => openViewer(m.file);
    box.appendChild(doc); bindRetry(box, m);
  }
  const unavailable = () => `<div class="unavailable">Image no longer on WhatsApp's servers<br><button class="btn-text retry">Retry</button></div>`;
  function bindRetry(box, m) {
    const b = $(".retry", box);
    if (b) b.onclick = e => { e.stopPropagation(); renderAttachment(box, { ...m, media: "pending" }); observeMedia(); };
  }

  // Fetch attachments as they scroll into view (two at a time).
  const queue = [], busy = new Set();
  const io = new IntersectionObserver(entries => entries.forEach(en => {
    if (!en.isIntersecting) return;
    io.unobserve(en.target);
    const id = en.target.dataset.pending;
    if (id && !busy.has(id) && !queue.includes(id)) { queue.push(id); pump(); }
  }), { root: feed, rootMargin: "400px 0px" });
  function observeMedia() { inner.querySelectorAll("[data-pending]").forEach(el => io.observe(el)); }
  async function pump() {
    while (busy.size < 2 && queue.length) {
      const id = queue.pop(); busy.add(id);
      (async () => {
        let file = null, failed = false;
        try { file = (await api(`/api/feed/media/${encodeURIComponent(id)}`, { method: "POST" })).file; } catch (e) { failed = true; }
        busy.delete(id);
        const post = inner.querySelector(`.post[data-id="${CSS.escape(id)}"]`);
        if (post) {
          const box = $(".body", post), wasImage = !!$(".media", box);
          const fname = $(".doc-name", box)?.textContent;
          renderAttachment(box, { id, kind: wasImage ? "image" : (file ? file.kind : "document"), file_name: fname,
                                  file, media: failed ? "failed" : "ok" });
        }
        pump();
      })();
    }
  }

  // ---------- scrolling, search, filters, live updates ----------
  const floatDay = $("#floatDay"); let fdt;
  function updateFloatDay() {
    let cur = null;
    for (const el of inner.querySelectorAll(".day")) { if (el.offsetTop - feed.scrollTop <= 12) cur = el; else break; }
    if (!cur) { floatDay.classList.remove("show"); return; }
    floatDay.textContent = cur.textContent; floatDay.classList.add("show");
    clearTimeout(fdt); fdt = setTimeout(() => floatDay.classList.remove("show"), 1200);
  }
  feed.addEventListener("scroll", () => {
    updateFloatDay();
    if (feed.scrollTop < 200 && S.hasMore && !S.loading) loadPage(false);
    if (feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80) $("#newPill").hidden = true;
  }, { passive: true });
  $("#filters").addEventListener("click", e => {
    const b = e.target.closest(".chip"); if (!b || !S.jid) return;
    $("#filters").querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === b));
    S.kind = b.dataset.f === "all" ? "" : b.dataset.f; resetFeed(); inner.innerHTML = skeleton(); loadPage(true);
  });
  $("#searchToggle").onclick = () => {
    const bar = $("#chSearchBar"); bar.hidden = !bar.hidden;
    if (!bar.hidden) $("#chSearch").focus(); else if (S.q) { $("#chSearch").value = ""; S.q = ""; resetFeed(); loadPage(true); }
  };
  let qt; $("#chSearch").addEventListener("input", e => {
    clearTimeout(qt); qt = setTimeout(() => { S.q = e.target.value.trim(); resetFeed(); inner.innerHTML = skeleton(); loadPage(true); }, 300);
  });
  $("#backBtn").onclick = () => { app.classList.remove("open"); S.jid = null; history.replaceState(null, "", location.pathname); renderChannels(); };
  $("#newPill").onclick = () => { feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" }); $("#newPill").hidden = true; };

  async function poll() {
    if (document.hidden) return;
    loadChannels();
    if (!S.jid || S.q || S.kind || S.loading) return;
    try {
      const res = await api(`/api/feed/messages?jid=${encodeURIComponent(S.jid)}&limit=30`);
      const fresh = res.messages.filter(m => !S.seen.has(m.id) && m.ts >= S.newest);
      if (!fresh.length) return;
      const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
      if (inner.querySelector(".loader")) inner.innerHTML = "";
      appendPosts(fresh);
      if (atBottom) feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" }); else $("#newPill").hidden = false;
    } catch (e) { /* offline; try again next tick */ }
  }
  setInterval(poll, 15000);

  // ---------- viewer ----------
  const viewer = $("#viewer"), vbody = $("#viewerBody"), tabs = $("#sheetTabs"), vfilter = $("#viewerFilter");
  let sheets = [], sheetIdx = 0;
  async function openViewer(file) {
    const kind = file.kind, name = file.file_name || "Attachment";
    viewer.hidden = false; viewer.classList.remove("closing");
    $("#viewerTitle").textContent = name;
    $("#viewerSub").textContent = [{ image: "Image", pdf: "PDF", workbook: "Spreadsheet", csv: "CSV" }[kind] || "File", size(file.size)].filter(Boolean).join(" · ");
    const icon = $("#viewerIcon"); icon.className = "doc-icon " + kind; icon.textContent = kind === "image" ? "IMG" : ext(name, kind);
    $("#viewerDownload").href = `/sources/file/${file.id}?download=1`;
    tabs.innerHTML = ""; vfilter.hidden = true; vfilter.value = ""; vbody.className = "viewer-body"; vbody.innerHTML = "";
    if (kind === "image") { vbody.classList.add("image"); vbody.innerHTML = `<img alt="" src="/sources/file/${file.id}">`; return; }
    if (kind === "pdf") { vbody.innerHTML = `<iframe title="${esc(name)}" src="/sources/file/${file.id}#view=FitH"></iframe>`; return; }
    vbody.innerHTML = `<div class="viewer-msg"><div class="shimmer" style="position:relative;width:240px;height:10px;border-radius:5px"></div></div>`;
    try {
      const p = await api(`/api/feed/preview/${file.id}`);
      if (p.type !== "sheets" || !p.sheets.length) throw new Error("No preview for this file type - use download.");
      sheets = p.sheets; vfilter.hidden = false;
      tabs.innerHTML = sheets.map((s, i) => `<button data-i="${i}">${esc(s.name)}<em>${s.total_rows}</em>${s.hidden ? " (hidden)" : ""}</button>`).join("");
      showSheet(Math.max(0, sheets.findIndex(s => !s.hidden)));
    } catch (e) { vbody.innerHTML = `<div class="viewer-msg">${esc(e.message)}</div>`; }
  }
  function showSheet(i) {
    sheetIdx = i;
    tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", +b.dataset.i === i));
    const s = sheets[i], q = vfilter.value.trim().toLowerCase();
    if (!s.rows.length) { vbody.innerHTML = `<div class="viewer-msg">This sheet is empty.</div>`; return; }
    // The header is the fullest row near the top; title lines above it become a caption.
    const fill = r => r.filter(v => String(v).trim()).length;
    let hi = 0;
    s.rows.slice(0, 12).forEach((r, k) => { if (fill(r) > fill(s.rows[hi])) hi = k; });
    const caption = s.rows.slice(0, hi).map(r => r.filter(v => String(v).trim()).join(" · ")).filter(Boolean);
    const head = s.rows[hi], body = s.rows.slice(hi + 1);
    const rows = body.map((r, n) => [n + hi + 2, r]).filter(([, r]) => !q || r.some(v => String(v).toLowerCase().includes(q)));
    const cell = v => `<td dir="auto" title="${esc(v)}">${esc(v)}</td>`;
    vbody.innerHTML = `${caption.length ? `<div class="sheet-caption" dir="auto">${caption.map(esc).join("<br>")}</div>` : ""}
      <table class="grid"><thead><tr><th class="rn">${hi + 1}</th>${head.map(v => `<th dir="auto" title="${esc(v)}">${esc(v)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(([n, r], k) => `<tr style="animation-delay:${Math.min(k, 30) * 12}ms"><td class="rn">${n}</td>${r.map(cell).join("")}</tr>`).join("")}</tbody></table>
      ${s.total_rows > s.rows.length ? `<div class="viewer-note">Showing the first ${s.rows.length} of ${s.total_rows} rows. Download the file for the rest.</div>` : ""}
      ${q && !rows.length ? `<div class="viewer-note">No rows contain “${esc(q)}”.</div>` : ""}`;
  }
  tabs.addEventListener("click", e => { const b = e.target.closest("button"); if (b) showSheet(+b.dataset.i); });
  let vt; vfilter.addEventListener("input", () => { clearTimeout(vt); vt = setTimeout(() => showSheet(sheetIdx), 150); });
  function closeViewer() { viewer.classList.add("closing"); setTimeout(() => { viewer.hidden = true; vbody.innerHTML = ""; }, 260); }
  viewer.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeViewer(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !viewer.hidden) closeViewer();
    if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#channelSearch").focus(); }
  });

  // ---------- start ----------
  loadChannels().then(() => {
    const want = decodeURIComponent(location.hash.slice(1));
    if (want) openChannel(want);
  });
})();
