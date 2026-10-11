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

PROMPT_VERSION = 3  # bump when the instructions change, so every site is re-read once

PROMPT = """You extract restaurant DEALS for a deals app in Edmonton, Alberta. Today is {today}.

Restaurant: {name}
Website: {url}

Below is text pulled from the restaurant's website (several pages, each starting with "### PAGE: <url>").
List every current deal a customer can use: daily/weekly specials (e.g. "Wing Wednesday 59¢ wings"),
happy hours, BOGO, kids-eat-free, discounts, limited-time offers with a price or saving.

Do NOT include: normal menu items/prices or regular combos/bundles with no stated saving, gift-card promos,
catering, events with no food/drink saving, contests, job postings, loyalty/rewards sign-up bonuses, points
programs, birthday freebies, app-download or first-order offers, membership/student/CAA/first-responder
discounts, delivery-fee offers, non-food activities, or deals that have clearly expired.
If there are no deals, return {{"deals": []}}.

ACCURACY RULES (very important — people will drive to the restaurant based on this):
- Only state a price or saving that is written in the text. NEVER infer one. If a day has a featured item
  but no price/saving is written (e.g. "Tuesday: Wings"), set "price": null and title it as a feature
  (e.g. "Tuesday Wing Feature"), never "Half-Price" or "Cheap".
- Only use days and times that are written in the text. If a general note says e.g. "features after 2PM",
  apply it. Don't invent times.
- If a limited-time offer mentions a holiday, long weekend or season, set valid_until to its last day.

Return ONLY JSON in exactly this shape:
{{"deals": [{{
  "title": "short catchy name, max 60 chars, e.g. '59¢ Wings'",
  "description": "one customer-facing sentence with the conditions (min purchase, dine-in only, with drink, etc.). Write it like a menu blurb. Never mention 'the text', 'the website', 'the page', or what isn't stated",
  "price": "e.g. '59¢/wing', '$10', '50% off', 'BOGO' or null",
  "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]  (only the days it runs; all 7 if every day),
  "start": "HH:MM 24h or null", "end": "HH:MM 24h or null (null = until close)",
  "categories": 1-3 lowercase tags from: wings, burgers, pizza, tacos, sushi, pasta, steak, breakfast, brunch, chicken, seafood, appetizers, dessert, drinks, happy hour, kids, family, coffee, lunch, late night, vegetarian,
  "valid_until": "YYYY-MM-DD if a limited-time end date is stated, else null",
  "dine_in_only": true/false,
  "applies_to": "all" or a list of specific location names/addresses if only some locations offer it,
  "source_url": "the PAGE url the deal came from"
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


def extract_deals(name, url, text):
    prompt = PROMPT.format(today=dt.date.today().isoformat(), name=name, url=url, text=text)
    call = _call_openai_compatible if os.environ.get("LLM_PROVIDER", "anthropic") != "anthropic" else _call_anthropic
    for attempt in range(3):
        try:
            out = call(prompt)
            m = re.search(r"\{.*\}", out, re.S)
            return json.loads(m.group(0)).get("deals", []) if m else []
        except LLMError as e:
            if getattr(e, "status", 0) in (429, 500, 502, 503, 529):
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except json.JSONDecodeError:
            return []
    return []
