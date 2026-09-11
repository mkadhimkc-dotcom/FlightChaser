# BUILD PLAN — SEA → Europe Fare Watcher

**Read this entire file before writing any code.**

This supersedes any earlier spec in this repo.

---

## HOW TO WORK THIS FILE

- Phases run in order. Do not start a phase until the
  previous phase's **GATE** is confirmed by the human.
- At every GATE: stop, print what you need verified, and wait.
  Do not proceed on your own judgment.
- If something in this file conflicts with what you think is
  best practice, follow this file and say so in one line.
- Section 12 lists approaches already evaluated and rejected.
  Do not propose them.
- Ask before adding any dependency not listed in section 11.

---

## 1. WHAT IS BEING BUILT

A Python agent that polls Google Flights every 3 hours for
round-trip fares from Seattle to four European cities, ranks
those four cities three different ways, and pushes the
rankings to Telegram at least twice a day.

The user is booking **one trip** and is city-agnostic — they
will fly to whichever city wins. This is a comparison tool,
not four independent trackers.

**Hard constraint: this must cost $0/month to run.**

---

## 2. TRIP PARAMETERS

All of these belong in `config.yaml`. Nothing hardcoded.

```yaml
origin: SEA
destinations: [FCO, MAD, AGP, CDG]   # Rome, Madrid, Malaga, Paris
depart_dates: [2026-11-01, 2026-11-02, 2026-11-03,
               2026-11-04, 2026-11-05]
return_dates: [2026-11-10, 2026-11-11, 2026-11-12,
               2026-11-13, 2026-11-14, 2026-11-15]
adults: 2
cabin: economy
trip: round-trip
currency: USD
language: en-US
```

5 departures x 6 returns = **30 cells per destination**,
**120 cells total**.

The destination list is **fixed**. Do not add cities, suggest
cities, or build a "discover cheaper alternatives" feature.

---

## 3. PHASE 0 — SPIKE  ⛔ HARD GATE

Build `spike.py` only. Nothing else. This decides whether the
project is viable.

```
[ ] pip install fast-flights
[ ] Fetch the Google Flights date-grid / price-calendar for
    SEA->FCO, departures Nov 1-5, returns Nov 10-15,
    adults=2, economy, round trip, USD, en-US
[ ] Print all 30 cells: depart, return, total price,
    per-person, airline(s), stops, duration
[ ] Call build_booking_url() on the cheapest cell, print URL
[ ] Dump the raw response structure so the human can see
    what fields actually exist
```

### Two things that must be right

**Use the grid endpoint — ONE call returns all 30 cells.**
Do not loop 30 individual queries. That is a 30x difference
in API calls and is what makes this project free instead of
$275/month. If the installed version has no grid endpoint,
STOP and report before falling back to per-cell queries.

**Query with `adults=2`, never 1.** Cheap fare buckets often
have a single seat left. Querying for one passenger and
assuming two are purchasable produces alerts for fares that
do not exist.

### Also note

The human is currently on a **UK IP address**. Force
`currency=USD` and `language=en-US` explicitly. Flag in the
output that these results may carry geo skew and should be
treated as provisional until re-verified from Seattle.

### ⛔ GATE — human verifies, you do not

Print this and stop:

1. Human opens google.com/travel/flights, runs the identical
   search, and confirms **3 randomly chosen cells** match.
2. The booking URL opens the correct fare for 2 passengers.

If (1) fails, the data source is wrong. Do not build on it.

---

## 4. PHASE 1 — COLLECTOR

```
[ ] Source interface: get_grid(destination, dates) -> list[Cell]
[ ] FastFlightsSource implements it (primary)
[ ] SerpApiSource — write the class, leave it UNCONFIGURED
    (see section 10)
[ ] One poll = 4 grid calls, one per destination
[ ] Retry with exponential backoff on transient failure
[ ] Sanity filter: reject any cell < $700 or > $6000 total.
    A parse failure returning 0 must never reach the ranker.
[ ] On total failure write a status row — never silently
    log nothing
[ ] Append to prices.csv
```

### Storage: CSV, not SQLite

This file gets committed to git on every run. Git deltas
append-only text efficiently; a rewritten binary SQLite file
stores a full new copy every commit and bloats the repo.
**Use CSV.**

`prices.csv` — write this schema from the very first run.
Adding columns later means backfilling nulls.

```
run_ts_utc, iata, depart_date, return_date, price_total,
price_per_person, currency, stops_out, stops_ret,
duration_out_min, duration_ret_min, duration_total_min,
airlines, booking_url, source, geo
```

`geo` = "UK" or "US". Needed to segregate the provisional
laptop data from the real Seattle data later.

### ⛔ GATE
Run one full poll. Confirm 120 rows land in the CSV with
sane prices across all four destinations.

---

## 5. PHASE 2 — RANKING ENGINE

Three rankers behind one interface. Each ranks all four
destinations 1-4. Each row carries **its own best date
pair** — the cheapest Rome cell and the cheapest Madrid cell
will not fall on the same dates, and collapsing that out
makes the output unactionable.

### Board 1 — CheapestRanker
Lowest `price_total` per destination. Sort ascending.

### Board 2 — CheapValueRanker
Within each destination, filter to cells priced within
`value_band_pct` (default 15%) of that destination's own
minimum. Among those, pick the shortest
`duration_total_min`. Rank destinations by that cell's
duration.

### Board 3 — CompositeRanker

```
score = (w_price    * price_norm)
      + (w_duration * duration_norm)
      + (w_stops    * stop_penalty)

price_norm, duration_norm: min-max normalized across all
                           120 cells
stop_penalty: 0 = nonstop, 0.4 = one stop, 1.0 = two or more
lower score wins
```

Defaults `w_price=0.50, w_duration=0.35, w_stops=0.15`.
All three in config — the human will tune after a week.

### Rank stability — REQUIRED, not optional

Two destinations $4 apart will trade places on every poll and
corrupt the rank history. Both rules apply to all three
boards:

- **Deadband:** a challenger only displaces an incumbent if
  it beats it by >= $50 AND >= 2%. Inside the band, hold.
- **Confirmation:** a rank change must persist across 2
  consecutive polls before being written to history or
  triggering an event push.

`rankings.csv`:

```
run_ts_utc, board, rank, iata, price_total, depart_date,
return_date, airlines, stops, duration_total_min,
prev_rank, delta_price, delta_rank, confirmed
```

---

## 6. PHASE 3 — NOTIFICATIONS

### Schedule

```
Poll:      every 3h
Scheduled: 2x daily, ALWAYS fires, changed or not.
           Times are a config value (see section 13).
Event:     #1 changes in any board (confirmed over 2 polls)
           new 30-day low for any destination
           any destination drops >=8% AND >=$120 total
Silent:    pinned board edited in place every 3h via
           editMessageText (no push notification)
Failure:   push if 3 consecutive polls return zero rows
```

The scheduled push is a **separate job** from the poll. It
reads latest state and always fires. Do not couple it to
whether data changed.

### Required in every row
Destination, total price, airline, stops, duration, date
pair. Booking link is nice-to-have — if `build_booking_url()`
proves unreliable, ship without it rather than blocking.

### Format

```
✈️ SEA → EUROPE · 2 adults
Nov 3±2 → Nov 12/13±2
Fri Sep 11 · 8:00 AM PT

💰 CHEAPEST
1  FCO  $1,780  Delta/KLM      1st 14h20  Nov4→13
2  MAD  $1,912  Iberia/AA      1st 13h05  Nov2→12
3  CDG  $2,050  Air France     non 10h15  Nov5→15
4  AGP  $2,340  Lufthansa      2st 19h40  Nov1→14

⚡ CHEAPEST + SHORTEST  (within 15% of city low)
   ...same shape...

⭐ BEST OVERALL
   ...same shape...

30-day low: $1,712 FCO (Sep 22)
🔗 Book: FCO · MAD · CDG · AGP
```

Monospace the board blocks so columns align on mobile.
Booking links as one tappable row at the bottom, not
per-row. Show operating carriers; de-duplicate codeshares.

### Secrets
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from a
`.env` file that is **gitignored from the first commit**.
Never hardcode them, not even temporarily for testing.
Store the pinned board's `message_id` in a local state file
so `editMessageText` can target it across runs.

---

## 7. PHASE 4 — LAPTOP RUN (through Sunday)

Target: collecting real data before the human travels.

```
[ ] Simple scheduler — cron, launchd, or a loop. Does not
    need to be production grade.
[ ] Tag all rows geo=UK
[ ] Confirm Telegram pushes arrive on the phone
[ ] .gitignore: .env, __pycache__, state files
[ ] Private GitHub repo, push before Sunday
```

---

## 8. PHASE 5 — RASPBERRY PI MIGRATION (Seattle)

The Pi is the permanent home. Residential Seattle IP that
Google will not challenge, always-on, $0 forever.

```
[ ] 64-bit Raspberry Pi OS. fast-flights is pure Python,
    so ARM is a non-issue.
[ ] systemd service + timer. NOT a self-hosted GitHub
    Actions runner — fewer moving parts for a single-user
    project.
[ ] Timezone=America/Los_Angeles in the unit file. Let
    systemd handle the Nov 1 2026 DST shift. Do NOT
    compute UTC offsets in Python.
[ ] Persistent=true on the timer so a missed poll runs on
    next boot instead of silently skipping.
[ ] Restart=on-failure with backoff.
[ ] .env at /etc/fare-watcher.env, chmod 600, loaded via
    systemd EnvironmentFile.
[ ] Deploy key or fine-grained PAT for the git push.
[ ] Tag all rows geo=US.
[ ] Date guard: stop polling after Nov 3, 2026.
```

### Geo comparison — do this once at cutover
Run laptop and Pi against the same query in the same hour
and diff the results. If UK and US prices diverge
materially, the geo=UK rows are discarded from the history
rather than merged.

### Dead-box detection
The heartbeat catches a broken scraper but not a powered-off
Pi — nothing runs, so nothing alerts. Add a free
healthchecks.io ping on each successful run. The human is in
the UK; the Pi is in Seattle and cannot be physically
reached.

---

## 9. KNOWN CAVEATS — put these in the README

- No SEA nonstop to MAD or AGP. Verify FCO and CDG nonstop
  operation for early November; seasonal service may have
  ended.
- Without a duration cap, the #1 slot on the Cheapest board
  will often be a 30+ hour double-connection. Prefer a
  configurable `max_duration_total_min` over `max_stops`.
  Leave it **unset** by default; the human sets it after
  seeing real output.
- `fast-flights` is an unofficial scraper and will
  eventually break when Google changes its protobuf
  encoding. **Pin an exact version.** The heartbeat exists
  so breakage surfaces in hours, not weeks.
- Silent schema drift is the real long-term risk: if Google
  moves a field and the decoder starts logging outbound-only
  price as the total, prices stay plausible and every
  guardrail passes. Consider a daily Playwright cross-check
  of 3 cells against visible page text — but write that
  checker against the rendered DOM, independent of the
  fast-flights field mapping, or it inherits the same bug.

---

## 10. COST MODEL — why $0 matters here

At 3-hour polling: 8 runs/day x 4 grid calls = ~960
calls/month.

```
fast-flights on the Pi          $0        <- target
SerpApi free tier (250/mo)      $0        impossible at
                                          3h cadence
SerpApi Starter (1,000/mo)      $25/mo    96% utilized,
                                          no overage room
```

Write `SerpApiSource` against the same interface so a switch
is a config change. **Do not sign up for or integrate a paid
API key unless the human explicitly asks.**

---

## 11. DEPENDENCIES

Allowed without asking: `fast-flights` (pinned), `requests`,
`pyyaml`, `python-dotenv`.

Anything else — ask first. Keep the tree small; this runs on
a Pi and must survive seven weeks unattended.

---

## 12. REJECTED — do not propose these

- **DuckDuckGo or any SERP scrape.** Returns SEO landing-page
  copy ("Flights from $399!"), not dated bookable fares.
- **Amadeus Self-Service.** The free test environment returns
  **synthetic data**. Real fares require a paid plan.
- **Kayak / Skyscanner scraping.** Aggressive bot defenses.
- **SQLite committed to the repo.** Binary diffs bloat git.
- **Per-cell queries instead of the grid.** 30x the calls for
  identical data.
- **Adding destinations beyond the four.** Fixed shortlist.
- **Self-hosted GitHub Actions runner on the Pi.** Evaluated;
  systemd is simpler for this scope.

---

## 13. CONFIG VALUES — ship these defaults, expect tuning

| Key | Default | Note |
|---|---|---|
| `value_band_pct` | 15 | Board 2 tolerance |
| `w_price / w_duration / w_stops` | 0.50 / 0.35 / 0.15 | Board 3 |
| `deadband_usd / deadband_pct` | 50 / 2 | Rank stability |
| `confirm_polls` | 2 | Rank stability |
| `max_duration_total_min` | unset | Human sets week 1 |
| `sanity_min / sanity_max` | 700 / 6000 | Total, 2 adults |
| `push_times` | TBD | **Ask the human.** They are |
| | | in the UK; Pacific times |
| | | must land at a sane UK hour |
