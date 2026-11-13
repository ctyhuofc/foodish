"""Turns a restaurant's website text into structured deals using a small, cheap AI model.

Pick a provider with environment variables:
  LLM_PROVIDER=anthropic (default)  LLM_API_KEY=...  LLM_MODEL=claude-haiku-5-5
  LLM_PROVIDER=openai-compatible    LLM_API_KEY=...  LLM_MODEL=<model>  LLM_BASE_URL=https://api.x.ai/v1   (xAI / Grok API, OpenAI, etc.)
"""
import datetime as dt
import json
import os
import re
import time

import requests

PROMPT_VERSION = 7  # bump when the instructions change, so every site is re-read once

PROMPT = """You extract restaurant DEALS for a deals app in {city}, {region}. Today is {today}.

Restaurant: {name}
Website: {url}

Below is text pulled from the restaurant's website (several pages, each starting with "### PAGE: <url>").
List every current deal a customer can use: daily/weekly specials (e.g. "Wing Wednesday 59¢ wings"),
happy hours, BOGO, kids-eat-free, discounts, limited-time offers with a price or saving.

Do NOT include: normal menu items/prices or regular combos/bundles with no stated saving, gift-card promos,
catering, events with no food/drink saving, contests, job postings, loyalty/rewards sign-up bonuses, points
programs, birthday freebies, app-download or first-order offers, membership/student/CAA/first-responder
discounts, delivery-fee offers, non-food activities, or deals that have clearly expired.
Regular menu or coupon-menu pricing (e.g. a chain's list of pizza combos at everyday prices) is NOT a deal unless
the text presents it as a special, a daily/weekly feature, or a limited-time offer.
This app is for diners in the {city} area. If the text limits a deal to other provinces, other cities, or
named locations outside the {city} area, leave it out (e.g. "at our Brampton or Milton locations" is not a
Toronto deal). Deals with no stated place are fine.
Leave out offers for the United States or other countries (US-only promos, prices in US dollars, US app deals):
only deals a diner can use in Canada count.
Leave out deals that depend on an event rather than the day of the week, such as game-day or "during the
Oilers game" specials, because the app would show them every day.
Return at most 6 deals per restaurant: the ones a diner would most want to know about.
If there are no deals, return {{"deals": []}}.

ACCURACY RULES (very important — people will drive to the restaurant based on this):
- Only state a price or saving that is written in the text. NEVER infer one. If a day has a featured item
  but no price/saving is written (e.g. "Tuesday: Wings"), set "price": null and title it as a feature
  (e.g. "Tuesday Wing Feature"), never "Half-Price" or "Cheap".
- Only use days and times that are written in the text. If a general note says e.g. "features after 2PM",
  apply it. Don't invent times.
- If a limited-time offer mentions a holiday, long weekend or season, set valid_until to its last day.
- If a deal starts on a later date ("starting Nov 30", "from December 1"), set valid_from to that date.
- Every deal needs "quote": the exact words from the text that show it is a deal, copied character for
  character (max 160 chars), including the price or saving if there is one. If you can't quote text that
  shows a special, a feature or a saving, it is not a deal: leave it out.

Return ONLY JSON in exactly this shape:
{{"deals": [{{
  "title": "short name of the deal, max 40 chars, WITHOUT the price and without day names or times (those have their own fields), e.g. 'Wing night', 'Half-price wine bottles', 'Happy hour pints'. Keep a day name only when it is the deal's own name, like 'Taco Tuesday'",
  "description": "one customer-facing sentence with the conditions (min purchase, dine-in only, with drink, etc.). Write it like a menu blurb. Never mention 'the text', 'the website', 'the page', or what isn't stated",
  "price": "the headline price or saving, max 8 chars: '59¢', '$10', '$4.75', '50% off', '$2 off', 'BOGO', 'Free', or null if none is written",
  "price_unit": "what the price buys, max 14 chars, e.g. 'per wing', 'pints', 'per taco', 'wine bottles', 'kids meal', or null",
  "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]  (only the days it runs; all 7 if every day),
  "start": "HH:MM 24h or null", "end": "HH:MM 24h or null (null = until close)",
  "categories": 1-3 lowercase tags from: wings, burgers, pizza, tacos, sushi, pasta, steak, breakfast, brunch, chicken, seafood, appetizers, dessert, drinks, happy hour, kids, family, coffee, lunch, late night, vegetarian,
  "valid_from": "YYYY-MM-DD if it starts on a later date, else null",
  "valid_until": "YYYY-MM-DD if a limited-time end date is stated, else null",
  "dine_in_only": true/false,
  "applies_to": "all" or a list of specific location names/addresses if only some locations offer it,
  "source_url": "the PAGE url the deal came from",
  "quote": "the exact words from the text this deal is based on"
}}]}}

WEBSITE TEXT:
{text}
"""


class LLMError(Exception):
    """An AI request was refused; the message is the provider's own explanation."""


def _check(r):
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message") if isinstance(err, dict) else str(err)
            kind = err.get("type", "") if isinstance(err, dict) else ""
        except ValueError:
            msg, kind = r.text[:300], ""
        e = LLMError(f"HTTP {r.status_code} {kind}: {msg}".strip())
        e.status = r.status_code
        raise e


def _call_anthropic(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["LLM_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        # Haiku 5.5 rejects temperature/top_p/top_k, so none are sent. Its thinking counts toward
        # max_tokens, so leave generous room for the JSON answer.
        json={"model": os.environ.get("LLM_MODEL") or "claude-haiku-5-5", "max_tokens": 6000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    _check(r)
    return "".join(b.get("text", "") for b in r.json()["content"])


def _call_openai_compatible(prompt):
    base = (os.environ.get("LLM_BASE_URL") or "https://api.x.ai/v1").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}", "content-type": "application/json"},
        json={"model": os.environ["LLM_MODEL"], "temperature": 0, "max_tokens": 2000,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    _check(r)
    return r.json()["choices"][0]["message"]["content"]


def parse_reply(out):
    """The deals list from the AI's reply. An empty list only when the AI really said {"deals": []}.
    A reply we can't read (cut off, not JSON, wrong shape) raises LLMError, so the site's saved deals
    are kept and it is retried tomorrow, instead of being saved as "no deals"."""
    m = re.search(r"\{.*\}", out or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    deals = data.get("deals") if isinstance(data, dict) else None
    if not isinstance(deals, list):
        raise LLMError(f"unreadable AI reply: {(out or '').strip()[:80]!r}")
    return [d for d in deals if isinstance(d, dict)]


def extract_deals(name, url, text, city="Edmonton", region="Alberta"):
    prompt = PROMPT.format(today=dt.date.today().isoformat(), name=name, url=url, text=text, city=city, region=region)
    call = _call_openai_compatible if os.environ.get("LLM_PROVIDER", "anthropic") != "anthropic" else _call_anthropic
    unreadable = None
    for attempt in range(3):
        try:
            return parse_reply(call(prompt))
        except LLMError as e:
            status = getattr(e, "status", 0)
            if status in (429, 500, 502, 503, 529):
                time.sleep(10 * (attempt + 1))
                continue
            if not status and unreadable is None:  # unreadable reply: ask once more
                unreadable = e
                continue
            raise
    raise unreadable or LLMError("AI busy: gave up after 3 tries")
