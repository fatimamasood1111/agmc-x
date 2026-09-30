"""
Hotel search: generated search-page links only.

There is no live hotel-pricing API behind this — Ignav (the flight data
source, see flight_tool.py) is flight-only, and finding a real hotel-pricing
provider with a free/self-service signup (the same bar Ignav had to clear
after Amadeus's self-service portal was decommissioned) is its own research
task, not done here. So this mirrors only the "search_links" half of
flight_tool.py: real, reverse-engineered URL formats for major hotel sites,
each verified live to actually resolve to a real search for the given
location/dates/guests — NOT a priced, trusted-seller, cheapest-first
comparison the way flight_tool.search_flights + get_booking_link is.

Nothing here calls an external API — every link is built by plain string/
URL construction from the CEO's own request. That also means there's no
ignav_id-style session-tracked itinerary to protect and no approval gate
needed: there's no real external price or availability being fetched or
asserted, just search pages handed over for the CEO to browse himself,
exactly like flight_tool.py's "Or search yourself" links. If a real
priced/bookable hotel flow is ever wanted, it needs its own data source
first — don't bolt a booking-link/approval flow onto this file without one.
"""
from urllib.parse import quote


def search_hotels(location: str, checkin: str, checkout: str, adults: int = 1, rooms: int = 1) -> dict:
    """Build search-page links for a hotel stay. location is a free-text
    place name (city, area, or landmark — e.g. "Dubai" or "Dubai Marina").
    checkin/checkout are YYYY-MM-DD. Returns links to major, always-public
    hotel search sites for the CEO to browse and book directly himself —
    this never returns a price or holds/books anything."""
    loc = location.strip()
    q = quote(loc)

    return {
        "location": loc,
        "checkin": checkin,
        "checkout": checkout,
        "adults": adults,
        "rooms": rooms,
        "search_links": [
            {
                "provider_name": "Booking.com",
                # Verified live 2026-09-23: ss=<place text>, checkin/checkout
                # as YYYY-MM-DD, group_adults + no_rooms reflected exactly
                # in the resulting search ("2 adults · 0 children · 1 room").
                "url": f"https://www.booking.com/searchresults.html?ss={q}&checkin={checkin}&checkout={checkout}&group_adults={adults}&no_rooms={rooms}",
            },
            {
                "provider_name": "Expedia",
                # Verified live 2026-09-23: destination=<place text>,
                # startDate/endDate as YYYY-MM-DD, adults reflected exactly
                # ("Dubai, United Arab Emirates" / "Oct 15-Oct 18" / "2
                # travelers, 1 room"). No per-room param confirmed working —
                # don't add one without re-verifying live first.
                "url": f"https://www.expedia.com/Hotel-Search?destination={q}&startDate={checkin}&endDate={checkout}&adults={adults}",
            },
            {
                "provider_name": "Google Hotels",
                # Verified live 2026-09-23: google.com/travel/search?q=hotels
                # in <place>&checkin=...&checkout=... resolves to a real
                # Google Hotels results page for that place and dates.
                "url": f"https://www.google.com/travel/search?q={quote('hotels in ' + loc)}&checkin={checkin}&checkout={checkout}",
            },
        ],
    }
