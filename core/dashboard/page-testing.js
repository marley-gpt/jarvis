// Testing (the home page): the product-testing cockpit. Every product on its rail of stages with what blocks it
// and the one next action; each test's numbers per day against Blake's day 1/2/3 rules, with "insufficient data"
// said plainly; the kill rule; and who is working on it right now. Data: sections pipeline, tests, inbox, live, money.
(function () {
  const { F, link, ref, chip, bar, block } = App, esc = J.esc;
  const $ = (s, el) => el.querySelector(s);

  const SMALL = { done: "done", carried: "from the last round", active: "in progress", queued: "queued", blocked: "blocked", waiting: "waiting for you", todo: "" };
  const stop = (s) => `<div class="stop ${s.state}${s.here ? " here" : ""}">${esc(s.label)}<small>${
    s.open ? `${s.open} open` : SMALL[s.state] || ""}</small></div>`;

  function nextHTML(p) {
    const n = p.next, name = `${p.name} (${p.slug})`;
    const ask = (label, text, cls = "") => `<button class="btn ${cls}" data-act="say" data-text="${esc(text)}">${esc(label)}</button>`;
    let main = "";
    if (n.kind === "approve") main = `<button class="btn ok" data-act="approve" data-id="${n.approval}">Approve A${n.approval}</button>
      <button class="btn no" data-act="reject" data-id="${n.approval}">Reject</button>`;
    else if (n.kind === "read") main = `<button class="btn primary" data-act="scroll" data-to="test-${n.test}">Open ${n.test}</button>`;
    else if (n.kind === "research") main = ask("Start product research", `Start product research on ${name} with Blake's product selection checklist and the Master Key tools. Come back with go, caution or stop.`, "primary")
      + ask("Run the whole pipeline", `Start the ads for ${name}: run the product pipeline end to end and bring me the review package.`);
    else if (n.kind === "start") main = ask(`Start ${n.task}`, `Start ${n.task} for ${name} now. Hand it to the right specialist, as a durable worker if it takes more than a few minutes, and tell me when it is done.`, "primary");
    else if (n.kind === "watch") main = `<a class="btn" href="/work/${n.task}">Watch ${n.task}</a>`;
    else if (n.kind === "debate") main = `<button class="btn primary" data-act="debate" data-kind="kill" data-product="${esc(p.slug)}" data-question="${esc(`${p.name}: retest with new creatives, or drop it and move to the next product?`)}">Put it to the council</button>`;
    else main = ask("Run the product pipeline", `Start the ads for ${name}: run the product pipeline end to end and bring me the review package.`, "primary");
    return `<div class="next"><span class="dim">Next</span><span>${link(n.label)}</span><span class="grow"></span>${main}</div>`;
  }

  function productHTML(p) {
    const last = p.last_test;
    return `<div class="product" id="p-${esc(p.slug)}">
      <div class="product-head"><h3>${esc(p.name)}</h3>${p.round > 1 ? `<span class="chip">Round ${p.round}</span>` : ""}
        ${last ? `<span class="dim">${ref(last.id)} ${esc(last.status)}${last.verdict ? `: ${esc(last.verdict)}` : ""}</span>` : ""}
        <span class="grow"></span><a class="ref" data-ref="product:${esc(p.slug)}">Records</a></div>
      <div class="rail">${p.stages.map(stop).join("")}</div>
      ${p.blockers.length ? `<ul class="blockers">${p.blockers.map((b) => `<li>${link(b)}</li>`).join("")}</ul>` : ""}
      ${nextHTML(p)}
      ${p.open_count ? `<details style="margin-top:8px"><summary>${F.plural(p.open_count, "open task")}</summary>${p.open.map((t) =>
        `<div class="row" style="flex-wrap:nowrap;padding:3px 0">${ref(t.id)}<span class="clip grow">${esc(t.title)}</span><span class="dim">${esc(t.agent || "JARVIS")}</span>${
          t.worker ? `<a class="chip run" href="/work/${t.worker}">${t.worker}</a>` : chip(t.status)}</div>`).join("")}</details>` : ""}
    </div>`;
  }

  function pipelineHTML(pl) {
    const hunts = pl.hunts.map((h) => `<li class="row" style="flex-wrap:nowrap">${ref(h.id)}<span class="clip grow">${esc(h.title)}</span>${
      h.worker ? `<a class="chip run" href="/work/${h.worker}"><span class="dot run"></span>${h.worker} researching</a>` : chip(h.status)}</li>`).join("");
    return (pl.products.length ? pl.products.map(productHTML).join("")
      : `<div class="empty">No product is in the pipeline. Add a candidate on the right, or ask JARVIS for a product hunt.</div>`)
      + (hunts ? `<div class="product"><div class="product-head"><h3>Finding the next candidate</h3><span class="dim">research with no product registered yet</span></div><ul class="list" style="margin-top:6px">${hunts}</ul></div>` : "")
      + (pl.archived.length ? `<div class="product"><details><summary>${F.plural(pl.archived.length, "archived product")}</summary>${pl.archived.map((p) =>
        `<div style="padding:3px 0"><a class="ref" data-ref="product:${esc(p.slug)}">${esc(p.name)}</a> <span class="dim">${link(p.notes || "")}</span></div>`).join("")}</details></div>` : "");
  }

  const HEAD = { cut: "Cut", continue: "Continue", diagnose: "Diagnose", scale: "Profitable" };
  function testHTML(t) {
    const tot = t.totals;
    let verdict, cls, why;
    if (!t.days.length) [verdict, cls, why] = ["No numbers yet", "insufficient", "No daily read has been recorded for this test."];
    else if (!t.sufficient) [verdict, cls, why] = ["Insufficient data", "insufficient",
      `Under ${F.usd(t.minimums.spend_usd, 0)} of spend or ${t.minimums.clicks} clicks, so this is neither a win nor a loss. Blake's day rule so far: ${t.why}`];
    else [verdict, cls, why] = [HEAD[t.call], t.call, t.why];
    if (!t.live && t.status !== "planned") {   // an ended test shows how it ended, not a live call
      verdict = t.status[0].toUpperCase() + t.status.slice(1);
      cls = t.status === "passed" ? "continue" : t.status === "failed" ? "cut" : "insufficient";
      why = `${t.verdict || "No verdict recorded"}.${t.sufficient ? "" : ` Too little data to judge the product itself (under ${F.usd(t.minimums.spend_usd, 0)} of spend or ${t.minimums.clicks} clicks).`}`;
    }
    const row = (d, label, tag) => `<tr><td style="white-space:nowrap">${label}</td><td>${tag}</td><td class="n">${F.usd(d.spend)}</td><td class="n">${F.num(d.clicks)}</td>
      <td class="n">${d.cpc == null ? "n/a" : F.usd(d.cpc)}</td><td class="n">${F.pct(d.ctr)}</td><td class="n">${F.num(d.atc)}</td><td class="n">${F.num(d.checkouts)}</td>
      <td class="n">${F.num(d.purchases)}</td><td class="n">${d.cpa == null ? "n/a" : F.usd(d.cpa)}</td><td class="n">${F.usd(d.revenue)}</td><td class="n">${F.x(d.roas)}</td></tr>`;
    const be = t.breakeven, scale = Math.max(2, (be || 0) * 1.6, (tot.roas || 0) * 1.15);
    return `<section class="panel" id="test-${t.id}">
      <h2>${ref(t.id)} ${esc(t.product)} ${chip(t.status)}<span class="dim">${t.live ? `day ${t.day_n ?? "?"} of ${t.planned_days}, ${F.usd(t.budget_usd, 0)} budget`
        : t.ended ? `ended ${F.ago(t.ended)}` : "not started"}</span><span class="grow"></span><a class="ref" data-ref="${t.id}">Plan and criteria</a></h2>
      <div class="call"><span class="verdict ${cls}">${esc(verdict)}</span><span class="dim">${link(why)}</span></div>
      ${t.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      ${t.days.length ? `<div class="scroll-x"><table class="tight"><thead><tr><th>Day</th><th>Blake's day rule</th><th class="n">Spend</th><th class="n">Clicks</th><th class="n">CPC</th><th class="n">CTR</th>
        <th class="n">ATC</th><th class="n">Checkout</th><th class="n">Sales</th><th class="n">CPA</th><th class="n">Revenue</th><th class="n">ROAS</th></tr></thead><tbody>
        ${t.days.map((d) => row(d, `<span title="${esc(d.source)}">Day ${d.n}${/^\d+$/.test(d.day) ? "" : ` <span class="faint">${esc(d.day)}</span>`}</span>`,
          `<span class="tag ${d.call}" title="${esc(d.why)}">${esc(HEAD[d.call])}</span>`)).join("")}
        ${row({ ...tot, cpa: tot.cpa, cpc: tot.cpc }, "<b>Total</b>", "")}</tbody></table></div>
      <div class="gauge"><div class="row"><span>ROAS ${F.x(tot.roas)}</span><span class="dim">${be ? `breakeven ${F.x(be)}` : "no breakeven line set (bin/jarvis sales breakeven)"}</span>
        ${t.roas_verdict ? `<span class="${t.roas_verdict === "above" ? "good" : "bad"}">${t.roas_verdict} breakeven</span>` : ""}
        ${t.pnl ? `<span class="grow"></span><span class="dim">test profit</span><span class="num ${t.pnl.profit < 0 ? "bad" : "good"}">${F.usd(t.pnl.profit)}</span>` : ""}</div>
        ${bar((tot.roas || 0) / scale, t.roas_verdict === "above" ? "good" : t.roas_verdict === "below" ? "bad" : "", be ? be / scale : null)}
        <div class="faint">Figures in US dollars as recorded from Meta and Shopify. The rule is judged on the totals to date; ${esc(t.rule_source)}.</div></div>` : ""}
      ${t.ads.length ? `<details style="padding:8px 12px;border-top:1px solid var(--line)"><summary>${F.plural(t.ads.length, "creative read")} from the daily feedback loop</summary>
        <div class="faint" style="margin:6px 0">Written reads per ad. Spend, CTR and CPA per creative are not stored as figures yet.</div>
        ${t.ads.map((a) => `<div class="row" style="flex-wrap:nowrap;padding:2px 0">${a.verdict ? `<span class="tag">${esc(a.verdict)}</span>` : ""}<a class="clip grow" data-ref="${a.id}" style="cursor:pointer;color:var(--ink)">${esc(a.title)}</a></div>`).join("")}</details>` : ""}
      ${!t.live && t.lesson ? `<details style="padding:8px 12px;border-top:1px solid var(--line)"><summary>Lesson recorded</summary><div class="md" style="margin-top:6px">${link(t.lesson)}</div></details>` : ""}
    </section>`;
  }

  function killHTML(m) {
    const f = m.figures, kill = m.kill_aud || 0, frac = kill ? f.spend_since_sale_aud / kill : 0;
    return `<div class="gauge"><div class="row"><span class="num" style="font-size:17px">${F.aud(f.spend_since_sale_aud)}</span><span class="dim">on ads since the last sale${
      f.days_without_sale ? `, over ${F.plural(f.days_without_sale, "day")}` : ""}</span></div>
      ${bar(frac, frac >= 1 ? "bad" : frac >= .7 ? "amber" : "", 1)}
      <div class="dim">${frac >= 1 ? `<span class="bad">Kill rule hit.</span> ` : ""}Your kill line is ${F.aud(kill, 0)} without a sale. Last sale: ${esc(f.last_sale_day || "none recorded yet")}.</div></div>`;
  }

  App.acts.scroll = (el) => document.getElementById(el.dataset.to)?.scrollIntoView({ block: "start", behavior: "smooth" });
  App.acts.candidate = (el) => {
    const form = el.closest("form"), name = form.elements.name.value.trim(), link_ = form.elements.link.value.trim();
    if (!name) return App.toast("Give the product a name first.", "err");
    App.say(`New product candidate: ${name}.${link_ ? ` Supplier or reference link: ${link_}.` : ""} Register it and ${
      el.dataset.mode === "pipeline" ? "start the ads: run the product pipeline end to end and bring me the review package."
        : "start product research with Blake's product selection checklist and the Master Key tools. Come back with go, caution or stop."}`);
    form.reset();
    App.toast("Sent to JARVIS");
  };

  App.register("testing", {
    title: "Testing",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>Testing</h1><p id="tSum"></p><span class="grow"></span>
            <button class="btn" data-act="say" data-text="Run a product hunt now with the Master Key tools and Blake's checklist, and send me a short list of candidates.">Run a product hunt</button>
            <button class="btn" data-act="debate" data-kind="product">Put a call to the council</button></header>
          <div class="cols"><div class="stack">
            <section class="panel" id="tNeedsP" hidden><h2>Waiting for you <a class="dim" href="/work" id="tNeedsAll"></a></h2><div id="tNeeds"></div></section>
            <section class="panel"><h2>Pipeline <small>candidate to verdict</small></h2><div id="tPipe"></div></section>
            <div class="stack" id="tTests"></div>
          </div><div class="stack">
            <section class="panel"><h2>Working now <a class="dim" href="/team">Team</a></h2><div id="tLive"></div><div class="feed" id="tFeed" style="max-height:220px;border-top:1px solid var(--line)"></div></section>
            <section class="panel"><h2>Kill rule</h2><div id="tKill"></div></section>
            <section class="panel"><h2>Add a candidate</h2><form class="body stack" style="gap:8px" onsubmit="return false">
              <input class="field" name="name" placeholder="Product name" aria-label="Product name">
              <input class="field" name="link" placeholder="Supplier or reference link (optional)" aria-label="Link">
              <div class="row"><button class="btn primary" data-act="candidate" data-mode="research" type="button">Research it</button>
              <button class="btn" data-act="candidate" data-mode="pipeline" type="button">Run the whole pipeline</button></div>
              <p class="faint">JARVIS registers it and hands it to the specialists. Spending and launching still come back to you for approval.</p></form></section>
          </div></div>`;
      }
      const pl = S.pipeline, live = S.tests.filter((t) => t.live);
      $("#tSum", el).textContent = `${F.plural(pl.products.length, "product")} in the pipeline, ${live.length ? F.plural(live.length, "test") + " live" : "no test live"}`;
      const inbox = S.inbox || [];
      $("#tNeedsP", el).hidden = !inbox.length;
      $("#tNeedsAll", el).textContent = inbox.length > 3 ? `all ${inbox.length}` : "";
      block($("#tNeeds", el), App.inboxHTML(inbox, 3));
      block($("#tPipe", el), pipelineHTML(pl));
      block($("#tTests", el), S.tests.length ? S.tests.map(testHTML).join("")
        : `<section class="panel"><h2>Tests</h2><div class="empty">No test has been registered. A product reaches a live test once its campaign draft is approved.</div></section>`);
      block($("#tLive", el), App.agentsHTML(S.live.agents.filter((a) => a.running)));
      App.feed($("#tFeed", el), S.live.feed, 14, el);
      block($("#tKill", el), killHTML(S.money));
    },
    open(arg) { if (arg) setTimeout(() => (document.getElementById(`test-${arg}`) || document.getElementById(`p-${arg}`))?.scrollIntoView({ block: "start" }), 60); },
  });
})();
