"""Phase 0 spike: does fast-flights' get_calendar_grid() give us a real,
single-call price grid for SEA -> FCO across our date range?

This is throwaway/diagnostic code, not the collector. See BUILD-PLAN.md
section 3.

NOTE ON SOURCE: BUILD-PLAN.md section 3 says `pip install fast-flights`
(the AWeirdDev PyPI package). That package (checked: v3.1.0, the only
version on PyPI) has NO grid/calendar endpoint and no build_booking_url()
- confirmed by reading its installed source and its PyPI page. Per the
human's explicit instruction, this spike instead uses a fork that claims
a real get_calendar_grid():

    pip install git+https://github.com/jumping2000/google-flights

pinned to commit d8962650dbcb79dd378d7ce92f41500e2ec8b024 (the exact
version installed and inspected for this spike).
"""

import time

from fast_flights import FlightQuery, Passengers, create_query, get_flights, get_calendar_grid

ADULTS = 2
CURRENCY = "USD"
LANGUAGE = "en-US"

FROM_AIRPORT = "SEA"
TO_AIRPORT = "FCO"
DEPARTURE_RANGE = ("2026-11-01", "2026-11-05")
RETURN_RANGE = ("2026-11-10", "2026-11-15")


def main():
    print(f"=== get_calendar_grid: {FROM_AIRPORT} -> {TO_AIRPORT} ===")
    print(f"departure_range={DEPARTURE_RANGE} return_range={RETURN_RANGE}")
    print(f"adults={ADULTS} currency={CURRENCY} language={LANGUAGE}")
    print()

    t0 = time.monotonic()
    calendar = get_calendar_grid(
        from_airport=FROM_AIRPORT,
        to_airport=TO_AIRPORT,
        departure_range=DEPARTURE_RANGE,
        return_range=RETURN_RANGE,
        adults=ADULTS,
        currency=CURRENCY,
        language=LANGUAGE.split("-")[0],
    )
    elapsed = time.monotonic() - t0

    print(f"Elapsed: {elapsed:.2f}s for one get_calendar_grid() call")
    print(f"Cells returned: {len(calendar.entries)} (expected 30 for a full 5x6 grid)")
    print()

    if not calendar.entries:
        print("!!! ZERO CELLS RETURNED. Grid call failed or was empty. STOP. !!!")
        return

    print("--- All cells ---")
    for e in sorted(calendar.entries, key=lambda e: (e.outbound_date, e.return_date)):
        print(
            f"depart={e.outbound_date} return={e.return_date} "
            f"total=${e.price:.0f} {e.currency}"
        )
    print()

    cheapest = calendar.cheapest()
    print(f"--- Cheapest cell ---")
    print(
        f"depart={cheapest.outbound_date} return={cheapest.return_date} "
        f"total=${cheapest.price:.0f} {cheapest.currency}"
    )
    print()

    # The grid endpoint gives price + dates only, not airline/stops/duration
    # per BUILD-PLAN's required output. Run one get_flights() search on the
    # cheapest date pair to pull those fields and build a real booking URL.
    print("--- Detail + booking URL for cheapest cell (via get_flights) ---")
    query = create_query(
        flights=[
            FlightQuery(date=cheapest.outbound_date, from_airport=FROM_AIRPORT, to_airport=TO_AIRPORT),
            FlightQuery(date=cheapest.return_date, from_airport=TO_AIRPORT, to_airport=FROM_AIRPORT),
        ],
        trip="round-trip",
        passengers=Passengers(adults=ADULTS),
        currency=CURRENCY,
        language=LANGUAGE,
    )
    print(f"Booking URL: {query.url()}")

    try:
        result = get_flights(query)
        print(f"Flights found for this date pair: {len(result)}")
        for f in list(result)[:5]:
            stops = len(f.flights) - 1
            airlines = ", ".join(f.airlines)
            legs = " / ".join(
                f"{leg.from_airport.code}->{leg.to_airport.code} {leg.duration}min"
                for leg in f.flights
            )
            print(f"  ${f.price} {airlines} stops={stops} {legs}")
    except Exception as exc:
        print(f"get_flights() detail lookup failed (non-fatal for this spike): {exc!r}")

    print()
    print("=== RAW RESPONSE STRUCTURE (first 3 entries as dataclasses) ===")
    for e in calendar.entries[:3]:
        print(e)


if __name__ == "__main__":
    main()
