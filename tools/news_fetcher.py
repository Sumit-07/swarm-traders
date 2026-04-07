"""News fetcher — uses Gemini with Google Search grounding.

Instead of a separate news API, we ask Gemini to search the web for
the latest Indian market news and return structured results. This
gives us real-time headlines with zero extra API keys.
"""

import json
import re

from config import GOOGLE_API_KEY
from tools.logger import get_agent_logger

logger = get_agent_logger("news_fetcher")

MARKET_NEWS_PROMPT = """\
You are a financial news researcher for an Indian stock market trading system.
Your job is to search for and compile the most recent market-moving news.

Current time: {current_time} IST, Date: {current_date}

Search the web and compile the latest news relevant to Indian stock markets.
Cover ALL of the following categories — skip a category only if there is
genuinely nothing relevant in the last 12 hours:

1. **Indian Market News**
   - Nifty 50 / Sensex movement and outlook
   - Sector-specific moves (banking, IT, pharma, metals, auto)
   - FII/DII flow data for today or yesterday
   - RBI policy, government announcements affecting markets
   - Major corporate earnings, results, or guidance updates

2. **Global Cues**
   - US markets (S&P 500, Nasdaq, Dow) — last close and futures
   - Asian markets (Nikkei, Hang Seng, SGX Nifty) — current status
   - Crude oil price and direction
   - US Dollar index (DXY) and USD/INR movement
   - Any geopolitical events affecting global risk appetite

3. **Risk Events (next 24 hours)**
   - Scheduled economic data releases (India or US)
   - Central bank meetings or speeches (RBI, Fed)
   - Major earnings announcements today/tomorrow
   - Options expiry or settlement dates
   - Any political or geopolitical developments

4. **Market Sentiment Indicators**
   - India VIX level and direction
   - Put-call ratio if available
   - Any unusual volume or block deals

IMPORTANT RULES:
- Only include FACTUAL, VERIFIED information from your search results
- Include the source name for each piece of news
- If you cannot find information for a category, say "No significant updates"
- Do NOT make predictions or give trading advice
- Focus on the last 12-24 hours of news
- For numbers (index levels, percentages), be precise

Respond in this exact JSON format:
{{
  "headlines": [
    {{"title": "headline text", "source": "source name", "category": "DOMESTIC|GLOBAL|EARNINGS|POLICY|RISK", "impact": "POSITIVE|NEGATIVE|NEUTRAL"}},
  ],
  "global_cues": {{
    "us_markets": "S&P/Nasdaq last close summary",
    "asian_markets": "current Asian market status",
    "crude_oil": "price and direction",
    "dxy_usdinr": "dollar index and rupee status"
  }},
  "risk_events_next_24h": [
    "event description with time if known"
  ],
  "fii_dii_flow": "FII/DII data if available, else 'Not available'",
  "india_vix": "VIX level and direction if available",
  "overall_sentiment": "BULLISH | BEARISH | NEUTRAL | MIXED",
  "one_line_summary": "Single sentence capturing the most important market theme right now"
}}
"""


def fetch_market_news(current_time: str, current_date: str) -> dict:
    """Fetch latest market news using Gemini with Google Search grounding.

    Returns structured news dict or error fallback.
    """
    if not GOOGLE_API_KEY:
        logger.warning("GOOGLE_API_KEY not set — news fetch disabled")
        return _empty_result("API key not configured")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GOOGLE_API_KEY)

        prompt = MARKET_NEWS_PROMPT.format(
            current_time=current_time,
            current_date=current_date,
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )

        raw_text = response.text
        logger.info(f"News fetch complete — response length: {len(raw_text)}")

        return _parse_news_response(raw_text)

    except ImportError:
        logger.error("google-genai package not installed — pip install google-genai")
        return _empty_result("google-genai not installed")
    except Exception as e:
        logger.error(f"News fetch failed: {e}")
        return _empty_result(str(e))


def _parse_news_response(text: str) -> dict:
    """Parse Gemini's JSON response, handling common LLM quirks."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip())
    stripped = re.sub(r"\s*```\s*$", "", stripped)
    if stripped != text.strip():
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", stripped)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # Truncated JSON — try to salvage what we can
    brace_match = re.search(r"\{[\s\S]*", stripped)
    if brace_match:
        partial = brace_match.group(0)
        result = _salvage_truncated_json(partial)
        if result:
            logger.warning("News JSON was truncated — salvaged partial result")
            return result

    logger.warning(f"Failed to parse news JSON: {text[:300]}")
    return {"_raw": text, "_parse_error": True,
            "overall_sentiment": "UNKNOWN", "headlines": []}


def _salvage_truncated_json(partial: str) -> dict | None:
    """Try to extract useful fields from truncated JSON.

    When Gemini's response is cut off, we may still have complete
    headlines and global_cues even if the rest is missing.
    """
    result = {}

    # Try to extract headlines array
    headlines_match = re.search(
        r'"headlines"\s*:\s*\[([\s\S]*?)\]', partial
    )
    if headlines_match:
        try:
            result["headlines"] = json.loads("[" + headlines_match.group(1) + "]")
        except json.JSONDecodeError:
            # Try salvaging individual headline objects
            items = re.findall(r'\{[^{}]+\}', headlines_match.group(1))
            headlines = []
            for item in items:
                try:
                    headlines.append(json.loads(item))
                except json.JSONDecodeError:
                    continue
            if headlines:
                result["headlines"] = headlines

    # Try to extract other top-level fields
    for field in ("overall_sentiment", "one_line_summary", "fii_dii_flow", "india_vix"):
        match = re.search(rf'"{field}"\s*:\s*"([^"]*)"', partial)
        if match:
            result[field] = match.group(1)

    # Try to extract global_cues object
    cues_match = re.search(r'"global_cues"\s*:\s*\{([^{}]*)\}', partial)
    if cues_match:
        try:
            result["global_cues"] = json.loads("{" + cues_match.group(1) + "}")
        except json.JSONDecodeError:
            pass

    if result:
        result.setdefault("headlines", [])
        result.setdefault("overall_sentiment", "UNKNOWN")
        return result

    return None


def _empty_result(reason: str) -> dict:
    """Return a safe empty news result."""
    return {
        "headlines": [],
        "global_cues": {},
        "risk_events_next_24h": [],
        "fii_dii_flow": "Not available",
        "india_vix": "Not available",
        "overall_sentiment": "UNKNOWN",
        "one_line_summary": f"News unavailable: {reason}",
    }
