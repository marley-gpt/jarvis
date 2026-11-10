// Brain: search everything JARVIS remembers, read an entry, and see the latest decisions and conversation.
// Data: section brain for the counts and recent records; /api/memory for a search (only when the owner searches).
// /brain/M-261110-cdd9 (or any record id) opens that record.
(function () {
  const { F, link, ref, block } = App, esc = J.esc;
  const $ = (s, el) => el.querySelector(s);
  let q = "", cat = "", results = null, timer = null, n = 0;

  const entry = (m) => `<li data-ref="${esc(m.id)}" style="cursor:pointer"><div class="row" style="flex-wrap:nowrap"><span class="clip grow">${esc(m.title)}</span>
      <span class="chip">${esc(m.category)}</span>${m.provenance === "mentor" ? `<span class="chip done">Blake</span>` : m.provenance === "user" ? `<span class="chip">you</span>` : ""}
      ${m.status === "needs-review" ? `<span class="chip wait">needs review</span>` : ""}</div>
    ${m.snippet ? `<div class="dim">${esc(m.snippet.replace(/[\[\]]/g, ""))}</div>` : ""}<div class="faint num">${esc(m.id)}${m.product ? `, ${esc(m.product)}` : ""}, ${esc(F.day(m.updated))}</div></li>`;

  async function search() {
    const mine = ++n;
    if (!q && !cat) { results = null; return App.draw(); }
    try {
      const rows = await App.get(`/api/memory?q=${encodeURIComponent(q)}&c=${encodeURIComponent(cat)}`);
      if (mine === n) { results = rows; App.draw(); }
    } catch (e) { App.toast(e.message, "err"); }
  }
  App.acts["brain-cat"] = (el) => { cat = cat === el.dataset.c ? "" : el.dataset.c; search(); };

  App.register("brain", {
    title: "Brain",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>Brain</h1><p id="bSum"></p></header>
          <div class="cols"><div class="stack">
            <section class="panel"><div class="body"><input class="field" id="bQ" type="search" placeholder="Search memory: a product, a rule, something Blake said" aria-label="Search memory" autocomplete="off">
              <div class="row" id="bCats" style="margin-top:8px;gap:4px"></div></div><ul class="list" id="bList" style="border-top:1px solid var(--line)"></ul></section>
          </div><div class="stack">
            <section class="panel"><h2>Decisions</h2><ul class="list" id="bDec"></ul></section>
            <section class="panel"><h2>Conversation <small>from the journal</small></h2><div id="bJournal" class="body stack" style="gap:8px"></div></section>
          </div></div>`;
        $("#bQ", el).addEventListener("input", (e) => { q = e.target.value.trim(); clearTimeout(timer); timer = setTimeout(search, 180); });
      }
      const b = S.brain;
      $("#bSum", el).textContent = `${F.num(b.total)} entries${b.needs_review ? `, ${b.needs_review} need review` : ""}`;
      block($("#bCats", el), b.categories.map((c) => `<button class="btn sm ${cat === c.name ? "primary" : "ghost"}" data-act="brain-cat" data-c="${esc(c.name)}">${esc(c.name)} <b>${c.n}</b></button>`).join(""));
      const rows = results || b.recent;
      block($("#bList", el), rows.length ? (results ? "" : `<li class="faint">Latest entries</li>`) + rows.map(entry).join("")
        : `<li class="dim">Nothing in memory matches. Try fewer words, or another category.</li>`);
      block($("#bDec", el), b.decisions.map((d) => `<li>${ref(`D${d.id}`)} <span class="faint num">${esc(F.day(d.ts))}</span><div>${link(d.decision.length > 220 ? d.decision.slice(0, 220) + "…" : d.decision)}</div></li>`).join("")
        || `<li class="dim">No decisions recorded.</li>`);
      block($("#bJournal", el), b.journal.slice(0, 12).map((j) => `<div><span class="faint num">${esc(j.ts.slice(5, 16).replace("T", " "))} ${esc(j.role)}</span><div class="${j.role === "owner" ? "" : "dim"}">${link(j.text.length > 300 ? j.text.slice(0, 300) + "…" : j.text)}</div></div>`).join("")
        || `<div class="dim">Nothing said yet.</div>`);
    },
    open(arg) { if (arg) App.record(arg); },
  });
})();
