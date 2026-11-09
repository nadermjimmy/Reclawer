"""Measure the brain against the gold set (eval/build_gold.py). Free by default: no LLM calls.

Matching is REPLAYED: each gold suggestion goes through master.match again, with the current master, the group's
team assignment, and only the aliases that existed before the person decided (so an alias created by that very
decision can't make the replay look right). Everything else is MEASURED from what was recorded:
brain verdicts, auto-approvals a person reopened, acceptance of extracted values, logged API usage.

Metrics, overall and per source kind (message / row / pdf_page / image):
- match precision / recall (suggestion -> correct existing row), new-vs-existing accuracy
- cross-script misses listed separately (Arabic name vs Latin row, or the other way round)
- noise precision / recall of the brain's recorded verdicts (needs reject reasons to have a noise label)
- extraction: share of extracted values a person accepted, per field and per source kind
- auto-approve precision: brain approvals a person did not reopen (an upper bound: unreviewed ones count as right)
- share of suggestions that reached an LLM after extraction (the brain), API calls and cost from llm_usage

Run in the container:  python -m eval.run_eval [--gold DIR] [--out report.md]
"""
import argparse, datetime, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, master, master_schema as ms, normalize, profiles  # noqa: E402
from eval import build_gold  # noqa: E402

KINDS = build_gold.KINDS
# USD per million tokens (input, output); batch requests are half price. Cache reads ~10% of input.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}


def _ratio(a, b):
    return round(a / b, 4) if b else None


def _sheets_for(field):
    kinds = [k for k, fs in ms.PROPOSABLE.items() if field in fs]
    return [s for s, k in ms.SHEETS.items() if k in kinds]


def replay_match(c, item, mode="legacy"):
    """What the matcher (legacy or v2) says for this suggestion: (row_key or None, match_state)."""
    assigned = profiles.assigned_keys(c, item.get("chat_jid"))
    dev = item["developer"] or item.get("chat_developer") or ""
    hits = master.match(c, item["project"], dev, _sheets_for(item["field"]), assigned,
                        alias_before=item["decided_at"], chat_jid=item.get("chat_jid"), field=item["field"],
                        source_kind=item["source_kind"], mode=mode)
    if not hits:
        return None, "new"
    sheet = item["sheet"] if item["sheet"] in hits else next(iter(hits))
    row, state, _ = hits[sheet]
    return master.row_key_of(row), state


def matching_metrics(c, matching, mode="legacy"):
    per = {k: {"tp": 0, "fp": 0, "fn": 0, "nve_ok": 0, "nve_n": 0, "n": 0} for k in KINDS}
    cross, misses = [], []
    for it in matching:
        lab = it["label"]
        if lab["type"] not in ("row", "new_project"):
            continue
        k = per[it["source_kind"]]
        k["n"] += 1
        pred, state = replay_match(c, it, mode)
        gold = lab.get("row_key") if lab["type"] == "row" else None
        k["nve_n"] += 1
        k["nve_ok"] += (pred is None) == (gold is None)
        if pred and gold and pred == gold:
            k["tp"] += 1
        else:
            if pred:
                k["fp"] += 1
            if gold:
                k["fn"] += 1
            if gold:
                gold_project = gold.split("|")[1]
                rec = {"id": it["id"], "kind": it["source_kind"], "chat_says": it["project"], "expected": gold,
                       "got": pred or "(no match)"}
                misses.append(rec)
                if normalize.is_arabic(it["project"]) != normalize.is_arabic(gold_project):
                    cross.append(rec)
    out = {}
    tot = {"tp": 0, "fp": 0, "fn": 0, "nve_ok": 0, "nve_n": 0, "n": 0}
    for k, v in per.items():
        for x in tot:
            tot[x] += v[x]
        out[k] = {"n": v["n"], "precision": _ratio(v["tp"], v["tp"] + v["fp"]), "recall": _ratio(v["tp"], v["tp"] + v["fn"]),
                  "new_vs_existing_acc": _ratio(v["nve_ok"], v["nve_n"])}
    out["all"] = {"n": tot["n"], "precision": _ratio(tot["tp"], tot["tp"] + tot["fp"]),
                  "recall": _ratio(tot["tp"], tot["tp"] + tot["fn"]), "new_vs_existing_acc": _ratio(tot["nve_ok"], tot["nve_n"])}
    return out, cross, misses


def noise_metrics(c, matching):
    """The brain's recorded verdicts vs the person's label (noise = rejected as not an offer / duplicate)."""
    per = {k: {"tp": 0, "fp": 0, "fn": 0, "n": 0} for k in list(KINDS) + ["all"]}
    for it in matching:
        b = c.execute("SELECT verdict FROM brain_reviews WHERE proposal_id=?", (it["id"],)).fetchone()
        if not b or it["label"]["type"] == "rejected_unknown":
            continue
        pred, gold = b["verdict"] == "noise", it["label"]["type"] == "noise"
        for k in (it["source_kind"], "all"):
            per[k]["n"] += 1
            per[k]["tp"] += pred and gold
            per[k]["fp"] += pred and not gold
            per[k]["fn"] += gold and not pred
    return {k: {"n": v["n"], "precision": _ratio(v["tp"], v["tp"] + v["fp"]), "recall": _ratio(v["tp"], v["tp"] + v["fn"])}
            for k, v in per.items()}


def extraction_metrics(extraction):
    """Share of extracted values a person accepted (wrong-project and noise rejections excluded: not a value error)."""
    by_kind, by_field = {}, {}
    for it in extraction:
        if it["decision"] == "rejected" and it["reason"] in ("wrong_project", "not_an_offer", "duplicate"):
            continue
        ok = it["decision"] == "approved"
        for d, key in ((by_kind, it["source_kind"]), (by_field, it["field"])):
            e = d.setdefault(key, [0, 0])
            e[0] += ok
            e[1] += 1
    fmt = lambda d: {k: {"n": n, "accepted": _ratio(a, n)} for k, (a, n) in sorted(d.items())}
    return {"by_source_kind": fmt(by_kind), "by_field": fmt(by_field)}


def auto_approve_metrics(c):
    total = c.execute("SELECT COUNT(*) FROM brain_reviews WHERE action IN ('approved','reopened') "
                      "AND verdict='update_existing'").fetchone()[0]
    reopened = c.execute("SELECT COUNT(*) FROM brain_reviews WHERE action='reopened' AND verdict='update_existing'").fetchone()[0]
    return {"brain_approved": total, "reopened_by_person": reopened,
            "precision_upper_bound": _ratio(total - reopened, total)}


def noise_filter_metrics(c, matching):
    """Phase 4: of the gold MESSAGE items a person approved (real offers), how many would the noise filter skip."""
    try:
        import noise
    except Exception:
        return None
    m, ver = noise.model()
    if m is None:
        return None
    thr = noise.SKIP if noise.SKIP is not None else noise.DEFAULT_REPORT_THRESHOLD
    offers, noisy = [], []
    for it in matching:
        if it["source_kind"] != "message":
            continue
        t = c.execute("SELECT text FROM source_messages WHERE wa_id=?", (it["source_ref"],)).fetchone()
        if not t or not t[0]:
            continue
        (offers if it["label"]["type"] in ("row", "new_project") else
         noisy if it["label"]["type"] == "noise" and it["label"].get("reason") == "not_an_offer" else []).append(t[0])
    po, pn = noise.p_offer(offers), noise.p_offer(noisy)
    fs = sum(p < thr for p in po)
    return {"model": ver, "threshold": thr, "offers": len(po), "false_skips": fs,
            "false_skip_rate": _ratio(fs, len(po)), "skip_share_noise": _ratio(sum(p < thr for p in pn), len(pn))}


def llm_metrics(c, days=7):
    created = c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    reviewed = c.execute("SELECT COUNT(*) FROM brain_reviews").fetchone()[0]
    since = int(time.time()) - days * 86400
    usage = c.execute("SELECT purpose, model, batch, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, "
                      "SUM(cache_read) cr FROM llm_usage WHERE ts>=? GROUP BY 1, 2, 3", (since,)).fetchall()
    rows, total = [], 0.0
    for u in usage:
        pin, pout = PRICES.get(u["model"], PRICES["claude-sonnet-5-5"])
        cost = ((u["i"] or 0) * pin + (u["cr"] or 0) * pin * 0.1 + (u["o"] or 0) * pout) / 1e6
        cost *= 0.5 if u["batch"] else 1.0
        total += cost
        rows.append({"purpose": u["purpose"], "model": u["model"], "batch": bool(u["batch"]), "calls": u["n"],
                     "input_tokens": u["i"], "output_tokens": u["o"], "usd": round(cost, 4)})
    by_matcher = c.execute("SELECT COUNT(*) FROM brain_reviews WHERE model LIKE 'matcher-%'").fetchone()[0]
    skipped = c.execute("SELECT COUNT(*) FROM noise_skips WHERE requeued_at IS NULL").fetchone()[0]
    cache_hits = c.execute("SELECT COALESCE(SUM(hits),0) FROM extraction_cache").fetchone()[0]
    template_uses = c.execute("SELECT COALESCE(SUM(used),0) FROM sheet_templates").fetchone()[0]
    reviewed -= by_matcher
    return {"suggestions": created, "reviewed_by_brain_llm": reviewed, "placed_by_matcher": by_matcher,
            "share_reaching_brain_llm": _ratio(reviewed, created),
            # every new message/row/page/image goes to Claude except these (all off by default)
            "extraction_share_reaching_llm": 1.0 if not (skipped or cache_hits or template_uses) else None,
            "noise_skipped_messages": skipped, "extraction_cache_hits": cache_hits, "template_sheets_read": template_uses,
            "usage_last_days": days, "usage": rows, "usd_last_days": round(total, 4),
            "usd_per_day": round(total / days, 4) if usage else None}


def llm_replay(extraction, sample, max_usd, model=None):
    """PAID, opt-in: re-ask Claude to extract a sample of gold MESSAGE items and compare with the approved value.
    Estimates the cost first (chars/3.5 tokens) and refuses to run over max_usd."""
    import random, llm, proposals
    model = model or llm.MODEL
    items = [e for e in extraction if e["source_kind"] == "message" and e["decision"] == "approved"
             and (e["source"] or {}).get("text")]
    random.Random(7).shuffle(items)
    items = items[:sample]
    pin, pout = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    est = sum(((len(proposals.SYSTEM) + len(e["source"]["text"])) / 3.5 * pin + 800 * pout) / 1e6 for e in items)
    if est > max_usd:
        return {"skipped": f"estimated ${est:.2f} is over --max-usd {max_usd}", "estimated_usd": round(est, 2)}
    ok = n = 0
    for e in items:
        with db.connect() as c:
            prof = profiles.get(c, e["chat_jid"]) if e["chat_jid"] else None
        body = {"chat": e["chat_jid"] or "", "chat_profile": profiles.describe(prof),
                "messages": [{"source_id": "m", "text": e["source"]["text"], "attached_file": None}]}
        try:
            out = llm.call_json(proposals.SYSTEM, json.dumps(body, ensure_ascii=False), proposals.SCHEMA, model=model,
                                purpose="eval")["updates"]
        except Exception:
            continue
        n += 1
        want = ms.parse(e["field"], e["value"])
        ok += any(u["field"] == e["field"] and ms.same_value(e["field"], ms.round_prices(e["field"], ms.parse(e["field"], u["value"])), want)
                  for u in out)
    return {"sampled": n, "field_value_recall": _ratio(ok, n), "estimated_usd": round(est, 2), "model": model}


def run(gold_dir=None, llm_sample=0, max_usd=0.0):
    with db.connect() as c:
        if gold_dir:
            matching, extraction, meta = build_gold.load(gold_dir)
        else:
            matching, extraction = build_gold.collect(c)
            meta = build_gold.meta_of(matching)
        match, cross, misses = matching_metrics(c, matching)
        match_v2, cross_v2, _ = matching_metrics(c, matching, "v2")
        replay = llm_replay(extraction, llm_sample, max_usd) if llm_sample else None
        import matcher
        shadow = c.execute("SELECT COUNT(*) FROM matcher_shadow").fetchone()[0]
        return {"generated_at": int(time.time()), "gold": meta, "matching": match, "llm_replay": replay,
                "matching_v2": match_v2, "cross_script_misses_v2": cross_v2, "matcher_model": matcher.model()[1],
                "shadow_disagreements": shadow, "noise_filter": noise_filter_metrics(c, matching),
                "cross_script_misses": cross, "misses": misses[:200],
                "noise": noise_metrics(c, matching), "extraction": extraction_metrics(extraction),
                "auto_approve": auto_approve_metrics(c), "llm": llm_metrics(c)}


def pct(v):
    return "–" if v is None else f"{v * 100:.1f}%"


def report_md(r, title="Eval report"):
    g = r["gold"]
    lines = [f"# {title}", "",
             f"Generated {datetime.datetime.fromtimestamp(r['generated_at'], datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. "
             f"Gold: **{g['total']}** person decisions.", "",
             "| Source kind | Decisions | Match precision | Match recall | New vs existing | Noise P | Noise R | Values accepted |",
             "|---|---|---|---|---|---|---|---|"]
    for k in list(KINDS) + ["all"]:
        m, n = r["matching"].get(k, {}), r["noise"].get(k, {})
        e = r["extraction"]["by_source_kind"].get(k, {}) if k != "all" else {}
        cnt = g["by_source_kind"].get(k, g["total"] if k == "all" else 0)
        lines.append(f"| {k} | {cnt} | {pct(m.get('precision'))} | {pct(m.get('recall'))} | "
                     f"{pct(m.get('new_vs_existing_acc'))} | {pct(n.get('precision'))} | {pct(n.get('recall'))} | "
                     f"{pct(e.get('accepted'))} |")
    if r.get("matching_v2"):
        lines += ["", f"### Unified matcher (v2, model: {r.get('matcher_model')}) on the same gold", "",
                  "| Source kind | Match precision | Match recall | New vs existing |", "|---|---|---|---|"]
        for k in list(KINDS) + ["all"]:
            m = r["matching_v2"].get(k, {})
            lines.append(f"| {k} | {pct(m.get('precision'))} | {pct(m.get('recall'))} | {pct(m.get('new_vs_existing_acc'))} |")
        lines += ["", f"Cross-script misses: legacy {len(r['cross_script_misses'])}, v2 {len(r['cross_script_misses_v2'])}. "
                  f"Shadow disagreements logged: {r.get('shadow_disagreements', 0)}."]
    nf = r.get("noise_filter")
    if nf:
        lines += ["", "### Noise filter (text messages)", "",
                  f"Model {nf['model']}; threshold {nf['threshold']}; false-skip rate on gold offers "
                  f"{pct(nf['false_skip_rate'])} ({nf['false_skips']} of {nf['offers']}); would skip "
                  f"{pct(nf['skip_share_noise'])} of gold noise. In-sample when the model was trained on these labels."]
    lines += ["", "Labels: " + ", ".join(f"{k} {v}" for k, v in sorted(g["by_label"].items())) or "none", "",
              "## Values accepted per field", "", "| Field | Decisions | Accepted |", "|---|---|---|"]
    lines += [f"| {f} | {v['n']} | {pct(v['accepted'])} |" for f, v in r["extraction"]["by_field"].items()]
    a, l = r["auto_approve"], r["llm"]
    lines += ["", "## Brain auto-approve", "",
              f"{a['brain_approved']} auto-approved, {a['reopened_by_person']} reopened by a person: precision at most "
              f"{pct(a['precision_upper_bound'])} (unreviewed approvals count as right).", "",
              "## LLM use and cost", "",
              (f"Extraction: {pct(l['extraction_share_reaching_llm'])} of new sources go to Claude. "
               if l["extraction_share_reaching_llm"] is not None else
               f"Extraction without Claude: {l['noise_skipped_messages']} message(s) skipped as noise, "
               f"{l['extraction_cache_hits']} cache hit(s), {l['template_sheets_read']} sheet(s) read from a template. ") +
              f"Brain: {pct(l['share_reaching_brain_llm'])} of suggestions ({l['reviewed_by_brain_llm']} of {l['suggestions']}).", "",
              f"Measured API cost, last {l['usage_last_days']} days: ${l['usd_last_days']}"
              + (f" (≈ ${l['usd_per_day']}/day)." if l["usd_per_day"] is not None else
                 " (no usage logged yet: logging started with this version)."), ""]
    if l["usage"]:
        lines += ["| Purpose | Model | Batch | Calls | Input tok | Output tok | USD |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {u['purpose']} | {u['model']} | {'yes' if u['batch'] else 'no'} | {u['calls']} | "
                  f"{u['input_tokens']} | {u['output_tokens']} | {u['usd']} |" for u in l["usage"]]
    if r.get("llm_replay"):
        lines += ["## LLM replay (paid sample)", "", "```", json.dumps(r["llm_replay"], indent=1), "```", ""]
    lines += ["", f"## Cross-script misses ({len(r['cross_script_misses'])})", "",
              "Arabic name in the chat vs a Latin row on the sheet (or the other way round) that today's matcher missed.", ""]
    lines += [f"- `{m['chat_says']}` → expected `{m['expected']}`, got `{m['got']}` ({m['kind']})"
              for m in r["cross_script_misses"][:50]] or ["- none"]
    return "\n".join(lines) + "\n"


def run_job(log):
    """Pipeline step: build the gold set and write the report (no LLM calls)."""
    path, meta = build_gold.build()
    log(f"Gold set: {meta['total']} person decisions — " + ", ".join(f"{k} {v}" for k, v in meta["by_source_kind"].items()))
    r = run(path)
    md = report_md(r, "Brain eval")
    out = os.path.join(path, "report.md")
    with open(out, "w") as f:
        f.write(md)
    with open(os.path.join(path, "report.json"), "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    for line in md.splitlines():
        log(line)
    log(f"Saved {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gold")
    ap.add_argument("--out")
    ap.add_argument("--llm", type=int, default=0, help="PAID: re-extract this many gold messages with Claude")
    ap.add_argument("--max-usd", type=float, default=1.0, help="refuse the --llm replay above this estimate")
    a = ap.parse_args()
    db.init()
    rep = run(a.gold, a.llm, a.max_usd)
    md = report_md(rep)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    print(md)
