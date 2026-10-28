---
name: jarvis-product-pipeline
description: JARVIS's one-command pipeline from "start the ads for <product>" to a single review package (research, 15 static ads with copy, product page, campaign draft, test plan) and then launch. Use whenever the owner brings a product to test or asks to start a product's ads. JARVIS (the lead) runs it, delegating to the specialists in the order below.
---

# Product pipeline: product in, one review package out

The owner's goal: they send a product, everything is automated, and they only review **the creatives and the product page** before approving launch. Capital is tight, so images must be right the first or second time. Everything follows Blake's playbooks (`blake-*` skills, private, in `~/.claude/skills/`), the owner's standing rules (jarvis-memory section 0) and the feedback history (`bin/jarvis feedback digest`).

## 0. Kick-off (JARVIS, minutes)
1. `bin/jarvis product add "<name>"`, then `bin/jarvis task add "Launch test: <product>" -p <slug>` = T<p>. Create one subtask per step below, using `--parent T<p> --agent <agent>`.
2. Collect what the owner gave: supplier link, cost, photos. Ask only for what's missing (cost of goods incl. shipping, product photos, supplier). Don't ask anything else.
3. **One spend approval up front**, for image generation: estimate the number of images, about 15 plus about 30% retries, times the per-image credit cost. Run `bin/jarvis cost check <AUD>`, then `bin/jarvis approval request spend "Image generation for <product> test" --task T<p> --amount X --currency AUD`. Nothing paid happens until it's approved.

## 1. Pre-test gate: `product-testing` + `product-research` (parallel)
- Blake's pre-test checks (blake-testing, blake-product-research): price from his pricing formula, minimum margin on landed cost, Meta competition, trend direction, market size, USP.
- Output: a one-page go / caution / stop with reasons. **Stop** needs the owner's call. **Caution** carries on, with the risk shown in the package.

## 2. Research: `market-research` + `creative-research` (parallel)
- market-research follows the blake-brand research procedure: a voice-of-customer bank (100+ verbatim quotes, 4+ sources), the master research report, avatar(s), offer brief, and the top pain and desire themes with counts. The customer-research and market-researcher skills are core here.
- creative-research: funnel-hack the top competitors (ads, angles, offers, pages), plus proven statics in the niche to imitate, never copy (blake-creatives).
- Output paths: `brain/work/<product>/voc-bank.md`, `research-report.md`, `competitor-funnels.md`.

### Research gate (JARVIS checks it; no creative work starts until it passes)
The owner has no room for weak ads (2026-10-28): every ad must be aimed at a real customer, proven by research. JARVIS reads the three files and checks:
- **VoC bank:** 100+ quotes copied word for word, each with its source link. They come from 4+ different places (Reddit, Facebook groups, Amazon/competitor reviews including 1–3 stars, forums, TikTok/YouTube comments). No paraphrasing: Blake says AI rewording changes the customer's words.
- **Report:** the top pains, desires, objections and buying triggers, each with a count and example quote ids. Avatar(s) with awareness level and market sophistication. The offer brief.
- **Competitors:** the top 3–5 competitors' live ads (Meta Ad Library: number of active ads, impressions where shown, and how long each has run), their angles, offers, prices and product pages, and the gaps they leave open. Rank them by those three signals; the ranking picks the PDP model in step 6.
Anything thin goes back to the agent with what's missing. Nothing is invented to fill a gap: mark it `needs-review`. Record the gate verdict with `bin/jarvis feedback add --source qc`.

## 3. Creative build: `creative-director`
1. Angles from the research, each backed by quote evidence. **Every concept names the VoC quote ids and the competitor gap it is built on.** A concept that can't is cut.
2. 15 concepts split across Blake's BOF/MOF formats (blake-creatives section "make 15 statics"). TOF natives only if the TOF gate in memory passes, and then at most 2–3.
3. Copy for each ad (primary text, headline, description) using Blake's copy prompts and the customers' own words from the VoC bank.
4. One image brief per ad, built from Blake's prompt templates and the matching images in `blake-creatives/examples/`.
5. **Split the job between image and copy (owner, 2026-10-28).** The image does one thing: stop the scroll and make one point, with a short hook or offer at most. Don't cram claims, benefits and bullets into it. The primary text does the selling: killer long-form copy in the customers' own words is welcome.
6. **Destination per angle:** the product page by default (Blake's testing setup), or a **pre-lander** (advertorial, listicle or comparison, from Blake's swipe library M-261021-e8c6) when the angle needs education first (problem-aware or unaware buyers). Say which ads go where and why, and brief the pre-lander for step 6.

## 4. QC gate, BEFORE any image is generated: a second agent
- A fresh `creative-director` instance, or `creative-research`, reviews every concept, brief and copy against Blake's QC checklist, the VoC bank (does it use real pain and real words?), the owner's rules (15 ads, statics, honesty defaults, offer-on-image default) and `bin/jarvis feedback list --kind ad`.
- Weak briefs go back to step 3. Only approved briefs move on, and verdicts are recorded with `bin/jarvis feedback add --source qc`.

## 5. Images: JARVIS, within the approved budget
- Generate **one image per approved brief** with Blake's tool and settings (blake-creatives). No variant batches.
- **Quality bar (owner, 2026-10-28: image ads only sell if they look good).** Reject any image with: warped or misspelt text, a product that doesn't match the real one, AI tells (extra fingers, plastic skin, melted labels, odd shadows), low resolution or artefacts, clutter, or anything a thumb would scroll past. Check it full-size and as a phone-feed thumbnail.
- Check each result against its brief. Re-generate only the failing element. Max 2 attempts, then flag it for the owner. A weak image is cut, not shipped: 12 great ads beat 15 with filler.
- `bin/jarvis cost log <service> <AUD>` for every paid call. Save to `brain/files/<product>/ads/NN-<concept>.png`.

## 6. Product page: `shopify`
- **Model it on the winning competitor (owner, 2026-10-28).** From `competitor-funnels.md`, rank the competitors' pages by how well their ads are doing: the number of active ads, impressions (Meta Ad Library ranges where shown) and how long the ads have run. Base the PDP on the top one or two: their structure, section order, offer and proof approach. Rewrite every word from our VoC bank, never copy theirs. Name the competitor and the evidence in the package. The old store's pages are reference only.
- Build the PDP as blake-shopify-cro sets out (section order, offer, social proof, settings), then run its **review checklist** (every item pass or fail) before the owner sees it.
- Draft only: a local preview or draft spec, with no changes to the live store until the launch approval (`shopify_live`). Real, attributable reviews only. Empty slots are flagged.
- **Pre-lander** when step 3 calls for one: a Shopify page in the advertorial or listicle format from Blake's swipe library, written from the VoC bank, ending in a clear button to the product page. The honesty rules apply in full: no invented doctors, experts, studies or review counts (D11).

## 7. Campaign and test plan: `meta-ads` + `product-testing`
- meta-ads: the campaign draft per blake-meta-ads (structure, one ad set with the 15 ads, budget, region, naming, tracking checks).
- product-testing: register the test (`bin/jarvis test add`) with Blake's day 1/2/3 criteria and data source.

## 8. Review package: JARVIS
One document or page, presented all at once:
1. A 30-second summary (speakable).
2. Pre-test gate verdict.
3. Research highlights (top themes with real quotes).
4. **The 15 ads**: image plus primary text, headline and description, and the angle and format of each.
5. **PDP preview** with its checklist result.
6. Campaign draft and test plan.
7. Costs so far.
8. The approvals needed to launch (`launch`, `spend`, `shopify_live`).

Save it at `brain/work/<product>/review-package.md`, or as an HTML page.

## 9. Owner review, then the feedback loop
- Record every owner comment with `bin/jarvis feedback add --source owner` (per ad and per PDP section), revise only what was flagged, and re-present only the changes.
- On approval, run `approval approve`. JARVIS launches using the owner's connectors only after that.

## 10. After launch: `product-testing` daily
- Daily read per Blake (blake-testing): data source, the day 1/2/3 calls, offer-vs-creative diagnosis, and 5–10 new ads a day while testing.
- Every result goes through `bin/jarvis feedback add --source data`. Winners and losers feed the next batch.

Close T<p> with the package path and a speakable summary.
