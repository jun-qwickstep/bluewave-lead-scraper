---
name: scrape-local-leads
description: Scrape local businesses from Google Maps (Apify Google Maps Scraper), pull emails from their websites, clean and dedupe the list, verify every email with MillionVerifier, and save send-ready CSVs in leads/. Built for Blue Wave AI's two segments (flooring retailers, epoxy/concrete coating contractors) but works for any segment in config/segments.json. ALWAYS use this skill whenever someone wants new leads, a new list, a scrape, or more prospects from Google Maps, even if they only say "scrape Arizona", "get me flooring stores in Texas", "run a new batch", "pull epoxy contractors in Florida and Georgia", "how much would it cost to scrape California", "find more stores", or "let's do the next state". Also use it for any status or follow-up on a scrape or batch, like "is the Florida scrape done yet?", "how did the Texas run go", "finish the batch", "verify the emails from yesterday's run". Every run starts with a short interview and a cost summary, and nothing is spent until the user says yes.
---

# Scrape local leads

Turns "flooring stores in Arizona" into a verified email list, in this order:

**interview → cost summary → user says yes → scrape → clean → verify → files + summary**

Everything runs through one script: `.claude/skills/scrape-local-leads/scripts/leads.py` (Python standard library only; run it from the repo root). Each step is a subcommand, and the script keeps state per batch in `leads/.pending/`, so a batch can be picked up again in a later session.

## Why the guardrails exist

Two things matter more than speed on this account.

1. **Money is only spent after a clear yes to the actual numbers.** Apify charges per business scraped and MillionVerifier charges per email checked, and they charge the owner's card. The script refuses to spend without `--approve`, and the Apify run gets a hard cap (`maxTotalChargeUsd`) equal to the approved amount, so it cannot spend a cent more. Never pass `--approve` until the user has seen the cost summary from Step 2 and said yes afterwards. A "go ahead" or "you have my green light" sent *before* they've seen the numbers doesn't count, however confident it sounds. People say that expecting a small number, and the whole point is that they see the real one. In that case show the summary, say you'll start as soon as they confirm, and stop. "Yes", "go", "sounds good" after the summary all count.
2. **The market is small, so every business gets contacted once.** There are roughly 12,000 flooring retailers in the US. The cleaner skips any business, website or email already in an earlier batch in `leads/clean/`. Don't delete old files in `leads/clean/` to "start fresh". That's the memory that stops the same store getting emailed twice.

## Step 0: First-time setup (only if `.env` is missing or a key fails)

Run `python3 .claude/skills/scrape-local-leads/scripts/leads.py check`. If it fails:

1. If `.env` doesn't exist, copy it: `cp .env.example .env`.
2. Ask the user to open `.env` in their editor and paste their own Apify token and MillionVerifier key. Tell them where each one lives (see `references/setup.md`), and ask them **not** to paste keys into the chat.
3. Run `check` again. It shows which Apify account and plan the token belongs to (username plus masked email) and the MillionVerifier credit balance. Read the account name back to the user so they can confirm it's the right account. A token from the wrong account means the wrong card gets charged.

Only continue once `check` passes.

## Step 1: Interview

Ask only what isn't already clear from the request. Keep it short, one message, with sensible defaults filled in so the user can just say "yes" or change one thing. Read `leads/runs.csv` first. If earlier batches exist, mention which states are already done so nobody scrapes one twice by accident.

What you need:

| Question | Default | Notes |
|---|---|---|
| Segment | none, ask | `flooring` (retailers, the priority, ~70% of effort) or `coating` (epoxy / concrete coating contractors). Keys live in `config/segments.json`. |
| State(s) | none, ask | One batch per state. 2-letter code or full name. The scraper covers the whole state by itself, so no need to list cities. |
| Search terms | `core` (5 terms) | Show the actual terms. `full` adds 4–5 more, which mostly return the same shops again, so each extra term costs money for a small gain. The user can also give a custom list. |
| Max places per term | 300 | This is the cap per search term. Suggest by state size: small (WY, VT, ND…) 100–150, mid (AZ, CO, TN…) 300, big (CA, TX, FL, NY) 600–1000. If the cap is too low in a big state, the list comes back incomplete. Too high costs nothing extra unless the businesses actually exist. |

**Repeat state.** The plan output lists `earlier_batches_same_state`, `terms_already_run` and `terms_new`. If this state and segment have been run before, say so, and by default offer only the new terms. Rerunning terms that already ran mostly finds businesses that are already in a list, and those get filtered out anyway, so it's money for nothing. The exception is when the earlier batch hit its per-term cap (`raw_places` close to terms × cap). Then a higher cap on the same terms can find more.

**Franchises.** Chains and franchises (Floor & Decor, Garage Force, Premier Garage…) are flagged `known_chain` and kept by default. For coating in big states they can be a real share of the list, so ask once whether to drop them. If yes, set `exclude_known_chains` to `true` in `config/settings.json` before running `clean`.

Things to say during the interview when relevant, because they set expectations:
- Google Maps has no revenue or employee counts, so the brief's $500K–$10M / 3–25 staff filters can't be applied. Review count and the `likely_chain` flag are the proxies, and they're columns in the output.
- Most emails found on small-business websites are shared inboxes (info@, office@). That's expected and fine. They're kept in the same list and tagged `email_type = generic`.

## Step 2: Plan and cost summary

For each state:

```bash
python3 .claude/skills/scrape-local-leads/scripts/leads.py plan --segment flooring --state AZ --terms core --max-per-term 300
```

It prints JSON with the batch name, terms, `apify_cap_usd` (the worst case: every term hits the cap), estimated emails and MillionVerifier credits, and live balances for both accounts (`--offline` skips the balance lookups).

Turn that into a plain-English summary. Something like:

```
Here's what I'm about to run:

  Batch       2026-09-24_AZ_flooring
  Who         Flooring retailers and dealers in Arizona
  Terms       flooring store, carpet store, floor covering store,
              hardwood flooring store, tile store
  Size        up to 300 places per term, so 1,500 places max

  Apify       up to $9.00 (hard cap, it can't go over this)
              you have $4.20 left this month  <-- flag if cap > remaining
  Verify      roughly 1,275 emails, about 1,275 MillionVerifier credits (~$4.70)
              you have 9,800 credits

  Most runs cost less than the cap, because the cap assumes every
  term finds 300 shops. This is a one-off charge per scrape, not a subscription.

Want me to go ahead?
```

For several states, show one block per state plus a total line. One yes covers everything listed.

Apify won't accept a cap below $0.50, so small test runs show a $0.50 cap even when they'll spend less. Say so, and give the realistic figure (places × $0.006).

If the Apify cap is more than the remaining balance, say so plainly: Apify stops the run when the monthly limit runs out, so the list would come back partial. The fix (raise the limit or upgrade the plan) is the account owner's call. Don't suggest working around it.

If balances came back as errors (missing keys), say which key is missing from `.env` and stop there. See `references/setup.md`.

**Wait for the yes.** Then continue.

## Step 3: Scrape

```bash
python3 .claude/skills/scrape-local-leads/scripts/leads.py start --batch <batch> --approve
python3 .claude/skills/scrape-local-leads/scripts/leads.py collect --batch <batch>
```

`start` launches the Apify run and prints a console link. Share it, since the user can watch progress there. `collect` waits up to 9 minutes by default. A whole state usually takes 10–60 minutes, so when `collect` exits with code 2 (`still_running`), just run it again. Running it in the background works too. When it's done, it writes `leads/raw/<batch>_raw-<n>.csv` (one row per business, with the same business found by several terms already merged) and reports the approximate Apify cost. `hit_cap: true` means the spend cap stopped the run early. Say so, because the state may not be fully covered.

## Step 4: Clean

```bash
python3 .claude/skills/scrape-local-leads/scripts/leads.py clean --batch <batch>
```

It's free and runs locally. The script removes, and counts by reason: closed businesses, no website, off-category results (a place needs one Google category matching the segment's `category_allow` words, and its main category must not match `category_deny`), businesses already delivered in an earlier batch, extra locations sharing one website, junk emails (image file names, website-builder placeholders), and businesses where no usable email was found. It keeps at most 2 emails per business (`max_emails_per_business`), personal addresses first.

It prints how many emails need verifying = MillionVerifier credits. If `over_plan` is true (more emails than the estimate the user approved), show the new number and ask again before verifying. Otherwise the original yes covers it.

## Step 5: Verify

```bash
python3 .claude/skills/scrape-local-leads/scripts/leads.py verify --batch <batch> --approve
```

Every email goes to MillionVerifier. Only `ok` emails are kept, in `leads/clean/<batch>_clean-<n>.csv`, **the send-ready list**. Catch-all, unknown and invalid emails are dropped. They're only counted (`dropped_risky`, `dropped_invalid`), never saved, because a bounce costs more than the lead is worth on this small a market. Results are cached across batches in `leads/.pending/verified-emails.json`, so an email is never paid for twice.

Each batch ends up as exactly two files: the raw scrape in `leads/raw/` and the clean list in `leads/clean/`. It also appends one line to `leads/runs.csv` (the log of every batch: terms, cap, cost, counts).

## Step 6: Report back

End with a short summary the user can act on:

```
Arizona flooring is done.

  1,212 businesses found → 684 kept → 903 emails checked
  ✅ 712 ready to send    leads/clean/2026-09-24_AZ_flooring_clean-712.csv
  Dropped at verification: 121 catch-all / unknown, 70 invalid

  Spent: ~$6.80 on Apify (cap was $9.00), 903 MillionVerifier credits
  Mix: 38% personal emails, 62% shared inboxes (info@, office@)
  Top removal reasons: no email on website (301), off-category (120), duplicate site (64)
  Worth a look: 14 rows flagged known_chain (Floor & Decor etc.), filter them out if you don't want them
```

Pull the numbers from the script output and `leads/runs.csv`, never estimate them. If something looks off (almost nothing kept, a surprising removal reason dominating the counts), say so and suggest a fix to `config/segments.json` instead of changing it silently.

## Output columns

The clean file is ready to import into Instantly. Columns: `email`, `email_type` (personal/generic), `verification`, `company_name` (cleaned: taglines and LLC/Inc removed), `business_name` (as on Google), `website`, `domain`, `phone`, `street`, `city`, `state`, `postal_code`, `category`, `categories`, `rating`, `review_count`, `known_chain`, `likely_chain` (same website across 3+ listings), `facebook`, `instagram`, `google_maps_url`, `place_id`, `search_terms`, `batch`. There are no first names. Google Maps doesn't list owners.

## Picking up a batch later

`ls leads/.pending/` shows every batch and its `status` (`planned` → `running` → `collected` → `cleaned` → `done`). Continue from the next step. A `planned` batch has **not** been approved. Show the summary again before starting it, because balances may have changed.

## Changing what gets scraped

- Search terms, category keywords, new segments: `config/segments.json`
- Emails per business, chain list, whether known chains get removed (`exclude_known_chains`, off by default, flagged only), generic inbox words, junk domains, prices: `config/settings.json`

If the user asks for a new segment (say "roofers"), add a block to `segments.json` with the same shape and confirm the terms and category words with them before running.

## Testing without spending

`start --fixture <dataset.json>` uses a saved Apify dataset instead of a live run, and `verify --mock` fakes the verification results (anything containing "invalid"/"bounce" fails, "catchall" is catch-all, "unknown" is unknown). Use both together, plus `plan --offline`, for any dry run. Batches made this way are logged with `source = fixture`.

## Troubleshooting

See `references/setup.md` for keys, the Apify actor, and common errors.
