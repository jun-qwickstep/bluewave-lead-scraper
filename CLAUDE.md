# Blue Wave AI lead scraper

This repo builds cold email lists from Google Maps for Blue Wave AI's two segments: independent flooring retailers (priority) and epoxy / concrete coating contractors.

For any request about new leads, scraping a state, costs of a scrape, or finishing a batch, use the `scrape-local-leads` skill in `.claude/skills/`. It interviews, shows the cost, waits for a yes, then scrapes, cleans and verifies.

If the user says "check my setup" (or similar), run `python3 .claude/skills/scrape-local-leads/scripts/leads.py check` and explain the result in plain words: which Apify account and balance, how many MillionVerifier credits, and what to fix if a key is missing. If `.env` doesn't exist yet, create it with `cp .env.example .env`, open it for them, and tell them which line gets which key.

Rules that matter:
- Nothing that costs money runs without a clear yes from the user in this conversation.
- Never delete or rewrite files in `leads/clean/`. They're what stops the same business being contacted twice.
- Keys live in `.env` (see `.env.example`). Never print them or commit them.
