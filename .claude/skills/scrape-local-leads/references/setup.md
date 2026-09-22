# Setup and troubleshooting

## Keys (`.env` in the repo root)

Check them any time with `python3 .claude/skills/scrape-local-leads/scripts/leads.py check`, which shows the Apify account name, plan and balance plus the MillionVerifier credits. It exits non-zero if either key is missing or rejected.

Copy `.env.example` to `.env` and fill in:

| Key | Where to get it |
|---|---|
| `APIFY_TOKEN` | Apify console → Settings → API & Integrations → Personal API token. Use the token from the account that should be charged. `APIFY_API_TOKEN` works too. |
| `MILLION_VERIFIER_API_KEY` | MillionVerifier → API → your API key |

`.env` is gitignored. Never commit it and never paste keys into chat.

## The Apify actor

- Actor: `compass/crawler-google-places` ("Google Maps Scraper"), id `nwua9Gu5YrADL7ZDj`, set in `config/settings.json`.
- Inputs the script sends: `searchStringsArray` (terms), `countryCode: us`, `state: <full name>`, `maxCrawledPlacesPerSearch`, `scrapeContacts: true` (website email add-on). Reviews, images, detail pages and the paid filters are off. Filtering happens locally at no cost.
- Pricing is pay-per-event, checked 2026-09-22 on the FREE tier: $0.004 per place + $0.002 per place for the contacts add-on = $0.006. Paid plans are cheaper (Starter tier $0.003 + $0.002). If Apify changes prices, update `price_per_place_usd` / `price_per_contact_enrichment_usd` in settings. The live price list is on the actor's Pricing tab.
- Add-ons deliberately left off: "Business leads enrichment" (named people and work emails, $0.10/lead on the free tier, weak coverage for local shops) and its email verification (MillionVerifier does that job).

## MillionVerifier

- Single-email API, 1 credit per email, 8 at a time in parallel.
- Only `ok` goes into the clean file. `catch_all` / `unknown` / `error` and `invalid` / `disposable` are dropped and counted in `runs.csv` (`dropped_risky`, `dropped_invalid`). Results are cached in `leads/.pending/verified-emails.json`, so reruns never re-pay for an email.

## Common errors

| Message | Fix |
|---|---|
| `No Apify token` | Add `APIFY_TOKEN` to `.env` |
| `HTTP 402` / `monthly usage hard limit exceeded` on start | Apify balance is out. Raise the limit or upgrade in Apify billing. That's the account owner's decision. |
| `collect` exits with `still_running` | Normal for a whole state. Run `collect` again. |
| `apify_status: ABORTED`, `hit_cap: true` | The spend cap stopped the run. The data up to that point was saved. Plan a follow-up batch with a higher cap if the state needs full coverage. |
| `Run clean before verify` | Steps run in order: plan → start → collect → clean → verify |
| Very few businesses kept, lots of "off-category" | Look at the `removal_reasons` counts from `clean`, and at the `category` column of the raw file. Add missing words to `category_allow` or remove over-eager ones from `category_deny` in `config/segments.json`, then run `clean` again (free). |
| Need to redo cleaning after a config change | `clean` then `verify` can be rerun for the same batch. Verify spends credits again, so ask first. |
