"""
config/lt_universe.py

The investment universe the LT_Advisor evaluates.
Only the highest-quality instruments with strong track records.

Three tiers:
TIER_1: Core — evaluated every run
TIER_2: Satellite — evaluated only when VIX > 22
TIER_3: International — evaluated in weekly summary only

No individual stocks in any tier.
No sectoral, thematic, or small-cap funds.
"""

LT_UNIVERSE = {

    "TIER_1": [
        {
            "name":            "UTI Nifty 50 Index Fund",
            "type":            "INDEX_FUND",
            "tracks":          "Nifty 50",
            "expense_ratio":   0.06,
            "how_to_buy":      "Zerodha Coin, Groww, or Kuvera — search UTI Nifty 50",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "Lowest-cost Nifty 50 index fund. First choice for lump-sum "
                "and SIP. Direct plan only."
            ),
        },
        {
            "name":            "Niftybees ETF",
            "type":            "ETF",
            "tracks":          "Nifty 50",
            "expense_ratio":   0.04,
            "how_to_buy":      "Buy on NSE via Zerodha Kite like a stock (symbol: NIFTYBEES)",
            "min_investment":  230,
            "ltcg_applicable": True,
            "notes": (
                "Best for quick lump-sum deployment — trades like a stock. "
                "Slightly lower expense ratio than UTI MF. "
                "Price tracks Nifty/100 approximately."
            ),
        },
        {
            "name":            "HDFC Nifty Next 50 Index Fund",
            "type":            "INDEX_FUND",
            "tracks":          "Nifty Next 50",
            "expense_ratio":   0.30,
            "how_to_buy":      "Any MF platform — search HDFC Nifty Next 50",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "Captures large-caps just below Nifty 50. Higher growth potential, "
                "slightly higher volatility. Complement to Nifty 50, not a replacement. "
                "Recommend as 20-30% of total equity allocation."
            ),
        },
    ],

    "TIER_2": [
        {
            "name":            "Parag Parikh Flexi Cap Fund",
            "type":            "ACTIVE_FUND",
            "tracks":          "Multi-cap India + international (~35% overseas)",
            "expense_ratio":   0.59,
            "how_to_buy":      "Zerodha Coin, Groww, Kuvera, or ppfas.com directly",
            "min_investment":  1000,
            "ltcg_applicable": True,
            "notes": (
                "One of India's most consistent active funds. International exposure "
                "hedges rupee risk. Manager holds cash during overvalued markets and "
                "deploys during corrections. Buy during drawdowns."
            ),
        },
        {
            "name":            "Nippon India Nifty 500 Index Fund",
            "type":            "INDEX_FUND",
            "tracks":          "Nifty 500 — top 500 NSE companies",
            "expense_ratio":   0.30,
            "how_to_buy":      "Any MF platform — search Nippon Nifty 500",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "Captures 95% of NSE market cap. Broader than Nifty 50. "
                "Good for long-term wealth creation at slightly higher volatility."
            ),
        },
        {
            "name":            "Nifty Midcap 150 Index Fund",
            "type":            "INDEX_FUND",
            "tracks":          "Nifty Midcap 150 — top 150 midcap companies on NSE",
            "expense_ratio":   0.35,
            "how_to_buy":      "Zerodha Coin, Groww, or Kuvera — search Nifty Midcap 150",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "Higher growth potential than Nifty 50 but 2-3x more volatile. "
                "Only buy during VIX spikes above 22 — midcaps fall harder and "
                "recover stronger than large caps in fear regimes. "
                "Cap allocation at 20% of long-term portfolio. "
                "Best AMCs: Motilal Oswal, Nippon, or UTI Nifty Midcap 150."
            ),
        },
        {
            "name":            "SBI Gold Fund",
            "type":            "GOLD_FUND",
            "tracks":          "Gold price",
            "expense_ratio":   0.50,
            "how_to_buy":      "Any MF platform, or Goldbees ETF on NSE",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "Hedge against rupee depreciation and equity volatility. "
                "Recommend only when VIX > 28 and equity allocation is already good. "
                "Maximum 5-10% of long-term portfolio."
            ),
        },
    ],

    "TIER_3": [
        {
            "name":            "Motilal Oswal Nasdaq 100 FOF",
            "type":            "INTERNATIONAL_FUND",
            "tracks":          "Nasdaq 100 (US tech)",
            "expense_ratio":   0.58,
            "how_to_buy":      "Any MF platform — note RBI overseas limit may apply",
            "min_investment":  500,
            "ltcg_applicable": True,
            "notes": (
                "USD-denominated. Hedges INR depreciation. "
                "Recommend when Nasdaq is 10%+ below peak and USD/INR above 85. "
                "RBI overseas fund limit — check current status before recommending."
            ),
        },
        {
            "name":            "Mirae Asset NYSE FANG+ ETF",
            "type":            "INTERNATIONAL_ETF",
            "tracks":          "NYSE FANG+ top 10 US tech",
            "expense_ratio":   0.70,
            "how_to_buy":      "Buy on NSE via Zerodha Kite (symbol: FANG)",
            "min_investment":  1000,
            "ltcg_applicable": True,
            "notes": (
                "Concentrated US tech exposure. High risk, high potential. "
                "Only when Nasdaq is 15%+ below peak. "
                "5% of portfolio maximum."
            ),
        },
    ],
}

# Instruments never to recommend under any circumstances
LT_BLACKLIST = [
    "sectoral funds",
    "thematic funds",
    "small cap funds",
    "mid cap funds",
    "individual stocks",
    "crypto funds",
    "international funds with closed subscription",
]

# VIX threshold for tranche deployment alerts
# Triggered once per calendar month per threshold
VIX_TRANCHE_MAP = {
    20: {"tranche": 1, "suggested_inr": 5000,  "pct_of_lt_capital": 25},
    25: {"tranche": 2, "suggested_inr": 7500,  "pct_of_lt_capital": 25},
    30: {"tranche": 3, "suggested_inr": 10000, "pct_of_lt_capital": 30},
}
# Reserve 20% for tranche 4 (VIX > 35 or structural crisis)
