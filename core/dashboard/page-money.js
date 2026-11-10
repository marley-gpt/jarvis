// Money: today, the week against the A$380 target, ROAS against breakeven, the last 14 days, the month by category,
// each test's profit and loss, and the latest ledger lines. Real figures from the accountant's ledger only; when
// the sales watch hasn't pulled lately it says so. Data: sections money and capacity.
(function () {
  const { F, link, ref, chip, bar, block } = App, esc = J.esc;
  const $ = (s, el) => el.querySelector(s);
  const tone = (v) => v > 0 ? "good" : v < 0 ? "bad" : "";

  function figsHTML(m) {
    const f = m.figures, r = m.roas, be = r.breakeven;
    const roas = (w) => `<span class="${w.verdict === "above" ? "good" : w.verdict === "below" ? "bad" : ""}">${F.x(w.roas)}</span>`;
    const week = f.weekly_target_aud ? f.profit_week_aud / f.weekly_target_aud : 0;
    return `<div class="figs">
      <div><div class="k">Sales today</div><div class="v">${F.aud(f.sales_aud)}</div><div class="s">${F.plural(f.sales_count, "order")}</div></div>
      <div><div class="k">Ad spend today</div><div class="v">${F.aud(f.ad_spend_aud)}</div><div class="s">all costs ${F.aud(f.costs_today_aud)}</div></div>
      <div><div class="k">Profit today</div><div class="v ${tone(f.profit_today_aud)}">${F.aud(f.profit_today_aud)}</div><div class="s">daily share of target ${F.aud(f.daily_target_aud)}</div></div>
      <div><div class="k">ROAS today</div><div class="v">${roas(r.today)}</div><div class="s">${r.today.roas == null ? "no ad spend today" : `${F.aud(r.today.revenue_aud)} on ${F.aud(r.today.spend_aud)}`}</div></div>
      <div><div class="k">ROAS, 7 days</div><div class="v">${roas(r.week)}</div><div class="s">${be ? `breakeven ${F.x(be)}` : "no breakeven line set"}</div></div>
      <div><div class="k">Week against ${F.aud(f.weekly_target_aud, 0)}</div><div class="v ${tone(f.profit_week_aud)}">${F.aud(f.profit_week_aud)}</div>
        <div class="s" style="margin-top:6px">${bar(week, week >= 1 ? "good" : "")}</div><div class="s">${f.week_gap_aud >= 0 ? "ahead by" : "short by"} ${F.aud(Math.abs(f.week_gap_aud))}</div></div>
    </div>`;
  }

  // Profit per day as bars about a zero line: the only chart on the page, drawn as plain SVG.
  function daysHTML(days) {
    const max = Math.max(1, ...days.map((d) => Math.abs(d.profit))), w = 100 / days.length;
    const bars = days.map((d, i) => {
      const h = Math.abs(d.profit) / max * 44;
      return `<rect x="${(i * w + w * .18).toFixed(2)}%" y="${d.profit >= 0 ? 50 - h : 50}" width="${(w * .64).toFixed(2)}%" height="${Math.max(h, d.profit ? 1 : 0)}" fill="${d.profit >= 0 ? "var(--good)" : "var(--bad)"}"><title>${d.day}: profit ${F.aud(d.profit)} (in ${F.aud(d.income)}, out ${F.aud(d.expenses)})</title></rect>`;
    }).join("");
    const any = days.some((d) => d.income || d.expenses);
    return `<div class="body"><svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:120px;display:block" role="img" aria-label="Profit per day, last 14 days">
      <line x1="0" x2="100%" y1="50" y2="50" stroke="var(--line-2)" vector-effect="non-scaling-stroke"/>${bars}</svg>
      <div class="row faint" style="justify-content:space-between"><span>${esc(days[0].day.slice(5))}</span><span>${any ? `largest day ${F.aud(max)}` : "Nothing in the ledger for these 14 days"}</span><span>${esc(days[days.length - 1].day.slice(5))}</span></div></div>`;
  }

  const catRows = (obj, sign) => Object.entries(obj).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="n ${sign > 0 ? "" : "dim"}">${F.aud(v)}</td></tr>`).join("");
  function monthHTML(mo) {
    return `<table><tbody>${catRows(mo.income, 1) || `<tr><td class="dim">No income recorded this month</td><td></td></tr>`}
      <tr><td><b>Income</b></td><td class="n"><b>${F.aud(mo.total_income)}</b></td></tr>${catRows(mo.expenses, -1)}
      <tr><td><b>Costs</b></td><td class="n"><b>${F.aud(mo.total_expenses)}</b></td></tr>
      <tr><td><b>Profit</b></td><td class="n ${tone(mo.profit)}"><b>${F.aud(mo.profit)}</b></td></tr>
      <tr><td class="dim">Target for ${mo.days} days</td><td class="n dim">${F.aud(mo.target)}</td></tr></tbody></table>`;
  }

  App.register("money", {
    title: "Money",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>Money</h1><p id="mSum"></p><span class="grow"></span>
            <button class="btn" data-act="say" data-text="Pull the latest sales and ad spend now and tell me where the week stands.">Pull the latest now</button>
            <button class="btn" data-act="say" data-text="Give me this week's profit and loss and what each product test has cost so far.">Ask the accountant</button></header>
          <div class="stack">
            <section class="panel"><div id="mFigs"></div></section>
            <div class="cols even">
              <section class="panel"><h2>Profit per day <small>last 14 days</small></h2><div id="mDays"></div></section>
              <section class="panel"><h2>This month <small>by category</small></h2><div id="mMonth"></div></section>
            </div>
            <section class="panel"><h2>Product tests <small>profit and loss per test, US dollars</small></h2><div id="mTests"></div></section>
            <div class="cols even">
              <section class="panel"><h2>Ledger <small>latest lines</small></h2><div id="mLedger"></div></section>
              <section class="panel"><h2>Sales watch</h2><div id="mWatch"></div></section>
            </div>
          </div>`;
      }
      const m = S.money, r = m.roas;
      $("#mSum", el).innerHTML = r.live ? `Live through the ${esc(r.source_label)}, pulled ${r.age_minutes} min ago`
        : `<span class="amber">No live data.</span> ${esc(r.why_not_live)} The figures below are the ledger as it stands.`;
      block($("#mFigs", el), figsHTML(m));
      block($("#mDays", el), daysHTML(m.daily));
      block($("#mMonth", el), monthHTML(m.month));
      block($("#mTests", el), m.tests.length ? `<div class="scroll-x"><table><thead><tr><th>Test</th><th>Product</th><th>Status</th><th class="n">Revenue</th><th class="n">Ad spend</th><th class="n">Other costs</th><th class="n">Profit</th><th class="n">Purchases</th><th></th></tr></thead><tbody>${
        m.tests.map((t) => `<tr><td>${ref(t.test)}</td><td>${esc(t.product)}</td><td>${chip(t.status)}</td><td class="n">${F.usd(t.revenue)}</td><td class="n">${F.usd(t.ad_spend)}</td>
          <td class="n">${F.usd(t.total_costs - t.ad_spend)}</td><td class="n ${tone(t.profit)}">${F.usd(t.profit)}</td><td class="n">${F.num(t.purchases)}</td>
          <td class="dim">${t.sufficient ? "" : "insufficient data"}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No product test has been registered yet.</div>`);
      block($("#mLedger", el), m.ledger.length ? `<div class="scroll-x"><table class="tight"><tbody>${m.ledger.map((l) => `<tr><td class="num dim">${esc(l.day.slice(5))}</td><td>${esc(l.category)}</td>
        <td class="clip dim" style="max-width:240px" title="${esc(l.note || "")}">${link(l.note || l.product || "")}</td><td class="n ${l.kind === "income" ? "good" : ""}">${l.kind === "income" ? "" : "-"}${F.aud(l.amount_aud)}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">The ledger is empty.</div>`);
      const ai = m.ai, lw = m.last_watch;
      block($("#mWatch", el), `<ul class="list">
        <li class="row"><span class="dot ${r.live ? "run" : "wait"}"></span><span>${r.live ? "Live" : "Not live"}</span><span class="dim">${lw ? `last pull ${F.ago(lw.pulled)}${(lw.errors || []).length ? `, errors: ${esc(lw.errors.join("; "))}` : ""}` : "never pulled"}</span></li>
        <li class="row"><span class="dim">Sources</span><span>${m.sources.shopify && m.sources.meta ? "Shopify and Meta read-only tokens" : "the claude.ai Shopify and Meta connectors (tokens not set: bin/jarvis sales setup)"}</span></li>
        <li><div class="row"><span class="dim">AI and API spend this month</span><span class="num">${F.aud(ai.spent)} of ${F.aud(ai.budget, 0)}</span></div>${bar(ai.budget ? ai.spent / ai.budget : 0, ai.ok ? "" : "bad")}</li>
        ${m.announcements.slice(0, 5).map((a) => `<li><span class="faint num">${esc(a.ts.slice(5, 16).replace("T", " "))}</span> ${link(a.say)}</li>`).join("")}</ul>`);
    },
  });
})();
