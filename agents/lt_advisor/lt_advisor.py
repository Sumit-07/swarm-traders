"""
agents/lt_advisor/lt_advisor.py

LT_Advisor agent — long-term investment opportunity advisor.
Runs 3x per trading day + Saturday weekly summary.
Read-only. Never touches the trading pipeline.
Sends alerts via Orchestrator → Telegram.
"""

import json
import logging
from datetime import datetime, date
from typing import Optional

from config.lt_universe import LT_UNIVERSE, VIX_TRANCHE_MAP
from memory.redis_store import RedisStore
from memory.sqlite_store import SQLiteStore
from tools.lt_data import (
    get_nifty_pe,
    get_nifty_52w_high_low,
    get_fii_monthly_flow,
    get_sector_performance_30d,
    get_vix_history_30d,
    get_upcoming_events,
)
from tools.llm import call_llm, render_prompt

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 55
VIX_ALERT_REPEAT_DAYS = 7


class LTAdvisor:

    def __init__(self, redis: RedisStore, db: SQLiteStore):
        self.redis = redis
        self.db = db
        self.today = date.today()
        self.month_str = self.today.strftime("%Y-%m")

    # ── Main entry point ─────────────────────────────────────────────────────

    def run(self, run_type: str) -> None:
        """
        Main entry point. Called by scheduler or /lt_scan command.
        run_type: MORNING | MIDDAY | EOD | WEEKLY | MANUAL
        """
        logger.info("LT_Advisor run starting. type=%s", run_type)
        start_time = datetime.now()

        try:
            if run_type == "WEEKLY":
                self._run_weekly_summary()
                return

            market = self._fetch_market_data()
            if not market:
                logger.warning("Market data unavailable. Skipping run.")
                self._log_run(run_type, None, "SILENCE", "data_unavailable")
                return

            # Midday check: only continue if VIX moved significantly
            if run_type == "MIDDAY":
                morning_vix = self._get_morning_vix()
                if morning_vix and abs(market["vix"] - morning_vix) < 2.0:
                    logger.info("VIX unchanged since morning (< 2pt move). Silent.")
                    self._log_run(run_type, market["vix"], "SILENCE", "vix_unchanged")
                    return

            # EOD check: only continue if Nifty moved significantly today
            if run_type == "EOD":
                if abs(market.get("nifty_change_today_pct", 0)) < 1.0:
                    logger.info("Nifty moved < 1%% today. Silent.")
                    self._log_run(run_type, market["vix"], "SILENCE", "small_move")
                    return

            # Store morning VIX for midday comparison
            if run_type == "MORNING":
                self.redis.set_state(
                    "state:lt_vix_morning",
                    {"vix": market["vix"]},
                    ttl=43200,
                )

            # Quick Python score
            score = self._compute_score(market)
            logger.info("Quick score: %d/100", score)

            if score < SCORE_THRESHOLD:
                logger.info("Score %d < threshold %d. Silent.", score, SCORE_THRESHOLD)
                self._log_run(run_type, market["vix"], "SILENCE", f"score_{score}")
                return

            # Check silence conditions
            silence = self._check_silence_conditions(market)
            if silence:
                logger.info("Silence condition: %s", silence)
                self._log_run(run_type, market["vix"], "SILENCE", silence)
                return

            # Call LLM for opportunity scan
            opportunity = self._run_opportunity_scan(market, run_type)
            if not opportunity or opportunity.get("action") == "SILENCE":
                reason = (
                    opportunity.get("silence_reason", "llm_silence")
                    if opportunity
                    else "llm_error"
                )
                self._log_run(run_type, market["vix"], "SILENCE", reason)
                return

            # Call LLM to draft Telegram message
            telegram_msg = self._draft_telegram_message(opportunity, run_type)
            if not telegram_msg:
                logger.error("Failed to draft Telegram message.")
                return

            # Send via Orchestrator
            self._send_to_orchestrator(opportunity, telegram_msg)
            self._log_run(
                run_type,
                market["vix"],
                "ALERT",
                None,
                instrument=opportunity.get("top_opportunity", {}).get("instrument"),
                score=opportunity.get("top_opportunity", {}).get("score"),
            )

        except Exception as e:
            logger.error("LT_Advisor run failed: %s", e, exc_info=True)

        finally:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                "LT_Advisor run complete. type=%s elapsed=%.1fs", run_type, elapsed
            )

    # ── Market data collection ───────────────────────────────────────────────

    def _fetch_market_data(self) -> Optional[dict]:
        """Collects all market data needed for scoring."""
        try:
            snapshot = self.redis.get_state("data:market_snapshot") or {}
            fii_redis = self.redis.get_state("data:fii_flow") or {}

            # Snapshot keys are nested dicts: {"nifty": {"ltp": ..., "change_pct": ...}, "indiavix": {"ltp": ...}}
            vix_data = snapshot.get("indiavix", snapshot.get("india vix", {}))
            nifty_data = snapshot.get("nifty", snapshot.get("nifty 50", {}))

            vix = float(vix_data.get("ltp", 0)) if isinstance(vix_data, dict) else float(vix_data or 0)
            nifty = float(nifty_data.get("ltp", 0)) if isinstance(nifty_data, dict) else float(nifty_data or 0)

            if vix == 0 or nifty == 0:
                logger.warning("VIX or Nifty is 0 in Redis snapshot.")
                return None

            pe = get_nifty_pe() or 22.0
            hl = get_nifty_52w_high_low()
            vix_hist = get_vix_history_30d()
            sectors = get_sector_performance_30d()
            events = get_upcoming_events(30)

            fii_30day = float(fii_redis.get("fii_30day", 0)) or float(
                get_fii_monthly_flow().get("fii_30day", 0)
            )

            nifty_from_high = (
                ((nifty - hl["high"]) / hl["high"] * 100) if hl["high"] else 0
            )

            return {
                "vix": vix,
                "vix_30d_avg": vix_hist["avg"],
                "vix_trend": vix_hist["trend"],
                "nifty": nifty,
                "nifty_52w_high": hl["high"],
                "nifty_52w_low": hl["low"],
                "nifty_from_high_pct": round(nifty_from_high, 2),
                "nifty_pe": pe,
                "fii_today": float(fii_redis.get("fii_today", 0)),
                "fii_5day": float(fii_redis.get("fii_5day", 0)),
                "fii_30day": fii_30day,
                "dii_5day": float(fii_redis.get("dii_5day", 0)),
                "sector_list": sectors,
                "calendar_events": events,
                "nifty_change_today_pct": float(
                    nifty_data.get("change_pct", 0) if isinstance(nifty_data, dict) else 0
                ),
            }

        except Exception as e:
            logger.error("Market data fetch failed: %s", e)
            return None

    def _compute_score(self, market: dict) -> int:
        return compute_quick_score(
            vix=market["vix"],
            vix_trend=market["vix_trend"],
            nifty_pe=market["nifty_pe"],
            nifty_from_high_pct=market["nifty_from_high_pct"],
            fii_30day_crore=market["fii_30day"],
        )

    # ── Silence checks ───────────────────────────────────────────────────────

    def _check_silence_conditions(self, market: dict) -> Optional[str]:
        """Returns silence reason string or None if should proceed."""
        results = self.db.query("""
            SELECT instrument FROM lt_advisor_log
            WHERE action_taken = 'ALERT'
              AND instrument IN (
                'UTI Nifty 50 Index Fund',
                'Niftybees ETF',
                'HDFC Nifty Next 50 Index Fund'
              )
              AND logged_at > datetime('now', '-7 days')
            LIMIT 1
        """)
        if results:
            return f"tier1_alerted_recently:{results[0]['instrument']}"

        return None

    def _check_vix_threshold_crossing(self, vix: float) -> Optional[dict]:
        """Returns VIX tranche alert if VIX crossed a new threshold this month."""
        for threshold in sorted(VIX_TRANCHE_MAP.keys(), reverse=True):
            if vix >= threshold:
                results = self.db.query(
                    """
                    SELECT COUNT(*) as cnt FROM lt_advisor_log
                    WHERE alert_type = 'VIX_THRESHOLD'
                      AND threshold_crossed = :threshold
                      AND strftime('%Y-%m', logged_at) = :month
                    """,
                    {"threshold": threshold, "month": self.month_str},
                )
                already = results[0]["cnt"] if results else 0

                if not already:
                    config = VIX_TRANCHE_MAP[threshold]
                    return {
                        "triggered": True,
                        "threshold_crossed": threshold,
                        "tranche_number": config["tranche"],
                        "suggested_amount_inr": config["suggested_inr"],
                        "first_time_this_month": True,
                    }
        return None

    def _get_morning_vix(self) -> Optional[float]:
        state = self.redis.get_state("state:lt_vix_morning")
        return float(state["vix"]) if state and "vix" in state else None

    # ── LLM calls ────────────────────────────────────────────────────────────

    def _run_opportunity_scan(
        self, market: dict, run_type: str
    ) -> Optional[dict]:
        """Calls PROMPT_OPPORTUNITY_SCAN. Returns parsed JSON or None."""
        from agents.lt_advisor.prompts import LT_SYSTEM_PROMPT, PROMPT_OPPORTUNITY_SCAN

        universe = list(LT_UNIVERSE["TIER_1"])
        if market["vix"] > 22:
            universe += LT_UNIVERSE["TIER_2"]
        if run_type == "WEEKLY":
            universe += LT_UNIVERSE["TIER_3"]

        vix_tranche = self._check_vix_threshold_crossing(market["vix"])

        system = LT_SYSTEM_PROMPT.format(
            date=self.today.isoformat(),
            time_ist=datetime.now().strftime("%H:%M"),
            run_type=run_type,
        )

        user = PROMPT_OPPORTUNITY_SCAN.format(
            vix=market["vix"],
            vix_30d_avg=market["vix_30d_avg"],
            vix_trend=market["vix_trend"],
            nifty=market["nifty"],
            nifty_52w_high=market["nifty_52w_high"],
            nifty_52w_low=market["nifty_52w_low"],
            nifty_from_high_pct=market["nifty_from_high_pct"],
            nifty_pe=market["nifty_pe"],
            fii_today=market["fii_today"],
            fii_5day=market["fii_5day"],
            fii_30day=market["fii_30day"],
            dii_5day=market["dii_5day"],
            sector_list=json.dumps(market["sector_list"], indent=2),
            calendar_events="\n".join(market["calendar_events"]),
            universe_json=json.dumps(universe, indent=2),
        )

        try:
            result = call_llm("lt_advisor", system, user, expect_json=True)

            if result.get("_parse_error"):
                logger.error("Opportunity scan returned unparseable response.")
                return None

            # Inject VIX tranche if triggered and LLM didn't include it
            if vix_tranche and not result.get("vix_tranche_alert", {}).get(
                "triggered"
            ):
                result["vix_tranche_alert"] = vix_tranche

            return result

        except Exception as e:
            logger.error("Opportunity scan LLM call failed: %s", e)
            return None

    def _draft_telegram_message(
        self, opportunity: dict, run_type: str
    ) -> Optional[str]:
        """Calls PROMPT_DRAFT_TELEGRAM. Returns plain text message."""
        from agents.lt_advisor.prompts import LT_SYSTEM_PROMPT, PROMPT_DRAFT_TELEGRAM

        system = LT_SYSTEM_PROMPT.format(
            date=self.today.isoformat(),
            time_ist=datetime.now().strftime("%H:%M"),
            run_type=run_type,
        )

        user = PROMPT_DRAFT_TELEGRAM.format(
            opportunity_json=json.dumps(opportunity, indent=2),
            time_ist=datetime.now().strftime("%H:%M"),
        )

        try:
            msg = call_llm("lt_advisor", system, user, expect_json=False)
            return msg.strip() if isinstance(msg, str) else None
        except Exception as e:
            logger.error("Telegram draft LLM call failed: %s", e)
            return None

    def _run_weekly_summary(self) -> None:
        """Saturday weekly summary — always sends regardless of score."""
        from agents.lt_advisor.prompts import LT_SYSTEM_PROMPT, PROMPT_WEEKLY_SUMMARY

        week_end = self.today.strftime("%d %b %Y")
        vix_hist = get_vix_history_30d()
        snapshot = self.redis.get_state("data:market_snapshot") or {}

        alerts_this_week = self.db.query("""
            SELECT instrument FROM lt_advisor_log
            WHERE action_taken = 'ALERT'
              AND logged_at > datetime('now', '-7 days')
        """)

        system = LT_SYSTEM_PROMPT.format(
            date=self.today.isoformat(),
            time_ist="10:00",
            run_type="WEEKLY",
        )

        user = PROMPT_WEEKLY_SUMMARY.format(
            week_end_date=week_end,
            nifty_weekly_pct=snapshot.get("nifty_weekly_pct", 0),
            vix_low=vix_hist["min"],
            vix_high=vix_hist["max"],
            vix_close=snapshot.get("vix", 0),
            fii_weekly=0,
            dii_weekly=0,
            alerts_count=len(alerts_this_week),
            alerted_instruments=", ".join(
                [r["instrument"] for r in alerts_this_week if r.get("instrument")]
            )
            or "none",
            next_week_events="\n".join(get_upcoming_events(14)),
        )

        try:
            msg = call_llm("lt_advisor", system, user, expect_json=False)
            if msg and isinstance(msg, str):
                self._send_to_orchestrator({"action": "WEEKLY"}, msg.strip())
                self._log_run(
                    "WEEKLY",
                    snapshot.get("vix", 0),
                    "ALERT",
                    None,
                    instrument="weekly_summary",
                    score=0,
                )
        except Exception as e:
            logger.error("Weekly summary failed: %s", e)

    # ── Output ───────────────────────────────────────────────────────────────

    def _send_to_orchestrator(
        self, opportunity: dict, telegram_msg: str
    ) -> None:
        """Publishes LT_ADVISOR_ALERT to Orchestrator channel."""
        instrument = (
            opportunity.get("top_opportunity", {}).get("instrument", "")
            if isinstance(opportunity.get("top_opportunity"), dict)
            else ""
        )

        self.redis.publish(
            "channel:orchestrator",
            {
                "from_agent": "lt_advisor",
                "to_agent": "orchestrator",
                "type": "LT_ADVISOR_ALERT",
                "priority": "NORMAL",
                "payload": {
                    "telegram_message": telegram_msg,
                    "instrument": instrument,
                    "score": (
                        opportunity.get("top_opportunity", {}).get("score", 0)
                        if isinstance(opportunity.get("top_opportunity"), dict)
                        else 0
                    ),
                    "run_type": opportunity.get("action", ""),
                },
                "timestamp": datetime.now().isoformat(),
            },
        )
        logger.info(
            "LT_ADVISOR_ALERT sent to Orchestrator. instrument=%s", instrument
        )

    def _log_run(
        self,
        run_type: str,
        vix: Optional[float],
        action: str,
        silence_reason: Optional[str],
        instrument: Optional[str] = None,
        score: Optional[int] = None,
        alert_type: str = "OPPORTUNITY",
        threshold: Optional[int] = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO lt_advisor_log (
                logged_at, run_type, vix_at_run, action_taken,
                silence_reason, instrument, score, alert_type,
                threshold_crossed, telegram_sent, llm_called
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now().isoformat(),
                run_type,
                vix,
                action,
                silence_reason,
                instrument,
                score,
                alert_type,
                threshold,
                action == "ALERT",
                action == "ALERT",
            ],
        )


# ── Standalone scoring function (also used by tests) ─────────────────────────


def compute_quick_score(
    vix: float,
    vix_trend: str,
    nifty_pe: float,
    nifty_from_high_pct: float,
    fii_30day_crore: float,
) -> int:
    """
    Pre-LLM score. 0-100.
    Implements the same scoring logic as PROMPT_OPPORTUNITY_SCAN
    so the Python check and LLM check are consistent.
    """
    score = 0

    # VIX component (30 points)
    if vix > 30:
        score += 30
    elif vix > 25:
        score += 25
    elif vix > 20:
        score += 18
    elif vix > 16:
        score += 10

    # VIX trend penalty
    if vix_trend == "FALLING" and vix > 20:
        score = int(score * 0.7)

    # Valuation (25 points)
    if nifty_pe and nifty_pe > 0:
        if nifty_pe < 16:
            score += 25
        elif nifty_pe < 18:
            score += 20
        elif nifty_pe < 20:
            score += 15
        elif nifty_pe < 22:
            score += 8
        elif nifty_pe < 25:
            score += 3

    # FII flow (20 points)
    if fii_30day_crore < -10000:
        score += 20
    elif fii_30day_crore < -5000:
        score += 15
    elif fii_30day_crore < -1000:
        score += 8

    # Distance from 52-week high (15 points)
    below_high = abs(min(nifty_from_high_pct, 0))
    if below_high > 20:
        score += 15
    elif below_high > 15:
        score += 12
    elif below_high > 10:
        score += 8
    elif below_high > 5:
        score += 4

    return min(score, 100)
