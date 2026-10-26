---
name: store-bug-fixer
description: The owner's bug-fixing routine for the Urban Supplied store - when a bug is found, fix it, test the fix before it goes live, check the rest of the site for anything else broken, and if customers were affected draft an email and ask the owner before it goes out. Use whenever something on the store is reported broken, after any store or theme change, before a product launch, and in the regular store sweep. store-qa does the work; JARVIS applies fixes only after approval.
---

# Store bug fixer

The owner asked for this routine (2026-10-26, from a reel showing a JARVIS that hands bugs to a developer agent). Five steps, in order, every time.

## 1. Reproduce it like a customer
- Open the store in a new tab and follow the path a shopper takes to the problem. Do it at desktop width and at phone width (390x844), because most Meta traffic is on phones.
- Write the exact steps, what should happen and what actually happens. Capture console errors and failed network requests.
- If you can't reproduce it, say so. Don't mark it fixed.

## 2. Find the cause and write the fix
- Use the Shopify read tools (product, collection, GraphQL) to see the data behind the page: variants, prices, metafields, inventory, the active theme.
- Write the smallest fix that solves it: the exact field, setting, app setting or theme file and the change. Give the before and after.
- Never apply it yourself. JARVIS asks the owner (`bin/jarvis approval request shopify_live ...`) and applies it after a yes.

## 3. Test before it goes live
- Say how the fix will be tested: on a theme preview or duplicate theme for code changes, or by re-running step 1 straight after JARVIS applies a data change.
- After it's applied, re-run the reproduction steps on both widths. Only then is it "fixed and tested".

## 4. Check the rest of the site
A bug rarely travels alone. Run the full customer path from the store-qa agent (home → collection → product → offer → cart → checkout to payment, policies, tracking) and log anything else you find the same way.

## 5. Customers and the report
- If real customers were affected (orders placed during the bug, wrong price charged, a delivery promise broken), check the orders read-only and draft one short, honest email per situation. Mark it "needs owner approval". Nothing is sent without a yes.
- Hand JARVIS the action plan: verdict, bug table, fixes awaiting approval, customer emails awaiting approval.
- JARVIS reports to the owner in one spoken line, e.g. "Three bugs found, one was blocking checkout on phones. Fixes are ready; shall I apply them?" Replies and results go into the next morning brief.

## Record it
Each bug: `bin/jarvis feedback add '<bug>' --kind store --verdict bad --source qc --agent store-qa --what '<where>' --why '<cause>' --change '<fix>'`. After a fix is confirmed, add a `--verdict good` record saying what fixed it, so the same bug is caught faster next time.
