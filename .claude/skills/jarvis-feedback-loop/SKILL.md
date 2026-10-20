---
name: jarvis-feedback-loop
description: The team's feedback loop. Every verdict on JARVIS's work (the owner's review of an ad, copy or product page, test results, a QC check against Blake's rules) is recorded as a lesson: what was made, worked or not, why, what to keep and what to change. Agents check past feedback before similar work. Use whenever feedback arrives on anything the team made, and at the start of any creative, PDP, research or test task.
---

# Feedback loop: get better every round

The owner's rule: whenever a task is completed and feedback comes in, good or bad, record **what worked and what didn't**, so the team improves every time. Nothing learned should live only in chat.

## 1. Before you start a task: read the history
```bash
bin/jarvis feedback list --agent <you>            # your past verdicts, newest first
bin/jarvis feedback list --kind ad --verdict bad  # e.g. which ads flopped
bin/jarvis feedback digest --agent <you>          # patterns: keep doing / change next time
```
Apply what you find. If the history contradicts your plan, follow the history and say so. Blake's method still sets the rules; feedback tells you how they worked out for *this* store.

## 2. When feedback arrives: record it straight away
Feedback comes from:
- **owner:** the owner's review ("ad 4 is weak, headline's too long"), approvals and rejections, voice comments;
- **data:** test results, CTR/ATC/checkout/sales by ad, PDP conversion, split tests (always with the numbers);
- **qc:** your own or another agent's check against Blake's checklist before anything is shown to the owner;
- **blake:** a Blake rule turned out to be the deciding factor.

```bash
bin/jarvis feedback add '<what it is about>' --kind <ad|copy|pdp|test|campaign|research|product|process> \
  --verdict <good|bad|mixed> --source <owner|data|qc|blake> \
  --what '<what was made: angle, format, hook, visual, offer, layout>' \
  --why '<why it worked or did not — your best honest read>' \
  --keep '<what to repeat>' --change '<what to do differently>' \
  --evidence '<numbers or the owner’s words>' -p <product> --agent <agent> --task T<n>
```
Rules:
- **One record per distinct lesson.** For 15 ads, record the winners and losers individually, plus one summary record for the batch.
- **Be specific.** "Problem-first headline in ≤6 words beat the benefit headline: 3.1% vs 1.2% CTR" teaches something. "Ad was good" doesn't.
- **Data beats opinion.** Mark small samples as such. A test below Blake's minimums is "insufficient data", never a win or a loss.
- **Never invent feedback.** If the owner gave a verdict but no reason, record the verdict, and put your hypothesis under `--why` labelled "hypothesis".

## 3. Close the loop: make it change behaviour
- After every **3 or more records on the same pattern**, write a durable rule. Use `bin/jarvis mem add -c <category> --provenance evidence`, or `--provenance user` if it's the owner's call, tagged `feedback-rule` and `for:<agent>`. Then tell mentor-learning so the relevant `blake-*` playbook gains a "What has worked for Urban Supplied" note. That note is kept separate from Blake's cited methods.
- If feedback shows a skill or playbook step is wrong for this store, flag it in your deliverable. Don't silently ignore it.
- If the cause was a mistake in your work, also save it as a mistake (`-c procedures`, tag `mistake`).

## 4. Report it
End the task with a line JARVIS can say out loud, e.g. "Saved the feedback: the problem-first headlines won and the busy layouts lost. Next batch will lean problem-first."
