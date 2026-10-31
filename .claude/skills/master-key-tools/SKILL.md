---
name: master-key-tools
description: How JARVIS and its agents use the owner's Master Key spy and research tools (GetHookd, Kalodata, PiPiAds, ShopHunter, AdSpy, WinningHunter, Canva, CapCut…) inside the DICloak browser - which tool for which job in a product test, the exact steps to open and drive them by computer use, what works and what doesn't, and the rules (one agent on the screen at a time, research only, never type passwords). Load before any product research, competitor/funnel-hack, ad-library or creative-inspiration work, and whenever the owner says "use the tools" or "use Master Key".
---

# Master Key tools (DICloak)

The owner pays for **Master Key** (about US$30 a month, Blake's recommended way to get the spy tools: memory M-261021-dc6a). It runs in **DICloak**, which opens the shared Master Key profile in its own Chromium build, **ginsbrowser.exe**, at `masterkeyvip.com/vip-dashboard`. The owner's standing rule (2026-10-29, M-261029-06e4): **during every product test, use the best Master Key tool for each job.** Blake's exact settings for each tool are in his private playbooks: load `blake-tools` and `blake-product-research` (and `blake-creatives` for GetHookd) and follow them.

## Which tool for which job

| Job in the pipeline | Use | Status (tested 2026-10-29) | Notes |
|---|---|---|---|
| Find products / prove demand on Shopify | **ShopHunter** (Explore → Products) | works | Filters on the left: Product Features, Ads, Product Revenue, Shop Revenue, Shop Features, Categories. Sort "Shop Ads % Change". Product cards show ad count and day/week revenue. Blake: Explore → Products, Search Relevance. |
| TikTok Shop sales, trends, creators | **Kalodata** | works | Top menu: Explore, Category, Brand & Shop, Creator, Product, Video & Ad, Livestream. **Set the country to the US** first (it opened on Indonesia). Has "Last 7/30/90/180/365 days" ranges. |
| Ad volume signal, ad spy (FB + TikTok) | **PiPiAds** | works | Menu: Ad Search, AI Tools, E-commerce, TikTok Shop, Apps. Features: Adspy, Ad Library, Advertiser, Ad Tracker, **Find Similar by Image**. Blake's winner signal: many ads launched in a few days (impressions are unreliable, M-261020-69db). |
| Winning ads, angles, advertorials, landing pages (funnel hack) | **GetHookd** | works | Home = Brand Spy (tracked brands, new ads, format mix). Discover, Create, Academy. Search ads, brands, shops, boards, scripts. Blake: Winning + Optimized, 4 ads per brand, longest running; tabs include Advertorials, Quiz Funnels, VSLs. |
| Facebook/Instagram ads with targeting filters | **AdSpy** | works | Search by Ad Text, Advertiser, Countries, Language, Created/Seen between, Gender, Ages, Likes, Media type, Technologies (e.g. Shopify). |
| More product ideas | **WinningHunter** | not tested yet | The dashboard went blank on 2026-10-29 before it could be tested. |
| Text overlays on ad images (Blake) | **Canva Pro** | owner must join once | Opens a Canva Teams invite ("discord.gg/spycom"). The owner signs in with their own free Canva account once; JARVIS never signs in or creates accounts. |
| Video editing (later, video stage) | **CapCut Pro** | not working | Blank sign-in page even after a reset (2026-10-29). Retry before relying on it. |
| Voiceover (later) | **ElevenLabs** | not in the owner's plan | Clicking it opens Master Key's sales page on Whop. |
| Trends and spotting winners | **TrendTrack** | under maintenance (still on 2026-10-30) | The provider warned it was banning accounts. Don't use until Master Key says it's back. |
| **Ad images** | **not Master Key** | | The owner says the Master Key image generators are poor (M-261029-ed74). Use our own **Higgsfield** connector (Nano Banana Pro, Blake's settings) for every ad image. |

Also on the dashboard, not part of our method: ChatGPT Plus, Claude MAX, Gemini Ultra, Perplexity, RunwayML, HeyGen, Flow (Veo 3), Magnific, Envato. Don't use them for ad images. Market research quotes must come from real customers, not AI rewording (Blake, M-261021-88e6).

Free tools Blake also uses alongside: **Google Trends**, the **Meta Ad Library** (public, headless procedure M-261021-ece5), Google Lens, ssstik.io, extract.pics.

## Desktop jobs: how screen work gets done

Computer use needs a grant the owner gives at the PC, and only a live desktop session can hold it. Scheduled runs and the phone session can't ask for it (M-261030-a942). So **one long-running desktop session, the job runner, holds the grant**, and everything else sends it jobs (D21):

- **From the phone or any other session:** `bin/jarvis job add '<what to do>'`. The phone also has a 🔎 Product hunt button and /jobs. Never tell the owner they must come to the PC for screen work if the runner is on (`bin/jarvis job list` shows it).
- **The runner's loop:**
  1. Once, at the start: computer use `request_access` for `["DICloak", "ginsbrowser.exe"]` (the owner clicks Allow).
  2. Run `bin/jarvis job wait` **in the background** (Bash `run_in_background`). It uses no tokens while it waits, and you're woken when it exits. "No job yet" means run it again.
  3. Do the job from its description. Take the screen with `bin/jarvis dicloak lock runner`, unlock after.
  4. `bin/jarvis job done J<n> --summary '<3-6 short spoken-style sentences>'` sends the result to the owner's phone. `job fail` if it couldn't be done, saying why.
  5. Go back to step 2. Never stop looping unless the owner says so.
- **It needs:** the PC on, unlocked, no sleep, the Claude desktop app open, and the DICloak Master Key profile open. If the runner session restarts, the owner must click Allow once again.
- **Other computer-use jobs** go the same way. The runner asks for any other app it needs; if the owner isn't there to allow it, it fails the job and says what's needed.

### Product hunt job
1. Load `blake-product-research`. Read the rules: `bin/jarvis mem search "rule" -c product-research`. Read past candidates so you don't repeat them: `bin/jarvis mem search "Product candidate" -c product-research`.
2. Owner rules: **every product needs 2-3 clear pain points, found in real customer words** (M-261031-a9d9; the fleece mask was dropped for having none, D17). No tech (M-261029-a481). Clothing only if it wins the full checklist against everything else (M-261030-6368). Testimonial-style ads should suit it (M-261031-edab).
3. Find with Blake's exact settings: PiPiAds and Kalodata (see the tips below; try both the new launches and the slow-burn pages), TrendTrack if it's back. Skip brands and anything we can't source.
4. Judge the best two or three: ShopHunter competition, ad count against days, Google Trends, USP "new or weird", TAM, cost, bundles, unisex, and the pain points (quick check of Amazon/Reddit reviews for real complaints).
5. Save evidence in `brain/files/research/<date>/`, a report at `bin/jarvis task path T<n>`, and a `Product candidate:` memory entry for each one judged. Be honest: "nothing strong today" is a valid result.

## How to drive it (computer use)

Computer use works in the desktop app: the main JARVIS session, desktop scheduled tasks, and the research agents it starts there (product-research, market-research, creative-research have the tools). The phone/dashboard (headless) session doesn't, so queue that work for the desktop. Any agent without it asks JARVIS for the searches it needs.

1. **Take the screen.** One screen, one mouse, so one agent at a time: `bin/jarvis dicloak lock <your-name>`. If someone else has it, do other work and retry. **Always** `bin/jarvis dicloak unlock <your-name>` when done, even after an error.
2. **Check it's open:** `bin/jarvis dicloak status`. If the profile isn't open, ask the owner to open DICloak and start the Master Key profile. Don't open or change DICloak profiles yourself.
3. **Bring it up:** `bin/jarvis dicloak show`, then computer use `request_access` for `["DICloak", "ginsbrowser.exe"]` (once per session) and `open_application "Ginsbrowser"`. If a screenshot shows a black screen or the desktop, run `show` again and `open_application` again. **Maximise** it by double-clicking its title bar.
4. **Before each key press, click inside the page first.** Keystrokes fail with "Claude's own window still has keyboard focus" otherwise.
5. **Open a tool:** on the dashboard, click its card, or click "Search tools…" and type its name. It opens in a new tab.
6. **If the tool shows a login or sign-up page** (normal for shared accounts): click the **Master Key extension** (purple icon, right of the address bar, about x=1316, y=40 on a 1456-wide screen) → **Reset current site**, then wait 15 to 25 seconds. That logged in GetHookd, Kalodata, PiPiAds, ShopHunter and AdSpy. If it still asks for a password, stop: **never type a password or pick "log in with Google"**. Report the tool as not working.
7. **Dismiss** any "Install app?" prompt with **Cancel**. Never install, buy, upgrade or sign out. The extension's "Sign out / change key" and "Reset all sites" are off limits.
8. **Work at a human pace**: a few searches, not hundreds. These are shared accounts, and heavy use gets them banned.
9. **Capture evidence:** take screenshots with `save_to_disk` and copy them into `brain/files/<product>/research/` (or `brain/files/research/<date>/` for a product hunt). Write down the tool, the date, the filters used and the numbers seen, so every claim can be checked.
10. **Close the tool tab** (click the page, then Ctrl+W) to go back to the dashboard. When finished, unlock the screen.

If the dashboard page goes blank, click inside it and press F5, then wait 20 seconds. If it stays blank, use the extension's **Reset current site** on the dashboard tab.

## Tool-by-tool tips (learned 2026-10-29/30)
- **PiPiAds**
  - Blake's product search goes straight to `pipiads.com/product-search?sort=5&sort_type=desc&video_count_min=10&play_count_max=500000`.
  - Then set Start Date → "15 days" → Apply.
  - Keyword searches use `&keyword=<words>`.
  - The list view (icon right of "Edit searches") shows ads, impressions and dates per product.
  - The TikTok/Facebook buttons filter by platform: use **Facebook** to judge Meta competition.
  - Keyword matching is loose, so read the results before trusting the count.
- **Kalodata**
  - Use `kalodata.com/product` and check the tab title says United States.
  - Filters in the left panel: Launch Date (with a "Clear" link), Revenue Source (Content) → Video → Apply, Revenue Growth Rate → >0%, then **Submit**.
  - Set 50 per page at the bottom, and use "Go to" for Blake's slow-burn pages.
  - Keep the mouse off the video thumbnails, because they open a preview over the table.
- **ShopHunter**
  - Scroll to the top first. Then click the search box, Ctrl+A, type, and click the purple **Search** button. Enter alone doesn't always run it.
  - The list view (icon right of Search) shows weekly revenue and Meta ads per product.
  - It matches words loosely, so judge relevance by eye.
- **Google Trends** (JARVIS's own browser): load `trends.google.com/trends/` first, then `.../explore?date=all&q=<term>`. Going straight to explore can return a 429 error.
- **General**
  - Never move the mouse to the screen edge: it opens a Windows panel that blocks control. Run `bin/jarvis dicloak show` to recover.
  - Misclicks often open a product in a new tab: close it with Ctrl+W.
  - Most top results in Kalodata are big brands, which can't be sourced. Skip them.

## Rules
- Research and reading only. No purchases, upgrades, account changes, messages or posts from these accounts.
- Never put the owner's own logins (Claude, Google, Shopify, Meta) into this shared profile: the provider controls it.
- Don't try to attach a debugger to the profile, read its cookies or get around its locks.
- Label anything taken from these tools as evidence, with the tool, the date and the filters. Never present a guess as a tool figure.
- Update the status table above when a tool starts or stops working (commit it), and save anything new about how a tool works to memory (`-c procedures`, tag `master-key`).
