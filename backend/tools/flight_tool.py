"""
Flight fares: Ignav Flight Prices & Booking Links API.

search_flights is a pure SEARCH tool — read-only fare quotes.

get_booking_link is the one exception to "no write/action tools" in this
codebase, and it's a narrow one: given the ignav_id of an itinerary the CEO
has already seen and explicitly confirmed, it fetches a deep link to that
fare on the airline/OTA's OWN site. It does not create a reservation, does
not touch payment details, and does not spend any money — the CEO clicks
the link and completes the actual purchase himself, on the airline's own
checkout, with his own payment method. Our system's involvement ends at
handing over the correct link.

This is the human-approval gate CLAUDE.md requires, made structural rather
than conversational: get_booking_link must ONLY ever be called by the
`/booking-link` endpoint in main.py, itself only reachable by the CEO
clicking "Confirm" on an approval card in the UI — never by the LLM tool
loop in orchestrator.py. The orchestrator can propose a booking (show an
approval card); it can never trigger this call on its own. Don't wire this
function into orchestrator.TOOL_DISPATCH — that would let chat text alone
(including injected/hallucinated content) trigger a real external call
without a genuine UI click behind it.

Was originally built against Amadeus for Developers' self-service API, which
Amadeus decommissioned on 2026-07-17 (existing keys disabled, portal now
Enterprise-only — see https://developers.amadeus.com/). Switched to Ignav
(https://ignav.com) instead: free account, no card required, no per-seat
enterprise sales process — matches the same "no-cost, no-API-account-hassle"
preference that shaped the social media tooling. Docs: https://ignav.com/docs

Auth: a single API key in the `X-Api-Key` header (get one free at
https://ignav.com/signup — no card required, 1,000 free requests then
pay-as-you-go, no monthly minimum).
"""
import base64
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from config import config

BASE_URL = "https://ignav.com/api/fares"

# "market": "AE" does flip Ignav's response currency to AED (confirmed
# empirically), but it ALSO localizes display text — provider_name/carrier
# can come back in Arabic script for the same airline — and there's no
# separate locale/lang param to decouple the two (tried locale/lang/language,
# all rejected with 400). Rather than show an English-speaking CEO Arabic
# airline names, we keep market at the English default and convert USD to
# AED ourselves. This isn't an approximation: AED has been hard-pegged to
# USD at this exact rate since 1997 (unlike a floating-rate currency), so
# the converted figure is the real AED price, not an estimate.
AED_PER_USD = 3.6725


def _to_aed(amount, currency):
    if amount is None or (currency or "").upper() != "USD":
        return amount, currency
    return round(amount * AED_PER_USD, 2), "AED"

# Some Ignav-returned links carry ad-network tracking params (gclid etc.)
# meant to be followed only via the exact ad-click chain they came from.
# Opened directly (e.g. clicked from this app), airline bot-defense
# (Akamai and similar) can reject them outright with a "restricted" page —
# seen in practice with Emirates and Qatar Airways links. Stripping these
# doesn't guarantee the link works, but it removes the strongest signal
# that flags the request as non-organic traffic.
TRACKING_PARAMS = {
    "gclid", "gclsrc", "msclkid", "fbclid",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}

# market="AE" also localizes some text fields (e.g. provider_name can come
# back as Arabic script for the same airline) — confirmed empirically. So
# trust and homepage-derivation below are both domain-based, off the actual
# url, never off provider_name: it's language-independent (an Arabic-named
# legit OTA won't get wrongly dropped) AND a stronger check than a display
# name anyway, since a display string can claim to be anything regardless of
# where the link actually goes.
TRUSTED_OTA_DOMAINS = {
    "kiwi.com", "expedia.com", "booking.com", "kayak.com", "skyscanner.net",
    "skyscanner.com", "priceline.com", "trip.com", "orbitz.com",
    "travelocity.com", "hopper.com", "momondo.com", "cheapoair.com",
    "lastminute.com",
}


def _domain_of(url: str) -> str:
    host = (urlsplit(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _clean_url(url: str) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _root_url(url: str) -> str:
    """Scheme + domain only, no path/query — used as a stable homepage
    fallback alongside a deep link that might be a fragile ad-tracking
    redirect or get bot-blocked. Derived from the real returned URL rather
    than a hardcoded per-airline table, so it needs no maintenance and can't
    drift out of date."""
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


# Ignav aggregates booking links from many resellers without vetting their
# reputation, and some are actively bad actors — e.g. martigo.* has widely
# reported bait pricing (quotes a low fare, then demands more after booking)
# and, via a partner, unauthorized card charges. Worse, a "cheapest first"
# sort actively promotes exactly that kind of reseller, since bait pricing
# means they undercut everyone up front. So: never show a raw reseller link
# to the CEO — the airline's own site is always trusted; a third-party OTA
# is only shown if its URL's domain is on TRUSTED_OTA_DOMAINS above (a large,
# established players allowlist). Extend that set deliberately, not by
# removing the filter.
def _is_trusted(option: dict) -> bool:
    if option.get("provider_type") == "airline":
        return True
    domain = _domain_of(option.get("url"))
    return any(domain == d or domain.endswith("." + d) for d in TRUSTED_OTA_DOMAINS)


def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str = "",
    adults: int = 1,
) -> list[dict]:
    """Search live flight fares. origin/destination are IATA airport codes
    (e.g. DXB, LHR). depart_date/return_date are YYYY-MM-DD; return_date
    is optional (omit for one-way). Returns the cheapest offers first, each
    tagged with an ignav_id — pass that (unchanged) to get_booking_link if
    the CEO confirms he wants to book that exact fare."""
    if not config.IGNAV_API_KEY:
        return _mock_flights(origin, destination, depart_date, return_date)

    endpoint = "round-trip" if return_date else "one-way"
    body = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "departure_date": depart_date,
        "adults": adults,
    }
    if return_date:
        body["return_date"] = return_date

    resp = requests.post(
        f"{BASE_URL}/{endpoint}",
        headers={"X-Api-Key": config.IGNAV_API_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    itineraries = resp.json().get("itineraries", [])
    return sorted((_summarize_itinerary(i) for i in itineraries), key=lambda i: i["price_total"])


def _build_option(provider_name, provider_type, fare_name, price_total, currency, raw_url) -> dict:
    price_total, currency = _to_aed(price_total, currency)
    entry = {
        "provider_name": provider_name,
        "provider_type": provider_type,
        "fare_name": fare_name,
        "price_total": price_total,
        "currency": currency,
        "url": _clean_url(raw_url),
    }
    if provider_type == "airline":
        homepage = _root_url(entry["url"])
        if homepage and homepage != entry["url"]:
            entry["homepage_url"] = homepage
    return entry


# --- Google Flights `tfs=` deep link -----------------------------------
# Google Flights encodes an entire search (airports, dates, airline filter,
# departure time window, cabin, trip type) as a protobuf, then base64url-
# encodes it into the `tfs` query param. There is no official spec for
# this — Google has never published one — but it's been reverse-engineered
# and documented publicly (e.g. https://gist.github.com/MomoDeve/
# a18053dea84dd28e320b8b2c489540eb), and several open-source projects build
# on it the same way this does. Building this ourselves is what lets a
# generated (always-reachable, non-ad-tracked) Google Flights link ALSO
# filter down to one specific flight — airline + a one-hour departure
# window + cabin — rather than just the route and dates. It cannot select
# one exact flight the way Ignav's fare-specific deep link tries to (Google
# doesn't expose a field for that), but airline + time window narrows
# results to the CEO's flight or a very short list next to it.
#
# Being unofficial cuts both ways: if Google changes this internal format,
# link generation silently stops narrowing results (falls back to the
# plain q= search below) rather than breaking outright — every builder
# function here is used inside a try/except for exactly that reason. Don't
# let a change here go unnoticed: if narrowed links stop looking narrowed
# in testing, re-check the gist above before assuming the code is at fault.
_CABIN_CODES = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}


def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _pb_field(field_number: int, wire_type: int, payload: bytes) -> bytes:
    return _pb_varint((field_number << 3) | wire_type) + payload


def _pb_varint_field(field_number: int, value: int) -> bytes:
    return _pb_field(field_number, 0, _pb_varint(value))


def _pb_bytes_field(field_number: int, data: bytes) -> bytes:
    return _pb_field(field_number, 2, _pb_varint(len(data)) + data)


def _pb_string_field(field_number: int, s: str) -> bytes:
    return _pb_bytes_field(field_number, s.encode("utf-8"))


def _pb_place(iata_code: str) -> bytes:
    return _pb_varint_field(1, 1) + _pb_string_field(2, iata_code.upper())  # entity_type=1: airport IATA


def _pb_leg(origin: str, destination: str, date: str, hint: dict | None) -> bytes:
    parts = _pb_string_field(2, date)  # departure_date
    hint = hint or {}
    if hint.get("stops") is not None:
        parts += _pb_varint_field(5, hint["stops"])  # max_stops
    for carrier in hint.get("carriers") or []:
        parts += _pb_string_field(6, carrier)  # include_airlines (repeated: "any of these" semantics)
    if hint.get("dep_hour") is not None:
        # +/-1 hour, not the exact hour: Ignav and Google Flights are two
        # different data snapshots and won't always agree to the minute —
        # an exact-hour window risks "no options matching your filters" on
        # a real flight (reproduced this with a synthetic test case) if the
        # two sources disagree across an hour boundary. Still narrow enough
        # to isolate one flight from a route/date search in practice.
        parts += _pb_varint_field(8, max(hint["dep_hour"] - 1, 0))  # departure_start_hour
        parts += _pb_varint_field(9, min(hint["dep_hour"] + 1, 23))  # departure_end_hour_inclusive
    parts += _pb_bytes_field(13, _pb_place(origin))
    parts += _pb_bytes_field(14, _pb_place(destination))
    return parts


def _google_flights_tfs_url(
    origin: str, destination: str, depart_date: str, return_date: str,
    cabin_class: str | None, outbound: dict | None, inbound: dict | None,
) -> str | None:
    """Best-effort filtered Google Flights link. Returns None on any
    unexpected shape (caller falls back to the plain q= search link) rather
    than risk handing the CEO a malformed URL."""
    try:
        legs = [_pb_leg(origin, destination, depart_date, outbound)]
        if return_date:
            legs.append(_pb_leg(destination, origin, return_date, inbound))  # reversed, per the spec
        body = b"".join(_pb_bytes_field(3, leg) for leg in legs)
        body += _pb_varint_field(9, _CABIN_CODES.get((cabin_class or "economy").lower(), 1))
        body += _pb_varint_field(19, 1 if return_date else 2)  # trip_type: 1=round-trip, 2=one-way
        tfs = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return f"https://www.google.com/travel/flights?tfs={tfs}"
    except Exception:
        return None


# --- Kayak `fs=` filter param --------------------------------------------
# Kayak's results page takes a real filter query param, `fs`, with
# semicolon-separated conditions — reverse-engineered the same way as
# Google's tfs= (see above): built a plain Kayak search, applied filters
# through Kayak's own UI, and read back the resulting URL. Verified live
# 2026-09-23 on a real DXB->JFK case: `airlines=EK,VS` (comma-separated
# IATA codes, no leading "-") narrowed results to only those carriers,
# `stops=~1` narrowed to itineraries with at most 1 stop, and
# `takeoff=0800,1000` (24h HHMM, outbound leg only) narrowed a 7-result
# list down to exactly 1 matching flight — same kind of narrowing as the
# Google Flights link, and same caveat: unofficial, can change, wrapped in
# try/except so a broken format falls back to the plain route/date URL
# rather than a malformed one. (Note: `airlines=-EK,...` with a leading
# "-" means EXCLUDE those carriers instead — confirmed by accident while
# reverse-engineering this; don't drop the comma-join without the check
# for a stray "-" ever creeping in.)
def _kayak_fs_param(outbound: dict | None, inbound: dict | None) -> str | None:
    try:
        carriers = list(dict.fromkeys(
            (outbound or {}).get("carriers", []) + (inbound or {}).get("carriers", [])
        ))
        stop_counts = [h["stops"] for h in (outbound, inbound) if h and h.get("stops") is not None]
        dep_hour = (outbound or {}).get("dep_hour")

        parts = []
        if carriers:
            parts.append("airlines=" + ",".join(carriers))
        if stop_counts:
            parts.append(f"stops=~{max(stop_counts)}")
        if dep_hour is not None:
            start = max(dep_hour - 1, 0)
            end = min(dep_hour + 1, 23)
            parts.append(f"takeoff={start:02d}00,{end:02d}00")
        return ";".join(parts) if parts else None
    except Exception:
        return None


def _generated_search_links(
    origin: str, destination: str, depart_date: str, return_date: str,
    cabin_class: str | None = None, outbound: dict | None = None, inbound: dict | None = None,
) -> list[dict]:
    """Search-PAGE links we build ourselves for a few major, always-public
    metasearch sites — NOT itinerary-specific deep links like Ignav's.
    These carry no ad-click session, so they don't hit the same bot-defense
    wall airline deep links do (see the module docstring): it's the same
    kind of URL anyone bookmarking or blogging a flight search would use,
    not a private redirect meant for one specific ad-click chain.

    Trade-off, and it's a real one: these land the CEO on a live search for
    the same route/dates, not a price-verified match to what Ignav quoted —
    the site's own inventory may show a different number. Callers should
    label these as "search" links, distinct from the priced options above.
    Google Flights, Kayak, and Momondo all narrow by airline + departure
    time + stops (+ cabin for Google) when outbound/inbound hints are given
    (see _google_flights_tfs_url / _kayak_fs_param) — Kayak and Momondo
    share the same fs= syntax since they're the same underlying platform.

    URL formats here are the commonly-used patterns for each site, not an
    officially documented/stable API — they can break if a site changes its
    URL scheme. Re-verify with a real browser if links start looking wrong.
    """
    if not (origin and destination and depart_date):
        return []
    o, d = origin.upper(), destination.upper()

    google_url = _google_flights_tfs_url(o, d, depart_date, return_date, cabin_class, outbound, inbound)
    if not google_url:
        query = f"flights from {o} to {d} on {depart_date}" + (f" through {return_date}" if return_date else "")
        google_url = f"https://www.google.com/travel/flights?q={quote(query)}"

    fs = _kayak_fs_param(outbound, inbound)
    fs_suffix = f"?fs={quote(fs, safe='')}" if fs else ""

    kayak_url = f"https://www.kayak.com/flights/{o}-{d}/{depart_date}" + (f"/{return_date}" if return_date else "") + fs_suffix
    # Momondo runs on the same platform as Kayak (both Booking Holdings) and
    # takes the identical fs= filter syntax — confirmed live 2026-09-23.
    # Replaced Skyscanner here: Skyscanner's own bot-defense served a
    # persistent "confirm you're not a robot" challenge that a real human
    # user could never get past (not just our automated test browser, as
    # first suspected — confirmed by the CEO hitting it directly). Momondo
    # is already on TRUSTED_OTA_DOMAINS and loaded clean with no challenge
    # in the same test. Don't add Skyscanner back without re-verifying live
    # that the captcha wall is gone.
    momondo_url = f"https://www.momondo.com/flight-search/{o}-{d}/{depart_date}" + (f"/{return_date}" if return_date else "") + fs_suffix

    links = [
        {"provider_name": "Google Flights", "url": google_url},
        {"provider_name": "Momondo", "url": momondo_url},
        {"provider_name": "Kayak", "url": kayak_url},
    ]
    return links


def _finalize_options(
    options: list[dict], ignav_id: str, origin: str = "", destination: str = "", depart_date: str = "", return_date: str = "",
    cabin_class: str | None = None, outbound: dict | None = None, inbound: dict | None = None,
) -> dict:
    # Drop untrusted resellers before anything else touches this list — see
    # TRUSTED_OTA_DOMAINS above. Then sort what's left cheapest first. Keep
    # the raw (pre-filter) count so callers can tell "nothing came back"
    # apart from "results came back but we don't trust any of the sellers" —
    # different messages, and the second one shouldn't sound like the fare
    # expired.
    raw_count = len(options)
    trusted = [o for o in options if _is_trusted(o)]
    trusted.sort(key=lambda o: o["price_total"] if o["price_total"] is not None else float("inf"))
    return {
        "ignav_id": ignav_id,
        "options": trusted,
        "raw_count": raw_count,
        "search_links": _generated_search_links(origin, destination, depart_date, return_date, cabin_class, outbound, inbound),
    }


def get_booking_link(
    ignav_id: str, origin: str = "", destination: str = "", depart_date: str = "", return_date: str = "",
    cabin_class: str | None = None, outbound: dict | None = None, inbound: dict | None = None,
) -> dict:
    """Fetch real booking links for a specific, already-quoted itinerary.
    Only ever call this after the CEO has clicked Confirm in the UI on that
    exact itinerary — see the module docstring. origin/destination/dates
    are used to build the generated search_links (see above); cabin_class/
    outbound/inbound (carrier+dep_hour+stops per leg) let that Google
    Flights link narrow down to this specific flight. Pass values recorded
    from the real search that produced this ignav_id, not client-supplied
    text — see orchestrator._known_itineraries."""
    if not config.IGNAV_API_KEY:
        return _mock_booking_link(ignav_id, origin, destination, depart_date, return_date, cabin_class, outbound, inbound)

    resp = requests.post(
        f"{BASE_URL}/booking-links",
        headers={"X-Api-Key": config.IGNAV_API_KEY, "Content-Type": "application/json"},
        json={"ignav_id": ignav_id},
        timeout=20,
    )
    if resp.status_code == 404:
        # Ignav 404s here for an invalid OR expired ignav_id — itineraries
        # are only bookable for a limited window after search. Don't let the
        # raw HTTPError string ("404 Client Error: Not Found for url: ...")
        # reach the CEO — that happened once already and is meaningless to
        # him. Treat it exactly like "zero trusted options": raw_count=0
        # naturally produces the "may have expired" message in main.py, and
        # search_links (which don't depend on Ignav at all) still get
        # attached, so there's still a reliable path forward.
        return _finalize_options([], ignav_id, origin, destination, depart_date, return_date, cabin_class, outbound, inbound)
    resp.raise_for_status()
    data = resp.json()
    options = [
        _build_option(
            link.get("provider_name"),
            link.get("provider_type"),
            link.get("fare_name"),
            link.get("price", {}).get("amount"),
            link.get("price", {}).get("currency"),
            link.get("url"),
        )
        for option in data.get("booking_options", [])
        for link in option.get("links", [])
    ]
    return _finalize_options(options, ignav_id, origin, destination, depart_date, return_date, cabin_class, outbound, inbound)


def _summarize_leg(leg: dict) -> dict:
    return {
        "carrier": leg.get("carrier"),
        "duration_minutes": leg.get("duration_minutes"),
        "stops": max(len(leg.get("segments", [])) - 1, 0),
        "segments": [
            {
                "carrier": s.get("marketing_carrier_code"),
                "flight_number": s.get("flight_number"),
                "from": s.get("departure_airport"),
                "depart_at": s.get("departure_time_local") or s.get("departure_time_utc"),
                "to": s.get("arrival_airport"),
                "arrive_at": s.get("arrival_time_local") or s.get("arrival_time_utc"),
            }
            for s in leg.get("segments", [])
        ],
    }


def _summarize_itinerary(itin: dict) -> dict:
    price = itin.get("price", {})
    price_total, currency = _to_aed(float(price.get("amount", 0)), price.get("currency"))
    legs = {"outbound": _summarize_leg(itin.get("outbound", {}))}
    if "inbound" in itin:
        legs["inbound"] = _summarize_leg(itin["inbound"])
    return {
        "ignav_id": itin.get("ignav_id"),
        "price_total": price_total,
        "currency": currency,
        "cabin_class": itin.get("cabin_class"),
        **legs,
    }


def _mock_flights(origin: str, destination: str, depart_date: str, return_date: str) -> list[dict]:
    """Used when IGNAV_API_KEY isn't set yet, so the rest of the system can
    still be tested end to end."""

    def leg(carrier_code, flight_no, from_, to, date, dep_time, arr_time):
        return {
            "carrier": "Emirates",
            "duration_minutes": 440,
            "stops": 0,
            "segments": [
                {
                    "carrier": carrier_code,
                    "flight_number": flight_no,
                    "from": from_,
                    "depart_at": f"{date}T{dep_time}:00",
                    "to": to,
                    "arrive_at": f"{date}T{arr_time}:00",
                }
            ],
        }

    def itinerary(ignav_id, price):
        result = {
            "ignav_id": ignav_id,
            "price_total": price,
            "currency": "AED",
            "cabin_class": "economy",
            "outbound": leg("EK", "1", origin.upper(), destination.upper(), depart_date, "08:30", "12:50"),
        }
        if return_date:
            result["inbound"] = leg("EK", "2", destination.upper(), origin.upper(), return_date, "14:10", "23:00")
        return result

    return [
        itinerary("mock-cheapest", 2340.0),
        itinerary("mock-mid", 2695.0),
        itinerary("mock-flex", 3120.0),
    ]


def _mock_booking_link(
    ignav_id: str, origin: str = "", destination: str = "", depart_date: str = "", return_date: str = "",
    cabin_class: str | None = None, outbound: dict | None = None, inbound: dict | None = None,
) -> dict:
    """Mocks a few sellers for the same itinerary, including one untrusted
    reseller with the cheapest headline price and a tracking-param-laden
    airline deep link — so mock mode exercises the same trust filter,
    tracking-param stripping, and homepage-fallback derivation as the real
    path, via the same _build_option/_finalize_options helpers."""
    options = [
        _build_option(
            "SketchyFlightsXYZ", "ota", "Economy Special", 2199.0, "AED",
            "https://sketchyflightsxyz.example/booking (mock — not on TRUSTED_OTA_DOMAINS, should be filtered out)",
        ),
        _build_option(
            "Kiwi.com", "ota", "Economy Special", 2298.0, "AED",
            "https://www.kiwi.com/en/search/ (mock — no IGNAV_API_KEY set)",
        ),
        _build_option(
            "Emirates", "airline", "Economy Special", 2340.0, "AED",
            "https://www.emirates.com/ae/english/book/?gclid=mock-tracking-param (mock — no IGNAV_API_KEY set)",
        ),
        _build_option(
            "Expedia", "ota", "Economy Special", 2365.0, "AED",
            "https://www.expedia.com/Flights-Search (mock — no IGNAV_API_KEY set)",
        ),
    ]
    return _finalize_options(options, ignav_id, origin, destination, depart_date, return_date, cabin_class, outbound, inbound)
