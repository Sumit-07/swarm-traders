-- Trading Swarm Database Schema

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    proposal_id TEXT,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'NSE',
    direction TEXT NOT NULL,            -- LONG / SHORT
    bucket TEXT NOT NULL,               -- conservative / risk
    strategy TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    stop_loss REAL,
    target REAL,
    status TEXT NOT NULL,               -- OPEN / CLOSED / CANCELLED
    entry_time TEXT,
    exit_time TEXT,
    pnl REAL,
    pnl_pct REAL,
    fees REAL,
    signal_confidence TEXT,
    analyst_note TEXT,
    risk_approval TEXT,
    mode TEXT DEFAULT 'PAPER',          -- PAPER / LIVE
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signal_type TEXT NOT NULL,           -- LONG / SHORT
    indicator_snapshot TEXT,             -- JSON blob
    confidence TEXT,
    valid INTEGER,                      -- 0 / 1
    invalidation_reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT UNIQUE NOT NULL,
    conservative_pnl REAL DEFAULT 0,
    risk_pnl REAL DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    trades_count INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    system_mode TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    channel TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT DEFAULT 'NORMAL',
    payload TEXT,                        -- JSON blob
    timestamp TEXT NOT NULL,
    ttl_seconds INTEGER DEFAULT 300,
    requires_response INTEGER DEFAULT 0,
    correlation_id TEXT,
    status TEXT DEFAULT 'DELIVERED',     -- DELIVERED / EXPIRED / FAILED
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orchestrator_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    description TEXT,
    agent_involved TEXT,
    decision TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS compliance_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_date TEXT NOT NULL,
    total_trades INTEGER,
    violations TEXT,                     -- JSON array
    compliance_score REAL,
    notes TEXT,
    report_json TEXT,                    -- full audit report JSON
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                -- fyers / yfinance / nsepython
    data_type TEXT NOT NULL,             -- quote / ohlcv / options_chain / news
    symbol TEXT,
    success INTEGER,
    error_message TEXT,
    fallback_used INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Optimizer knowledge graph
CREATE TABLE IF NOT EXISTS learnings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_date        DATE    NOT NULL,
    agent_target        TEXT    NOT NULL CHECK (agent_target IN
                            ('strategist','risk_strategist','analyst','all')),
    category            TEXT    NOT NULL CHECK (category IN
                            ('regime_detection','signal_quality',
                             'position_sizing','timing','risk_sizing')),
    regime              TEXT    NOT NULL CHECK (regime IN
                            ('trending','ranging','high_volatility','all')),
    applies_to          TEXT    NOT NULL CHECK (applies_to IN
                            ('intraday','swing','options','all')),
    learning            TEXT    NOT NULL,
    confidence          REAL    NOT NULL DEFAULT 0.70
                            CHECK (confidence BETWEEN 0.0 AND 1.0),
    times_reinforced    INTEGER NOT NULL DEFAULT 1,
    last_reinforced     DATE    NOT NULL,
    outcome_pnl         REAL,
    archived            BOOLEAN NOT NULL DEFAULT FALSE,
    source_meeting_date DATE    NOT NULL
);

CREATE TABLE IF NOT EXISTS optimizer_meetings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_date    DATE    NOT NULL UNIQUE,
    trade_count     INTEGER NOT NULL,
    conservative_pnl REAL,
    risk_pnl        REAL,
    regime          TEXT,
    round1_strategist    TEXT,
    round1_risk_strat    TEXT,
    round1_analyst       TEXT,
    round2_strategist    TEXT,
    round2_risk_strat    TEXT,
    round2_analyst       TEXT,
    round3_strategist    TEXT,
    round3_risk_strat    TEXT,
    round3_analyst       TEXT,
    synthesis_raw        TEXT,
    learnings_written    INTEGER DEFAULT 0,
    telegram_sent        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_learnings_agent ON learnings(agent_target);
CREATE INDEX IF NOT EXISTS idx_learnings_regime ON learnings(regime);
CREATE INDEX IF NOT EXISTS idx_learnings_archived ON learnings(archived);
CREATE INDEX IF NOT EXISTS idx_learnings_confidence ON learnings(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_from ON agent_messages(from_agent);
CREATE INDEX IF NOT EXISTS idx_messages_to ON agent_messages(to_agent);
CREATE INDEX IF NOT EXISTS idx_data_log_created ON data_log(created_at);
