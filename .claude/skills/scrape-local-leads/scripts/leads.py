#!/usr/bin/env python3
"""Google Maps -> verified email list pipeline.

Steps (each is a subcommand, run in order):
  plan     build the run plan + worst-case cost, show balances       (no spend)
  start    launch the Apify scrape with a hard spend cap            (spends Apify credit)
  collect  wait for the scrape, download it to leads/raw/            (no spend)
  clean    filter + dedupe + pick emails, report credits needed      (no spend)
  verify   MillionVerifier every email, write leads/clean/ files     (spends MV credits)
  balance  show Apify + MillionVerifier balances                     (no spend)

Stdlib only. Keys come from <repo>/.env or the environment:
  APIFY_TOKEN (or APIFY_API_TOKEN), MILLION_VERIFIER_API_KEY
Repo root = LEADS_ROOT env var, else the folder four levels above this script.
"""
import argparse, csv, datetime as dt, glob, json, math, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("LEADS_ROOT") or Path(__file__).resolve().parents[4])
CONFIG, LEADS = ROOT / "config", ROOT / "leads"
RAW, CLEAN, PENDING = LEADS / "raw", LEADS / "clean", LEADS / ".pending"
RUNS_CSV = LEADS / "runs.csv"
APIFY = "https://api.apify.com/v2"
MV = "https://api.millionverifier.com/api/v3"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

RAW_COLS = ["place_id", "business_name", "category", "categories", "website", "phone",
            "street", "city", "state", "postal_code", "rating", "review_count",
            "permanently_closed", "temporarily_closed", "emails", "facebook", "instagram",
            "google_maps_url", "search_terms"]
OUT_COLS = ["email", "email_type", "verification", "company_name", "business_name",
            "website", "domain", "phone", "street", "city", "state", "postal_code",
            "category", "categories", "rating", "review_count", "known_chain", "likely_chain",
            "facebook", "instagram", "google_maps_url", "place_id", "search_terms", "batch"]
RUNS_COLS = ["batch", "date", "state", "segment", "terms", "max_places_per_term",
             "approved_cap_usd", "apify_run_id", "apify_cost_usd", "raw_places",
             "businesses_kept", "businesses_removed", "emails_checked", "clean", "dropped_risky",
             "dropped_invalid", "mv_credits_used", "source"]


# ---------- helpers ----------

def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_env():
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def key(name, *alts):
    env = load_env()
    for n in (name,) + alts:
        if env.get(n):
            return env[n]
    return None


def cfg():
    return (json.loads((CONFIG / "settings.json").read_text()),
            json.loads((CONFIG / "segments.json").read_text()))


def http(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "bluewave-lead-scraper/1.0", **(headers or {})})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        die(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read().decode()[:400]}")


def apify(method, path, body=None, params=None):
    tok = key("APIFY_TOKEN", "APIFY_API_TOKEN")
    if not tok:
        die("No Apify token. Add APIFY_TOKEN=... to the .env file in the repo root.")
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    return http(method, f"{APIFY}{path}{q}", body, {"Authorization": f"Bearer {tok}"})


def state_code(s):
    s = s.strip()
    if s.upper() in STATES:
        return s.upper()
    for c, n in STATES.items():
        if n.lower() == s.lower():
            return c
    die(f"Unknown US state '{s}'. Use a 2-letter code like AZ or the full name.")


def domain_of(url):
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(p, cols, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def pending(batch):
    p = PENDING / f"{batch}.json"
    if not p.exists():
        die(f"No plan found for batch '{batch}'. Run `plan` first.")
    return p, json.loads(p.read_text())


def save_pending(batch, data):
    PENDING.mkdir(parents=True, exist_ok=True)
    (PENDING / f"{batch}.json").write_text(json.dumps(data, indent=2))


def apify_balance():
    if not key("APIFY_TOKEN", "APIFY_API_TOKEN"):
        return {"error": "No APIFY_TOKEN in .env"}
    try:
        d = apify("GET", "/users/me/limits")["data"]
        mx = d["limits"]["maxMonthlyUsageUsd"]
        used = d["current"]["monthlyUsageUsd"]
        return {"monthly_limit_usd": round(mx, 2), "used_this_cycle_usd": round(used, 2),
                "remaining_usd": round(mx - used, 2)}
    except SystemExit:
        raise
    except Exception as e:  # noqa
        return {"error": str(e)}


def mv_credits():
    k = key("MILLION_VERIFIER_API_KEY")
    if not k:
        return {"error": "No MILLION_VERIFIER_API_KEY in .env"}
    d = http("GET", f"{MV}/credits?api={urllib.parse.quote(k)}")
    return {"credits": d.get("credits")}


# ---------- plan ----------

def cmd_plan(a):
    settings, segments = cfg()
    if a.segment not in segments:
        die(f"Unknown segment '{a.segment}'. Options: {', '.join(k for k in segments if not k.startswith('_'))}")
    seg = segments[a.segment]
    code = state_code(a.state)
    if a.terms in (None, "core"):
        terms = seg["core_terms"]
    elif a.terms == "full":
        terms = seg["core_terms"] + seg["extra_terms"]
    else:
        terms = [t.strip() for t in a.terms.split(",") if t.strip()]
    per = a.max_per_term or settings["default_max_places_per_term"]
    max_places = per * len(terms)
    unit = settings["price_per_place_usd"] + settings["price_per_contact_enrichment_usd"]
    cap = max(math.ceil(round(max_places * unit, 6) * 100) / 100, settings["apify_min_cap_usd"])
    est_emails = int(max_places * settings["expected_emails_per_place"])
    mv_usd = round(est_emails * settings["millionverifier_usd_per_credit"], 2)

    date = a.date or dt.date.today().isoformat()
    base = f"{date}_{code}_{a.segment}"
    batch, n = base, 2
    while (PENDING / f"{batch}.json").exists() or glob.glob(str(RAW / f"{batch}_raw-*.csv")):
        batch, n = f"{base}-{n}", n + 1

    earlier = [r for r in (read_csv(RUNS_CSV) if RUNS_CSV.exists() else [])
               if r["state"] == code and r["segment"] == a.segment]
    done_terms = {t.strip() for r in earlier for t in r["terms"].split("|")}
    plan = {
        "earlier_batches_same_state": [
            {"batch": r["batch"], "terms": r["terms"], "max_places_per_term": r["max_places_per_term"],
             "raw_places": r["raw_places"], "clean": r["clean"]} for r in earlier],
        "terms_already_run": [t for t in terms if t in done_terms],
        "terms_new": [t for t in terms if t not in done_terms],
        "batch": batch, "date": date, "state_code": code, "state_name": STATES[code],
        "segment": a.segment, "segment_label": seg["label"], "terms": terms,
        "max_places_per_term": per, "max_places_total": max_places,
        "apify_cost_per_place_usd": unit, "apify_cap_usd": cap,
        "est_emails_to_verify": est_emails, "est_mv_credits": est_emails,
        "est_mv_cost_usd": mv_usd, "status": "planned",
    }
    if not a.offline:
        plan["apify_balance"] = apify_balance()
        plan["mv_balance"] = mv_credits()
    save_pending(batch, plan)
    print(json.dumps(plan, indent=2))


# ---------- start / collect ----------

def actor_input(plan):
    return {
        "searchStringsArray": plan["terms"],
        "countryCode": "us",
        "state": plan["state_name"],
        "language": "en",
        "maxCrawledPlacesPerSearch": plan["max_places_per_term"],
        "scrapeContacts": True,
        "scrapePlaceDetailPage": False,
        "skipClosedPlaces": False,
        "website": "allPlaces",
        "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
        "scrapeReviewsPersonalData": False,
    }


def cmd_start(a):
    p, plan = pending(a.batch)
    if not a.approve:
        die("Refusing to spend without --approve. Show the plan to the user and get a clear yes first.")
    if plan.get("run_id") and not a.force:
        die(f"Batch already started (run {plan['run_id']}). Use `collect`, or --force to launch again.")
    if a.fixture:
        plan.update(run_id="fixture", fixture=str(Path(a.fixture).resolve()), status="running",
                    started_at=dt.datetime.now().isoformat(timespec="seconds"))
        save_pending(a.batch, plan)
        print(json.dumps({"batch": a.batch, "run_id": "fixture", "status": "running"}))
        return
    settings, _ = cfg()
    run = apify("POST", f"/acts/{settings['apify_actor_id']}/runs", actor_input(plan),
                {"maxTotalChargeUsd": plan["apify_cap_usd"]})["data"]
    plan.update(run_id=run["id"], dataset_id=run["defaultDatasetId"], status="running",
                started_at=dt.datetime.now().isoformat(timespec="seconds"))
    save_pending(a.batch, plan)
    print(json.dumps({"batch": a.batch, "run_id": run["id"], "status": run["status"],
                      "cap_usd": plan["apify_cap_usd"],
                      "console": f"https://console.apify.com/actors/runs/{run['id']}"}, indent=2))


def join(v):
    return "; ".join(str(x) for x in v) if isinstance(v, list) else (v or "")


def flatten(items):
    """One row per place. Same place found by several terms is merged."""
    by_id = {}
    for it in items:
        pid = it.get("placeId") or it.get("url") or it.get("title")
        term = it.get("searchString") or ""
        if pid in by_id:
            r = by_id[pid]
            if term and term not in r["search_terms"].split("; "):
                r["search_terms"] += "; " + term
            extra = [e for e in (it.get("emails") or []) if e not in r["emails"].split("; ")]
            if extra:
                r["emails"] = "; ".join([x for x in r["emails"].split("; ") if x] + extra)
            continue
        by_id[pid] = {
            "place_id": pid, "business_name": it.get("title") or "",
            "category": it.get("categoryName") or "", "categories": join(it.get("categories")),
            "website": it.get("website") or "", "phone": it.get("phone") or "",
            "street": it.get("street") or "", "city": it.get("city") or "",
            "state": it.get("state") or "", "postal_code": it.get("postalCode") or "",
            "rating": it.get("totalScore") if it.get("totalScore") is not None else "",
            "review_count": it.get("reviewsCount") if it.get("reviewsCount") is not None else "",
            "permanently_closed": bool(it.get("permanentlyClosed")),
            "temporarily_closed": bool(it.get("temporarilyClosed")),
            "emails": join(it.get("emails")), "facebook": join(it.get("facebooks")),
            "instagram": join(it.get("instagrams")), "google_maps_url": it.get("url") or "",
            "search_terms": term,
        }
    return list(by_id.values())


def cmd_collect(a):
    p, plan = pending(a.batch)
    if not plan.get("run_id"):
        die("Batch not started yet. Run `start` after the user approves the plan.")
    if plan.get("raw_file"):
        print(json.dumps({"status": "collected", "raw_file": plan["raw_file"]}))
        return
    if plan["run_id"] == "fixture":
        items = json.loads(Path(plan["fixture"]).read_text())
        cost, status = 0.0, "SUCCEEDED"
    else:
        deadline = time.time() + a.max_minutes * 60
        while True:
            run = apify("GET", f"/actor-runs/{plan['run_id']}")["data"]
            status = run["status"]
            if status not in ("READY", "RUNNING"):
                break
            if time.time() > deadline:
                print(json.dumps({"status": "still_running", "run_id": plan["run_id"],
                                  "note": "Run `collect` again to keep waiting."}))
                sys.exit(2)
            time.sleep(20)
        if status not in ("SUCCEEDED", "ABORTED", "TIMED-OUT"):
            die(f"Apify run ended with status {status}. See https://console.apify.com/actors/runs/{plan['run_id']}")
        settings, _ = cfg()
        counts = run.get("chargedEventCounts") or {}
        cost = counts.get("place-scraped", 0) * settings["price_per_place_usd"] + \
            counts.get("contact-details-scraped", 0) * settings["price_per_contact_enrichment_usd"]
        items, offset = [], 0
        while True:
            chunk = apify("GET", f"/datasets/{plan['dataset_id']}/items",
                          params={"clean": "true", "format": "json", "offset": offset, "limit": 1000})
            items += chunk
            if len(chunk) < 1000:
                break
            offset += 1000
    rows = flatten(items)
    raw = RAW / f"{a.batch}_raw-{len(rows)}.csv"
    write_csv(raw, RAW_COLS, rows)
    plan.update(status="collected", raw_file=str(raw.relative_to(ROOT)), raw_places=len(rows),
                apify_status=status, apify_cost_usd=round(cost, 2))
    save_pending(a.batch, plan)
    print(json.dumps({"status": "collected", "apify_status": status, "raw_file": plan["raw_file"],
                      "places": len(rows), "apify_cost_usd_approx": round(cost, 2),
                      "hit_cap": status == "ABORTED"}, indent=2))


# ---------- clean ----------

EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")
SUFFIX_RE = re.compile(r"[,\s]+(llc|l\.l\.c\.|inc\.?|incorporated|co\.|corp\.?|corporation|ltd\.?)$", re.I)


def clean_company(name):
    n = name.strip()
    for sep in (" | ", " - ", " – ", " — ", ": "):
        if sep in n and len(n.split(sep)[0].strip()) >= 3:
            n = n.split(sep)[0].strip()
    for _ in range(2):
        n = SUFFIX_RE.sub("", n).strip()
    return n


def normalise_email(e):
    e = e.strip().lower()
    e = re.sub(r"^(mailto:|u003e|%20|20)", "", e).strip(" .,;:<>\"'()[]")
    return e


def junk_reason(e, settings):
    if not EMAIL_RE.match(e):
        return "not a valid email format"
    local, dom = e.split("@", 1)
    if re.search(r"\.(png|jpe?g|gif|webp|svg|css|js)$", e):
        return "looks like a file name, not an email"
    if dom in settings["junk_email_domains"] or any(dom.endswith("." + d) for d in settings["junk_email_domains"]):
        return "placeholder or website-builder address"
    if re.fullmatch(r"[0-9a-f]{16,}", local) or len(local) > 40:
        return "machine-generated address"
    return None


def email_type(e, row, generic):
    """generic = a shared inbox (info@, customercare@, scottsdale@, glendalehardwood@gmail.com)."""
    local = re.sub(r"[^a-z]", "", e.split("@")[0].lower())
    if local in generic or any(g in local for g in generic if len(g) >= 4):
        return "generic"
    city = re.sub(r"[^a-z]", "", row.get("city", "").lower())
    if city and city in local:
        return "generic"
    words = [w for w in re.findall(r"[a-z]+", row.get("business_name", "").lower()) if len(w) >= 5]
    if any(w in local for w in words):
        return "generic"
    return "personal"


def history(exclude_batch):
    """place_ids, domains and emails already delivered in earlier batches."""
    ids, doms, emails = set(), set(), set()
    for f in glob.glob(str(CLEAN / "*_clean-*.csv")):
        if Path(f).name.startswith(exclude_batch + "_"):
            continue
        for r in read_csv(f):
            ids.add(r.get("place_id", ""))
            if r.get("domain"):
                doms.add(r["domain"])
            emails.add(r.get("email", ""))
    return ids - {""}, doms, emails - {""}


def cmd_clean(a):
    p, plan = pending(a.batch)
    if not plan.get("raw_file"):
        die("Nothing collected yet. Run `collect` first.")
    settings, segments = cfg()
    seg = segments[plan["segment"]]
    allow = [w.lower() for w in seg["category_allow"]]
    deny = [w.lower() for w in seg["category_deny"]]
    chains = [c.lower() for c in settings["known_chains"]]
    generic = set(settings["generic_local_parts"])
    platforms = settings["not_a_real_website"]
    past_ids, past_doms, past_emails = history(a.batch)

    rows = read_csv(ROOT / plan["raw_file"])
    dom_count = {}
    for r in rows:
        d = domain_of(r["website"])
        if d and not any(d == x or d.endswith("." + x) for x in platforms):
            dom_count[d] = dom_count.get(d, 0) + 1
    # biggest listing first, so when a domain repeats we keep the main location
    rows.sort(key=lambda r: -float(r["review_count"] or 0))

    kept_biz, removed, seen_dom, seen_email, candidates = 0, [], set(), set(), []
    reasons = {}

    def drop(r, why, email=""):
        reasons[why] = reasons.get(why, 0) + 1
        removed.append({**base_row(r), "email": email, "reason": why})

    def base_row(r):
        d = domain_of(r["website"])
        name_l = r["business_name"].lower()
        return {
            "company_name": clean_company(r["business_name"]), "business_name": r["business_name"],
            "website": r["website"], "domain": d, "phone": r["phone"], "street": r["street"],
            "city": r["city"], "state": r["state"], "postal_code": r["postal_code"],
            "category": r["category"], "categories": r["categories"], "rating": r["rating"],
            "review_count": r["review_count"],
            "known_chain": "yes" if any(c in name_l for c in chains) else "no",
            "likely_chain": "yes" if (dom_count.get(d, 0) >= settings["chain_domain_threshold"]
                                      or len([x for x in r["emails"].split(";") if x.strip()]) >= 4) else "no",
            "facebook": r["facebook"], "instagram": r["instagram"],
            "google_maps_url": r["google_maps_url"], "place_id": r["place_id"],
            "search_terms": r["search_terms"], "batch": a.batch,
        }

    for r in rows:
        cats = [c.strip().lower() for c in r["categories"].split(";") if c.strip()] or [r["category"].lower()]
        main = r["category"].lower()
        d = domain_of(r["website"])
        real_site = d and not any(d == x or d.endswith("." + x) for x in platforms)
        b = base_row(r)
        if r["permanently_closed"] == "True":
            drop(r, "permanently closed"); continue
        if r["temporarily_closed"] == "True":
            drop(r, "temporarily closed"); continue
        if not r["website"]:
            drop(r, "no website"); continue
        if not any(w in c for c in cats for w in allow):
            drop(r, "off-category (no matching Google category)"); continue
        if any(w in main for w in deny):
            drop(r, f"off-category (main category: {r['category']})"); continue
        if settings["exclude_known_chains"] and b["known_chain"] == "yes":
            drop(r, "known chain / big box"); continue
        if r["place_id"] in past_ids or (real_site and d in past_doms):
            drop(r, "already in an earlier batch"); continue
        if real_site and d in seen_dom:
            drop(r, "same website as another listing in this batch"); continue
        emails, junk = [], []
        for e in [normalise_email(x) for x in r["emails"].split(";") if x.strip()]:
            if not e or e in emails:
                continue
            why = junk_reason(e, settings)
            (junk.append((e, why)) if why else emails.append(e))
        for e, why in junk:
            drop(r, f"junk email: {why}", e)
        fresh = []
        for e in emails:
            if e in past_emails:
                drop(r, "email already in an earlier batch", e)
            elif e in seen_email:
                drop(r, "email already used by another listing in this batch", e)
            else:
                fresh.append(e)
        if not fresh:
            drop(r, "no usable email found on website"); continue
        # personal addresses first, then generic, capped per business
        fresh.sort(key=lambda e: email_type(e, r, generic) == "generic")
        cap = settings["max_emails_per_business"]
        for e in fresh[cap:]:
            drop(r, f"extra email beyond {cap} per business", e)
        for e in fresh[:cap]:
            seen_email.add(e)
            candidates.append({**b, "email": e, "email_type": email_type(e, r, generic)})
        if real_site:
            seen_dom.add(d)
        kept_biz += 1

    (PENDING / f"{a.batch}.candidates.json").write_text(json.dumps(candidates, indent=1))
    plan.update(status="cleaned", businesses_kept=kept_biz,
                businesses_removed=len(rows) - kept_biz, emails_to_verify=len(candidates))
    save_pending(a.batch, plan)
    out = {"batch": a.batch, "raw_places": len(rows), "businesses_kept": kept_biz,
           "businesses_removed": len(rows) - kept_biz, "removal_reasons": reasons,
           "emails_to_verify": len(candidates),
           "personal": sum(1 for c in candidates if c["email_type"] == "personal"),
           "generic": sum(1 for c in candidates if c["email_type"] == "generic"),
           "mv_credits_needed": len(candidates),
           "mv_cost_usd_approx": round(len(candidates) * settings["millionverifier_usd_per_credit"], 2),
           "planned_mv_credits": plan["est_mv_credits"],
           "over_plan": len(candidates) > plan["est_mv_credits"]}
    if not a.offline:
        out["mv_balance"] = mv_credits()
    print(json.dumps(out, indent=2))


# ---------- verify ----------

def mock_result(e):
    if "invalid" in e or "bounce" in e:
        return "invalid"
    if "catchall" in e or "catch-all" in e:
        return "catch_all"
    if "unknown" in e:
        return "unknown"
    return "ok"


def mv_check(e, k):
    for attempt in range(3):
        try:
            d = http("GET", f"{MV}/?api={urllib.parse.quote(k)}&email={urllib.parse.quote(e)}&timeout=20", timeout=40)
            res = (d or {}).get("result", "error")
            if res != "error" or attempt == 2:
                return res
        except SystemExit:
            if attempt == 2:
                return "error"
        time.sleep(2)
    return "error"


def cmd_verify(a):
    p, plan = pending(a.batch)
    if plan.get("status") != "cleaned":
        die("Run `clean` before `verify`.")
    if not a.approve:
        die("Refusing to spend MillionVerifier credits without --approve.")
    cands = json.loads((PENDING / f"{a.batch}.candidates.json").read_text())
    # one cache for every batch, so an email is only ever paid for once
    cache_f = PENDING / "verified-emails.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    old_f = PENDING / f"{a.batch}.verified.json"
    if old_f.exists():
        cache.update(json.loads(old_f.read_text()))
    todo = [c["email"] for c in cands if c["email"] not in cache]
    if a.mock:
        cache.update({e: mock_result(e) for e in todo})
    elif todo:
        k = key("MILLION_VERIFIER_API_KEY") or die("No MILLION_VERIFIER_API_KEY in .env")
        with ThreadPoolExecutor(max_workers=8) as ex:
            cache.update(zip(todo, ex.map(lambda e: mv_check(e, k), todo)))
    cache_f.write_text(json.dumps(cache, indent=1))
    results = [cache[c["email"]] for c in cands]

    # only emails that verify "ok" are kept; everything else is counted, not saved
    clean = []
    for c, res in zip(cands, results):
        if res == "ok":
            clean.append({**c, "verification": res})
    dropped_risky = sum(r in ("catch_all", "unknown", "error") for r in results)
    dropped_invalid = len(results) - len(clean) - dropped_risky

    stem = a.batch
    clean_f = CLEAN / f"{stem}_clean-{len(clean)}.csv"
    for old in glob.glob(str(CLEAN / f"{stem}_*.csv")):
        os.remove(old)
    write_csv(clean_f, OUT_COLS, clean)

    credits = 0 if a.mock else len(todo) + int(plan.get("mv_credits_used", 0))
    run_row = {
        "batch": stem, "date": plan["date"], "state": plan["state_code"], "segment": plan["segment"],
        "terms": " | ".join(plan["terms"]), "max_places_per_term": plan["max_places_per_term"],
        "approved_cap_usd": plan["apify_cap_usd"], "apify_run_id": plan["run_id"],
        "apify_cost_usd": plan.get("apify_cost_usd", ""), "raw_places": plan["raw_places"],
        "businesses_kept": plan["businesses_kept"], "businesses_removed": plan["businesses_removed"],
        "emails_checked": len(cands), "clean": len(clean), "dropped_risky": dropped_risky,
        "dropped_invalid": dropped_invalid, "mv_credits_used": credits,
        "source": "fixture" if plan["run_id"] == "fixture" else "apify",
    }
    old_rows = [r for r in read_csv(RUNS_CSV) if r["batch"] != stem] if RUNS_CSV.exists() else []
    write_csv(RUNS_CSV, RUNS_COLS, old_rows + [run_row])
    plan["mv_credits_used"] = credits
    plan.update(status="done", files={"clean": str(clean_f.relative_to(ROOT))})
    save_pending(a.batch, plan)
    print(json.dumps({"batch": stem, "clean": len(clean),
                      "dropped_risky_catch_all_or_unknown": dropped_risky,
                      "dropped_invalid": dropped_invalid,
                      "verification_breakdown": {r: results.count(r) for r in set(results)},
                      "clean_file": plan["files"]["clean"],
                      "runs_log": str(RUNS_CSV.relative_to(ROOT))}, indent=2))


def cmd_check(a):
    """First-run setup check: .env present, both keys valid, whose accounts they are."""
    out = {"env_file": str(ROOT / ".env"), "env_exists": (ROOT / ".env").exists()}
    tok = key("APIFY_TOKEN", "APIFY_API_TOKEN")
    if tok:
        me = apify("GET", "/users/me")["data"]
        email = me.get("email") or ""
        out["apify"] = {"ok": True, "username": me.get("username"),
                        "name": (me.get("profile") or {}).get("name"),
                        "email": (email[:3] + "***@" + email.split("@")[1]) if "@" in email else "",
                        "plan": (me.get("plan") or {}).get("id"), **apify_balance()}
    else:
        out["apify"] = {"ok": False, "error": "APIFY_TOKEN missing"}
    out["millionverifier"] = {"ok": "credits" in mv_credits(), **mv_credits()}
    print(json.dumps(out, indent=2))
    if not (out["apify"]["ok"] and out["millionverifier"]["ok"]):
        sys.exit(1)


def cmd_balance(a):
    print(json.dumps({"apify": apify_balance(), "millionverifier": mv_credits()}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan"); p.add_argument("--segment", required=True); p.add_argument("--state", required=True)
    p.add_argument("--terms", help="core (default) | full | comma-separated list")
    p.add_argument("--max-per-term", type=int); p.add_argument("--date")
    p.add_argument("--offline", action="store_true", help="skip balance lookups")
    p = sp.add_parser("start"); p.add_argument("--batch", required=True)
    p.add_argument("--approve", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--fixture", help="use a saved Apify dataset JSON instead of a live run (testing)")
    p = sp.add_parser("collect"); p.add_argument("--batch", required=True)
    p.add_argument("--max-minutes", type=float, default=9)
    p = sp.add_parser("clean"); p.add_argument("--batch", required=True); p.add_argument("--offline", action="store_true")
    p = sp.add_parser("verify"); p.add_argument("--batch", required=True)
    p.add_argument("--approve", action="store_true"); p.add_argument("--mock", action="store_true",
                                                                     help="fake results, no credits (testing)")
    sp.add_parser("balance")
    sp.add_parser("check", help="first-run setup check: keys valid, which accounts")
    a = ap.parse_args()
    {"plan": cmd_plan, "start": cmd_start, "collect": cmd_collect, "clean": cmd_clean,
     "verify": cmd_verify, "balance": cmd_balance, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
