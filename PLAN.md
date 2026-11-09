# PLAN: a brain that learns from every human decision (Egypt only)

Status: draft for approval. No code has changed. Scope: MARKET=eg. UAE code paths are left untouched. No embedding APIs or embedding models: text similarity is char n-gram TF-IDF (scikit-learn) only.

---

## 1. Data flow today, per source

Every source ends in the same call, `master.add_proposal(c, item)`, which does the following in order:
1. Parse the value (`master_schema.parse` → `cell_parse`).
2. Round prices with the team rule (`ms.round_prices`).
3. Find the target row with `master.match`.
4. Apply the reading-order and freshness gate (`_filled_by_better`: message > row > pdf_page > image, 14 days).
5. Skip it if the cell already holds the value (`ms.same_value`).
6. Insert the `proposals` row and supersede older open proposals for the same cell (`_supersede`).

After that, `brain.review` gives a second look (§2) and auto-decides through `master.decide_detail`.

| # | Source | Ingest → stored as | Unit sent to the LLM | Model (env) | Live / batch |
|---|---|---|---|---|---|
| 1 | Text messages (all groups + ticked private chats) | Evolution webhook / `history.pull_history` → `source_messages` | `proposals.text_units`: one chat at a time, 60 messages per request, with chat name and `profiles.describe` | Sonnet 5.5 (`CLAUDE_MODEL`), effort medium | Sync now and Sync all (fast) run live with 6 workers. Step 6b uses the Message Batches API |
| 2 | Spreadsheets (xlsx/CSV) | `history.download_media` → `parse_files.parse_workbook` / `parse_csv` → `source_rows(kind='sheet_row')`, with column roles in `sheet_layouts` (`detect_layout`, `role_of`) | `proposals.sheet_units`: 150 rows per request, cells joined with `|`, plus chat context and profile | Sonnet 5.5 (`CLAUDE_MODEL`) | Same as text |
| 3 | PDFs (including scans) | `parse_pdf`: pypdf text per page → `source_rows(kind='pdf_page')`, used only as citation references | `proposals.pdf_units`: the PDF bytes as a base64 document block, split into chunks of ≤20 pages and ≤24 MB. Claude returns `p<page>` | Opus 5.5 (`CLAUDE_FILE_MODEL`) | Same as text |
| 4 | Images | `parse_image` → `source_rows(kind='image')` | `proposals.image_units`: one image per request (≤5 MB; jpeg/png/webp/gif), plus chat context | Opus 5.5 (`CLAUDE_FILE_MODEL`) | Same as text |
| 5 | Master workbook | `master.import_workbook` → `master_rows`, `master_cells`, `master_sheets`, `master_issues` | none | – | – |
| 6 | owner_rules_eg.json | `rules.py` (not-a-project names, locations), `master_schema.LOCATION_CODES/LOCATION_SHEETS` | none | – | – |

### Every LLM call today

| Where | Purpose | Model | Volume driver |
|---|---|---|---|
| `proposals.run_stage_live` / `_advance` (batch) | Extract cell values from sources 1–4 | Sonnet (text, rows), Opus (PDF, image) | Every new message, row, page and image |
| `brain.review` → `ask()` | Judge every new open suggestion: update existing / new / noise / unsure | **Opus 5.5** (`CLAUDE_BRAIN_MODEL`), 10 project groups per request, 6 workers | Every open suggestion, after every sync |
| `mapping.propose` | Inventory path: map a table to a project | `CLAUDE_MODEL` | Every new table context |
| `facts.extract_*` | Inventory path: facts | `CLAUDE_MODEL` | New messages and PDF pages |
| `media_review.classify` | Inventory path: what an image shows | `CLAUDE_MODEL` | New images |
| `digest.py` | Group digest | `CLAUDE_MODEL` | Manual |

All of these go through `llm.call_json`, which streams with structured outputs and the server-side refusal fallback. Batches go through `llm.batch_submit`, `llm.batch_status` and `llm.batch_results`.

## 2. The matchers that exist today (five, all different)

| Matcher | Where | How it decides | Used for |
|---|---|---|---|
| `master.match` | `master.py` | Steps, in order: exact project_key → `master_aliases` → the chat developer's own rows (difflib ≥0.6, or prefix/containment) → global exact/alias (only "near" when the developer differs) → global difflib ≥0.85 (or containment ≥5 chars) | **The target row of every proposal** (`add_proposal`) |
| `brain.Rows.score` | `brain.py` | max(difflib ratio, token Jaccard), containment → 0.8, same developer +0.35; top 8 + the chat developer's rows (15) + rows this chat updated before (6) | Candidate list shown to Opus |
| `build.link_facts` | `build.py` | Lowercase exact alias, else substring either way; must hit exactly one project | Inventory: fact → project |
| `mapping.propose` | `mapping.py` | The LLM chooses from `existing_projects` | Inventory: table → project |
| `profiles.match_developer` | `profiles.py` | difflib ≥0.75 or containment | Chat name → master developer |

None of these share normalization (`cell_parse.normalize_project_key` does NFKC + lowercase + alphanumerics only, with no Arabic folding; `search.normalize` does the Arabic folding but is used only for search). None of them learn from decisions, except through aliases.

## 3. Where human labels already live

| Table | What it holds | Strength | Problems for learning |
|---|---|---|---|
| `proposals` | `state` approved/rejected/superseded, `decided_by` (NULL = person, `'brain'`), `decided_at`, `row_id`, `sheet`, `field`, `project`, `project_key`, `developer`, `matched_label`, `match_state`, `new_raw`, `new_parsed_json`, `quote`, `source_kind/ref/ts` | A person's approve or reject is the core label (row and value) | **`row_id` is wiped on master re-upload.** A rejection has **no reason** (wrong row? wrong value? noise?). The **first match is not kept** once the brain retargets |
| `brain_reviews` | verdict, row_id, confidence, reason, action (`approved`/`rejected`/`kept`/`reopened`/`rejected_duplicate`) | `reopened` = a person overturned the brain (a strong negative) | `row_id` points at deleted rows after a re-upload |
| `master_aliases` | `alias_key → project_key`, label, confirmed_at (written when a person approves a near match) | Direct name knowledge | **No developer column**: "Arc" can only point at one project for every developer |
| `chat_profiles` (`source='team'`) | chat → developer, shares, on-request types, notes | Team-entered | Already keyed by jid, so it survives a re-upload |
| `label_mappings` (inventory) | table context → project, review_state approved/rejected | Reviewer decisions | Keyed by file/sheet context, not by master rows |
| `fact_assertions.validation_status`, `media_assignments.review_state` | Inventory reviews | – | Out of scope apart from project matching |

## 4. What happens on a master re-upload today

`master.import_workbook`:
- **Deleted and rebuilt:** `master_rows`, `master_cells`, `master_sheets`, `master_issues`. Every row gets a new id.
- **`UPDATE proposals SET state='superseded' WHERE state='proposed'`.**
- **`UPDATE proposals SET row_id=NULL`.** After this, every approved or rejected label loses its row. What remains is `sheet`, `project_key`, `developer` and `matched_label` (the sheet row's project name when the match was exact, near or brain).
- **Kept:** `master_aliases` (keyed by normalized name), `chat_profiles`, `brain_reviews` (now with dangling row ids).
- **Lost:** "new" rows created by approvals. That's fine once they've been exported and re-uploaded, but their labels then need re-linking.

**Fix planned in Phase 1 (schema only, no behaviour change):**
- **A stable `row_key`.** Defined as `norm(developer) | norm(project) | sheet`, using the new `normalize.py`. It is stored on `proposals` (at decision time and by backfill), `brain_reviews`, `master_aliases` and the new learned tables.
- **Re-linking on import.** After an import, `relink(c)` maps each `row_key` to the new `master_rows.id`. If that fails it falls back to `(sheet, norm(matched_label))`, then to `(norm(project))` restricted to the same developer, and records what it couldn't re-link.
- **Developer-aware aliases.** `master_aliases` gains `developer_key`. Existing rows backfill to `''`, meaning any developer, which keeps today's behaviour.

## 5. Phase plan

The same applies to every phase:
- **Gates:** tests pass, eval metrics are printed against the baseline, then commit and STOP.
- **Flags:** every new auto-decision sits behind an env flag that defaults to OFF.
- **Unchanged:** the reading order and 14-day rule, owner_rules as the override layer, and the never-auto fields.
- **Sales data:** no sales name, contact, phone, voice note or ID is read or proposed.

### Phase 0: Group → developer / projects assignment (ops-team labels)

**Why:** each ingestion currently guesses again which developer and project a group is about. The team knows. Their assignment should be stored once, used by every source, and kept as learning data.

**What exists already:** `chat_profiles` (`profiles.py`) holds one developer per chat, filled from "X & Byit App" and editable through the chat-header chip as free text. Extraction prompts, `master.match` (the developer's rows first) and the brain already read it.

**Files**
- **`receiver/db.py`:**
  - New table `chat_projects(jid, developer_key, project_key, project_label, sheet, assigned_by, assigned_at, PRIMARY KEY(jid, project_key))`. It is keyed by normalized developer and project, not `master_rows.id`, so it survives a master re-upload.
  - New columns `chat_profiles.assigned_by` and `chat_profiles.assigned_at`.
  - New table `assignment_log(jid, field, old, new, by, at)`, an append-only history of every team change. This is the learning label.
- **`receiver/profiles.py`:**
  - `assign(jid, developer, project_keys, by)` checks the developer against the master's developers. Only a master developer is accepted, with no free text, unless the team picks "Not on the sheet yet".
  - `assigned_projects(jid)`
  - `relink_projects(c)` runs on master import and maps `project_key` (+ developer) to the new rows, listing any it can't place.
  - Auto-fill from the chat name stays, marked `source='auto'` and shown as "Suggested". It never counts as a team label.
- **`receiver/master.py` (`match`):** tries the group's assigned projects first, then the developer's other rows, then the existing order. A hit outside the assignment is marked `match_state='near'` with the warning "Outside this group's assigned projects". The rest of the order is unchanged.
- **`receiver/proposals.py`:** the chat profile line in extraction prompts also names the assigned projects (user turn, so system prompts stay cacheable). `_store` uses the assigned developer when the message doesn't name one. This is the same behaviour as today, with better data.
- **`receiver/brain.py`:**
  - Candidates start with the assigned projects.
  - **`safe_to_approve` refuses anything outside the group's assignment.** This makes auto-approve stricter, never looser.
  - The profile line passed to the model names the assigned projects.
- **`receiver/api.py`:**
  - `GET/PUT /api/chats/{jid}/assignment` returns the developer dropdown options (master developers) and that developer's projects grouped by sheet.
  - `GET /api/chats?filter=unassigned`
  - `POST /api/chats/assign` for bulk developer assignment.
- **Frontend:**
  - **Chat header:** a developer **dropdown** (searchable list of master developers) and a project multi-select (the chosen developer's projects, by sheet). It replaces the small chip, which is kept as the read-only display in the chat list.
  - **Chat list:** an "Unassigned" filter chip, and bulk assign of a developer to selected groups.
  - **Sheet view:** the formula bar shows which groups feed the row.
- **Phase 1 eval:** the assignment is used as a label source for matching (group → project).
- **Phase 2 matcher:** adds the features "candidate is in this group's assigned projects" and "assigned developer match".
- **Tests:** `test_assignment.py` covers:
  - only master developers accepted
  - projects survive a master re-upload through `relink_projects`
  - assigned projects matched first
  - an outside-assignment match becomes near and is never auto-approved
  - bulk assign
  - the assignment log written
  - auto-fill not counted as a team label

**Behaviour change:** matching prefers what the team set, and the brain auto-approves less, never more. Matching only changes for groups the team has assigned. Groups with no assignment behave exactly as today.

### Phase 1: Eval harness (no behaviour change)

**Files**
- `receiver/normalize.py`: Arabic/Franco normalization, needed for stable keys. It is not used by matching yet. Covers:
  - alef forms → ا, ى → ي, ة → ه
  - diacritics and tatweel removed
  - Arabic-Indic and Persian digits → ASCII
  - lowercase and collapsed punctuation
  - common Franco forms (2→ء/ا, 3→ع, 7→ح, 5→خ; used only for keys, never shown)
  - stop words for developer prefixes and "compound / phase / residence / tower / el / al / مشروع / كمبوند / فيز", with a `strip_generic()` variant
  - `translit_key(text)`: a deterministic Arabic → Latin key, so 'ماونتن فيو' and 'Mountain View' can be compared:
    - a fixed letter map (e.g. ث→th, خ→kh, ش→sh, ج→g as Egyptians write it, ق→k/q merged)
    - then a **consonant skeleton** on both sides (vowels, w/y and doubled letters dropped)
    - letters Arabic lacks are folded: p→b, v→f, x→ks, c→k/s
    - so 'ماونتن فيو' and 'Mountain View' both become `mntn f`, and 'بالم هيلز' and 'Palm Hills' both become `blm hlz`
  - `row_key(developer, project, sheet)`
- `receiver/db.py`: additive columns only:
  - `proposals.row_key`, `proposals.first_row_key`, `proposals.first_match_state` (written by `add_proposal`; before this, the first match is lost)
  - `brain_reviews.row_key`
  - `master_aliases.developer_key`
  - `proposals.reject_reason`, nullable (see "Growing labels")
- `receiver/master.py`:
  - `add_proposal` writes `row_key` and the first-match columns.
  - `decide_detail` stamps `row_key`.
  - New `relink(c)`, called at the end of `import_workbook`, plus a one-off backfill.
  - No change to matching or decisions.
- `receiver/eval/build_gold.py`: exports person-decided proposals (`decided_by IS NULL`, approved or rejected) and person-reopened brain decisions into a frozen JSONL set under `/data/eval/gold-<date>/`. It keeps:
  - **Matching items:** query (project as written, developer, chat developer profile, source kind, field, quote) → label `row_key` / `new_project` / `noise_or_wrong` (person reject, unless a `reject_reason` says otherwise).
  - **Extraction items:** source ref (message text / sheet row cells / PDF page ref / image ref) → approved `(project row_key, field, new_raw)`.
  - A split by `source_kind` and a **time split** (train before T, test after T). Aliases confirmed after T are hidden at replay so the eval can't see answers from the test period.
- `receiver/eval/run_eval.py`:
  - **Deterministic replay, free:** matching through `master.match` and the current candidate retrieval.
  - **Optional LLM replay, paid:** behind `--llm --sample N --max-usd X`. It covers brain verdicts on matching items and extraction on a sample per source kind, and prints the cost estimate before running.
  - **Metrics:**
    - match precision and recall, and top-k recall of candidate retrieval
    - **cross-script misses** listed separately: Arabic name vs Latin row, or the other way round
    - new-vs-existing accuracy
    - noise precision and recall
    - extraction field accuracy per field and per source kind (exact after normalization and rounding)
    - auto-approve precision (from `decided_by='brain'` items a person later reopened or left alone, plus replay)
    - % of items reaching an LLM
    - estimated API cost per run, from `count_tokens` on a sample × list prices
- `BASELINE.md`: written from a run inside the Railway container (see the note on data below).
- **How it runs in production:** a pipeline step "Build eval set + run eval (no LLM)" (More > Pipeline), plus `railway ssh --service receiver python -m eval.run_eval`.
- **Tests:** `tests/test_normalize.py`, `tests/test_eval.py` (gold export, split, metrics on a synthetic labelled DB), `tests/test_relink.py` (re-upload keeps labels and aliases linked).

**Data reality:** this session has no access to the production database. The gold set and BASELINE.md can only be produced in the container. Until then I'll report counts per source kind from that run. The system has been live for only a few days, so I expect **fewer than 200 person decisions**.

**Growing labels (proposal for you to approve):**
- a 1-tap reject reason on Updates and Sheet (wrong project / wrong value / not an offer / duplicate), stored in `proposals.reject_reason`
- a "Label 50" review queue that samples brain-kept and auto-decided items for spot checks
- treating Reopen as a label

### Phase 2: Unified matcher

**Files**
- `receiver/matcher.py`: one matcher. `candidates()`, `features()`, `score()`, `decide()`. Plus `rank(name, names)` for generic name lists (inventory projects, developers).
  - **Candidates:**
    - alias hit (developer-aware)
    - rapidfuzz `token_set_ratio`, `partial_ratio` and `WRatio` on `normalize`d and `strip_generic` keys **and** on `translit_key`s
    - char n-gram TF-IDF cosine over row names (project + developer): `TfidfVectorizer(analyzer='char_wb', ngram_range=(2,4))` on normalized text, plus a second vectorizer on transliteration keys so both scripts land in one space
    - the same developer's rows
    - rows this chat updated before
  - **Pairwise features:**
    - the scores above
    - developer match (exact / containment / none)
    - location code match
    - whether the sheet holds this field kind
    - chat history count
    - alias hit
    - name length ratio
    - source kind
    - Arabic/Latin script mismatch flag
- `receiver/textsim.py`: a shared char n-gram TF-IDF helper.
  - Fitted per use: master row names for the matcher; learned examples per source kind (Phase 3); messages (Phase 4).
  - The fitted vectorizer and matrix are cached under `/data/models/tfidf-<use>-<hash of the fitted corpus>.joblib`.
  - It is refitted only when the corpus changes, for example after a master re-upload or new examples.
  - No external API.
- `receiver/train_matcher.py`:
  - Builds pairs from gold matching items (positive = labelled row; negatives = other top-k candidates).
  - Model: LightGBM, or logistic regression when there are fewer than 300 labels, wrapped in `CalibratedClassifierCV`.
  - Saves to `/data/models/matcher-<version>/` (`model.txt` or `.pkl`, plus `meta.json` with version, train date, label count, feature list and eval metrics).
  - Prints the precision/coverage curve and suggests `MATCH_AUTO` and `MATCH_TIEBREAK_LOW` for precision ≥0.98.
- `receiver/master.py`: `match()` becomes a thin wrapper.
  - `MATCHER=legacy` (default) keeps today's code.
  - `MATCHER=v2` uses `matcher.decide()`.
  - `MATCHER=shadow` runs both and logs the disagreements for the eval. Proposals still use legacy.
- `receiver/brain.py`: under `BRAIN_MATCHER=v2` (default off), the per-suggestion Opus call is replaced:
  - **p ≥ `MATCH_AUTO` (env, unset = off):** retarget.
  - **Middle band:** Haiku 5.5 (`CLAUDE_TIEBREAK_MODEL`) tie-break with the top 3 candidates.
  - **Below:** new_project, or left for a person.
  - **Noise judgement:** stays an LLM call (Haiku, batched) only for suggestions the matcher can't place.
  - **Auto-approve:** still needs `safe_to_approve` plus a new `BRAIN_AUTO_APPROVE` flag (default OFF from this phase on; today the brain auto-approves with no flag, see the open question below).
- `receiver/build.py` (`link_facts`): project matching uses `matcher.rank` with a strict threshold, keeping the "exactly one project" rule. No prompt or logic change elsewhere in facts, mapping or media_review.
- `receiver/requirements.txt`: pin `rapidfuzz`, `scikit-learn`, `lightgbm`, `numpy` (and `joblib`, which comes with scikit-learn) at exact versions released at least 2 weeks ago.
- `receiver/Dockerfile`: `apt-get install libgomp1`, which LightGBM needs on slim images.
- **camel-tools:** only if it installs cleanly in the image. It is not planned by default, because its model downloads are large.
- **Tests:** `tests/test_matcher.py` (normalization and transliteration cases, cross-script candidate recall, TF-IDF cache reuse and refit, decide bands, legacy/v2/shadow equivalence where expected), `tests/test_train_matcher.py` (small synthetic set; model saved with metadata; calibrated output in [0,1]).

### Phase 3: Learning memory + dynamic few-shot
- **`receiver/db.py`:** new table `learned_examples`, with these columns:
  - `id`, `source_kind`, `source_ref`, `input_text`, or for files a reference plus a short text excerpt (PDF page text, sheet row cells)
  - `normalized_text`, `chat_jid`, `chat_developer_key`, `project_key`, `row_key`, `field`, `output_json` (what was extracted)
  - `decision` (approved/rejected/reopened), `reason`, `created_at`
  - Keyed by `row_key` and developer, never by `master_rows.id`, so it survives re-uploads.
- **`master.decide_detail`:** gains an `actor` argument (`'person'`/`'brain'`). The brain path passes `'brain'`, which is all it changes. Only `actor='person'` decisions and Reopen write examples.
- **`receiver/memory.py`:** `record(...)` and `retrieve(source_kind, query_text, developer_key, k=FEWSHOT_K)`.
  - `FEWSHOT_K` defaults to 6.
  - Results are balanced between approved and rejected, with the same developer preferred.
  - Ranking is by char n-gram TF-IDF cosine over `normalized_text` of the same source kind, with a boost for the same `project_key` and for the same chat.
- **`proposals.py` / `brain.py`:** under `FEWSHOT=1` (default off), the retrieved examples go in the **user turn** after the source. System prompts stay byte-identical, so they cache. This also works in batch mode, because the examples are baked into each request.
  - **Retrieval is per request, not per message.** A text request holds 60 messages, so I'll take the top-k over the chunk (best example per message, deduplicated, capped at k).
- **`receiver/rule_suggestions.py`:** weekly on the poller. It groups rejection reasons and the rejected inputs (by reason, then char n-gram TF-IDF + agglomerative clustering) into **suggested** additions to owner_rules_eg.json (not-a-project names, locations, blocklist terms). They are written to `/data/review/rule-suggestions-<date>.json` and shown read-only. **Never auto-applied.**
- **Tests:** `test_memory.py` covers record on person decide only, survival across re-upload, balanced retrieval, prompt prefix unchanged, and batch params including the examples.

### Phase 4: Noise filter for text messages
- **`receiver/noise.py`:** char n-gram TF-IDF on normalized message text + logistic regression (class-weighted).
  - **Positives (noise):** messages whose proposals a person rejected with reason "not an offer" (resale / client unit / request / chatter), or that the brain marked noise and a person didn't reopen.
  - **Negatives:** messages with approved proposals.
  - **Saved to** `/data/models/noise-<version>/`.
- **`proposals.text_units`:** under `NOISE_SKIP` (unset = off), messages scoring below the threshold skip the LLM.
  - Every skip is logged to a `noise_skips` table with message, score and model version.
  - Skipped messages are still marked as processed **but can be re-queued** (Pipeline: "Re-read skipped").
  - Borderline messages go through as now.
- **`eval/run_eval.py`:** reports the false-skip rate on gold, which must be < 1% before you enable it.
- **Tests:** `test_noise.py` covers training on synthetic data, the skip path, the skip log, re-queueing, and the flag off meaning unchanged behaviour.

### Phase 5: Deterministic parsing, template memory, cache
- **`cell_parse.py`:** Egyptian notation. Each pattern gets a test, and source values stay verbatim with parsed values in separate fields:
  - Arabic-Indic digits
  - مليون/ألف multipliers
  - `x% مقدم` (down payment)
  - `N سنين/سنوات` (years)
  - Franco forms (e.g. `2 bed`, `3 ghoraf`)
  - EGP variants (ج.م / جنيه / LE)
  - sq m variants (م² / متر / m2 / sqm)
- **`receiver/templates.py`:** spreadsheet fingerprint = hash(normalized header row + column count + developer key).
  - When a person has confirmed a column → master-field mapping for that fingerprint (a small "Confirm columns" action on the source row page), new sheets with the same fingerprint are extracted **in code, with no LLM call**. They then go through `add_proposal`, so the gate, rounding and brain behave as today.
  - Behind `TEMPLATES=1`, default off.
- **Cache:**
  - **Files:** `source_files` already de-duplicates by sha256, and `processed_batches` keys mean an identical file is read only once. The brief's PDF/image cache therefore mostly exists already. What I'll add is an `extraction_cache(content_key, source_kind, chat_developer_key, model, output_json)` so that a **re-read** (redo / Re-read all / a new model version) or the same file posted in another chat reuses the stored extraction.
  - **Forwarded messages:** identical normalized message text reuses the cached extraction **only when the chat developer is the same**, because the same text in another developer's group means something else.
  - Behind `EXTRACTION_CACHE=1`, default off.
- **Tests:** `test_cell_parse_eg.py`, `test_templates.py`, `test_extraction_cache.py`.

### Phase 6: Guards + calibrated auto-approve
- **`receiver/guards.py`:** EGP-per-sq-m history per project, falling back to developer + location code.
  - Data comes from master cells (as uploaded) plus approved changes (`area/price` gives price / area).
  - Robust z-score (median/MAD). If a project has fewer than 5 points, it falls back to the fixed `BRAIN_PRICE_BAND`.
- **`brain.safe_to_approve`:** uses matcher probability ≥ `MATCH_AUTO` plus the guard result, instead of the LLM's "high". All owner exclusions stay: commission, feedback, location, Hold/Hide never auto; new rows never auto.
  - Behind `GUARDS_V2=1`, default off. Today's rule stays until you enable it.
- **Tests:** `test_guards.py` covers the median/MAD maths, the fallback below 5 points, and that the exclusions still hold.

### Phase 7: Retrain loop + visibility
- **`receiver/retrain.py`:** runs on the existing poller, nightly, when there are ≥ 25 new labels since the last train. It also runs after a master re-upload, once `relink` has run.
  - It retrains the matcher and the noise model, then re-runs the eval.
  - **Promotion:** a model is promoted only if no metric drops beyond a tolerance (see concerns), otherwise the old one is kept and the reason logged.
  - Model folders under `/data/models/` are versioned, with a `current` pointer.
- **`api.py`:** `GET /api/brain/metrics` returns:
  - model versions
  - eval metrics per source kind
  - the share of items reaching an LLM
  - cost trend (from logged token usage per run)
  - flag states
- **Frontend:** a minimal "Brain" panel under More.
- **Tests:** `test_retrain.py` covers the threshold, promote vs keep, and the re-upload trigger.

### Cost logging (all phases)
`llm.call_json` and the batch results already have `usage`. I'll add an `llm_usage(ts, purpose, model, input_tokens, output_tokens, cache_read, batch)` table, so cost per run is measured rather than estimated. This is additive and logging only.

## 6. Where the brief conflicts with the code, or I'd do it differently (your call)

1. **The brain's auto-approve and auto-reject already run with no flag.** They have been live since they shipped. Your rule says every auto-decision defaults OFF. Defaulting them OFF now *changes* production behaviour, so more items would reach people. My recommendation: add `BRAIN_AUTO_APPROVE` / `BRAIN_AUTO_REJECT`, defaulting ON for these two existing behaviours only, and OFF for everything new.
2. **Rejections have no reason today.** "Noise precision/recall" (Phase 1), the noise filter (Phase 4) and the rule suggestions (Phase 3) all need to know *why* a person rejected something. Training on all rejections would teach the noise filter to skip real offers that were only rejected for a wrong value or a wrong row. That is exactly the false-skip risk. I'd add a 1-tap reject reason in Phase 1 (wrong project / wrong value / not an offer / duplicate) and train Phase 4 only on "not an offer". Without it, Phase 4 shouldn't ship.
3. **`decided_by != 'brain'`.** In SQL that comparison silently drops every person decision, because `decided_by` is NULL for a person. I'll use `decided_by IS NULL`. Reopened brain decisions are added as labels too.
4. **The Phase 5 file cache is mostly redundant.** Files are already stored once per sha256 and read once (`processed_batches`). What actually saves money is reusing extractions on **re-reads and cross-chat reposts**, and that's what I planned. A forwarded message must also key on the chat developer.
5. **"Promote only if no metric drops" will freeze the models.** With a small gold set, every metric moves by a few points between runs from noise alone. I'd promote when the primary metric (match precision at the chosen coverage) improves or holds, and no secondary metric drops by more than 1 standard error (bootstrap) or 2 points, whichever is larger.
6. **Few-shot retrieval should be per request, not per message.** Per message would add 60×k examples to each text request. I planned top-k over the chunk, capped at k.
7. **Expect very few labels at first.** The system went live this week, so the gold set is probably well under 200. LightGBM won't be trained before ~300 labels (the brief already says logistic regression). Thresholds for precision ≥ 0.98 will have **wide confidence intervals** until there are a few hundred labels per source kind. I'll show the intervals, not just the point values.
8. **Data access.** This sandbox can't reach the production database. The gold set, BASELINE.md and real metrics come from a run in the Railway container (pipeline step / `railway ssh`). In this sandbox I can only test on synthetic data.
9. **Transliteration is lossy for English names written in Arabic.** 'ماونتن فيو' carries no vowels, so the key compares consonant skeletons (both sides → `mntn f`). That catches most loanword names (Mountain View, Palm Hills, Hyde Park, Zed). It will miss names whose Arabic spelling drops or swaps consonants, and names with a genuinely Arabic name next to an English brand (e.g. 'بالم هيلز' vs 'Palm Hills' works; 'الربوة' vs 'Rabwa' works; 'مدينتي' vs 'Madinaty' works; 'سوديك ويست' vs 'SODIC West' works; but a project the team renamed in English won't). Three things cover the rest:
   - The same-developer and chat-history features.
   - The alias table, which learns from every approved near match.
   - The cross-script miss list in the eval, so you can see how big the remainder is.
10. **TF-IDF across scripts:** char n-grams of Arabic text share no characters with Latin rows, so cosine is 0 across scripts. That's why there is a second TF-IDF on transliteration keys. Without it, the feature would only help within one script.

## 7. Open questions

1. **Brain auto-decisions (concern 1):** flags default ON for today's two behaviours, or OFF?
2. **Reject-reason picker (concern 2):** OK to add it in Phase 1?
3. **Running the baseline:** can I add the eval as a pipeline step (no LLM by default), so you or I can run it in the container and paste the output into BASELINE.md?
