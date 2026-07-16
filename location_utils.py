"""Normalize and validate scrape location strings (Realtor.com)."""
from __future__ import annotations

import re

_US_STATES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


_CITY_ONLY: dict[str, str] = {
    "new york": "New York, NY",
    "los angeles": "Los Angeles, CA",
    "chicago": "Chicago, IL",
    "houston": "Houston, TX",
    "phoenix": "Phoenix, AZ",
    "philadelphia": "Philadelphia, PA",
    "san antonio": "San Antonio, TX",
    "san diego": "San Diego, CA",
    "dallas": "Dallas, TX",
    "austin": "Austin, TX",
    "miami": "Miami, FL",
    "brooklyn": "Brooklyn, NY",
    "manhattan": "Manhattan, NY",
}


def normalize_realtor_location(raw: str) -> str:
    """Turn common user input into City, ST or a 5-digit ZIP."""
    s = (raw or "").strip()
    s = s.replace("\u2019", "'").replace("，", ",")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",+", ",", s)
    s = re.sub(r"\s*,\s*", ", ", s)

    alias = _CITY_ONLY.get(s.lower())
    if alias:
        return alias

    zip_only = re.fullmatch(r"(\d{5})(?:-\d{4})?", s)
    if zip_only:
        return zip_only.group(1)

    if "," in s:
        city, rest = s.split(",", 1)
        city = city.strip()
        rest = re.sub(r",?\s*(USA|U\.S\.A\.|US|United States)\.?\s*$", "", rest, flags=re.I).strip()
        if not rest:
            return s
        if re.fullmatch(r"[A-Za-z]{2}", rest):
            return f"{city}, {rest.upper()}"
        abbr = _US_STATES.get(rest.lower())
        if abbr:
            return f"{city}, {abbr}"
        return f"{city}, {rest}"

    m = re.match(r"^(.+?)\s+([A-Za-z]{2})$", s)
    if m:
        return f"{m.group(1).strip()}, {m.group(2).upper()}"

    return s


def is_valid_realtor_location(raw: str) -> bool:
    loc = normalize_realtor_location(raw)
    if re.fullmatch(r"\d{5}", loc):
        return True
    return bool(re.search(r",\s*[A-Za-z]{2}\b", loc))
