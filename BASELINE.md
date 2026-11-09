# Baseline: the brain before any learning (Phase 1)

**Status: not measured yet.** The baseline has to come from production data, because the gold set is the decisions
your team made on Railway. This repo's sandbox has no access to that database. Every other phase compares against the
numbers this file will hold.

## How to produce it (no Claude calls, free)

1. Deploy this branch. On startup, existing approvals and rejections get a stable row key
   (`master.stamp_row_keys` + `relink`). This is idempotent and changes no values.
2. Run **More > Pipeline > "Eval: brain report (no LLM)"**, or in the container:
   ```
   railway ssh --service receiver python -m eval.build_gold
   railway ssh --service receiver python -m eval.run_eval --gold /data/eval/gold-<date> --out /data/eval/BASELINE.md
   ```
3. Paste the report here: the table per source kind, values accepted per field, brain auto-approve, LLM share and
   cost, and the cross-script misses.

An optional paid check (`--llm 30 --max-usd 1`) re-extracts 30 gold messages with Claude. It refuses to run above the
cost estimate.

## What it will show, and its limits

| Metric | How it's measured today |
|---|---|
| Match precision / recall, new vs existing | **Replayed**: each person decision goes through `master.match` again. Aliases and approval-created rows from after that decision are hidden. |
| Cross-script misses | Replayed misses where the chat's name and the sheet row are in different scripts |
| Noise precision / recall | The brain's **recorded** verdicts vs the person's label. A noise label only exists when a rejection has a reason (new in this phase). |
| Values accepted (per field, per source kind) | Share of extracted values a person approved. Rejections for wrong project or noise don't count as value errors. |
| Auto-approve precision | Brain approvals that no person reopened. **An upper bound**: unreviewed approvals count as right. |
| LLM share and cost | Measured from `llm_usage`, which starts logging with this version, so the first days show no history |

## Expected size, and how to grow it

The system went live this week, so expect **well under 200 person decisions**, most of them on `message`. Until there
are a few hundred per source kind, every percentage has a wide margin. Ways to grow the labels:

- **Reject reasons.** After every reject on Updates, a toast asks "Why?" (Wrong project / Wrong value / Not an offer /
  Duplicate). One tap. Without it, rejections can't be used as noise labels.
- **Group assignments (Phase 0).** Each group the team assigns to a developer and projects is a label for matching.
- **Reopen.** A reopened brain decision becomes a person's decision.
- **Review the brain's backlog.** Approve or reject what it left open on Updates. Every decision is a label.
- **Spot checks.** About 20 brain auto-approvals a week, opened from the Approved tab and either left alone or
  reopened, make the auto-approve precision real instead of an upper bound.
