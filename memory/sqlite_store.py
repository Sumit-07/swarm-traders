"""SQLite persistent storage for trades, signals, messages, and audit logs."""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from tools.logger import get_agent_logger

logger = get_agent_logger("sqlite_store")

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SQLiteStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        self.init_db()

    def init_db(self):
        """Create all tables from schema.sql."""
        schema_sql = SCHEMA_PATH.read_text()
        with self.engine.connect() as conn:
            for statement in schema_sql.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))
            conn.commit()
        logger.info("SQLite database initialized")

    # --- Trades ---

    def log_trade(self, trade: dict):
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO trades (
                        trade_id, proposal_id, symbol, exchange, direction,
                        bucket, strategy, entry_price, exit_price, quantity,
                        stop_loss, target, status, entry_time, exit_time,
                        pnl, pnl_pct, fees, signal_confidence, analyst_note,
                        risk_approval, mode
                    ) VALUES (
                        :trade_id, :proposal_id, :symbol, :exchange, :direction,
                        :bucket, :strategy, :entry_price, :exit_price, :quantity,
                        :stop_loss, :target, :status, :entry_time, :exit_time,
                        :pnl, :pnl_pct, :fees, :signal_confidence, :analyst_note,
                        :risk_approval, :mode
                    )
                """),
                trade,
            )
            conn.commit()

    def update_trade(self, trade_id: str, updates: dict):
        updates["updated_at"] = datetime.now().isoformat()
        updates["trade_id"] = trade_id
        set_clause = ", ".join(
            f"{k} = :{k}" for k in updates if k != "trade_id"
        )
        with self.engine.connect() as conn:
            conn.execute(
                text(f"UPDATE trades SET {set_clause} WHERE trade_id = :trade_id"),
                updates,
            )
            conn.commit()

    def get_trades(self, date: str = None, status: str = None) -> list[dict]:
        conditions = []
        params = {}
        if date:
            conditions.append("DATE(entry_time) = :date")
            params["date"] = date
        if status:
            conditions.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.query(
            f"SELECT * FROM trades {where} ORDER BY entry_time DESC", params
        )

    # --- Signals ---

    def log_signal(self, signal: dict):
        if "indicator_snapshot" in signal and isinstance(
            signal["indicator_snapshot"], dict
        ):
            signal["indicator_snapshot"] = json.dumps(signal["indicator_snapshot"])
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO signals (
                        signal_id, symbol, strategy, signal_type,
                        indicator_snapshot, confidence, valid, invalidation_reason
                    ) VALUES (
                        :signal_id, :symbol, :strategy, :signal_type,
                        :indicator_snapshot, :confidence, :valid, :invalidation_reason
                    )
                """),
                signal,
            )
            conn.commit()

    # --- Messages ---

    def log_message(self, message: dict):
        if "payload" in message and isinstance(message["payload"], dict):
            message["payload"] = json.dumps(message["payload"])
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO agent_messages (
                        message_id, from_agent, to_agent, channel, type,
                        priority, payload, timestamp, ttl_seconds,
                        requires_response, correlation_id, status
                    ) VALUES (
                        :message_id, :from_agent, :to_agent, :channel, :type,
                        :priority, :payload, :timestamp, :ttl_seconds,
                        :requires_response, :correlation_id, :status
                    )
                """),
                {
                    "message_id": message.get("message_id"),
                    "from_agent": message.get("from_agent"),
                    "to_agent": message.get("to_agent"),
                    "channel": message.get("channel"),
                    "type": message.get("type"),
                    "priority": message.get("priority", "NORMAL"),
                    "payload": message.get("payload"),
                    "timestamp": message.get("timestamp"),
                    "ttl_seconds": message.get("ttl_seconds", 300),
                    "requires_response": 1 if message.get("requires_response") else 0,
                    "correlation_id": message.get("correlation_id"),
                    "status": message.get("status", "DELIVERED"),
                },
            )
            conn.commit()

    # --- Daily P&L ---

    def log_daily_pnl(self, pnl: dict):
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT OR REPLACE INTO daily_pnl (
                        date, conservative_pnl, risk_pnl, total_pnl,
                        trades_count, wins, losses, max_drawdown, system_mode
                    ) VALUES (
                        :date, :conservative_pnl, :risk_pnl, :total_pnl,
                        :trades_count, :wins, :losses, :max_drawdown, :system_mode
                    )
                """),
                pnl,
            )
            conn.commit()

    def get_daily_pnl(self, date: str) -> dict | None:
        rows = self.query("SELECT * FROM daily_pnl WHERE date = :date", {"date": date})
        return rows[0] if rows else None

    # --- Audit ---

    def log_audit(self, audit: dict):
        if "violations" in audit and isinstance(audit["violations"], list):
            audit["violations"] = json.dumps(audit["violations"])
        if "report_json" in audit and isinstance(audit["report_json"], dict):
            audit["report_json"] = json.dumps(audit["report_json"])
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO compliance_audit (
                        audit_date, total_trades, violations,
                        compliance_score, notes, report_json
                    ) VALUES (
                        :audit_date, :total_trades, :violations,
                        :compliance_score, :notes, :report_json
                    )
                """),
                audit,
            )
            conn.commit()

    # --- Data Log ---

    def log_data_event(self, source: str, data_type: str, symbol: str = None,
                       success: bool = True, error_message: str = None,
                       fallback_used: bool = False):
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO data_log (
                        source, data_type, symbol, success, error_message, fallback_used
                    ) VALUES (
                        :source, :data_type, :symbol, :success, :error_message, :fallback_used
                    )
                """),
                {
                    "source": source,
                    "data_type": data_type,
                    "symbol": symbol,
                    "success": 1 if success else 0,
                    "error_message": error_message,
                    "fallback_used": 1 if fallback_used else 0,
                },
            )
            conn.commit()

    # --- Generic Query ---

    def query(self, sql: str, params: dict = None) -> list[dict]:
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            columns = result.keys()
            return [dict(zip(columns, row)) for row in result.fetchall()]

    def execute(self, sql: str, params: list = None):
        """Execute INSERT/UPDATE/DELETE with positional ? params.

        Converts ? placeholders to :p0, :p1, ... for SQLAlchemy text().
        Returns the result proxy (use .rowcount for affected rows).
        """
        named_params = {}
        if params:
            converted_sql = sql
            for i, val in enumerate(params):
                converted_sql = converted_sql.replace("?", f":p{i}", 1)
                named_params[f"p{i}"] = val
            sql = converted_sql

        with self.engine.connect() as conn:
            result = conn.execute(text(sql), named_params)
            conn.commit()
            return result
