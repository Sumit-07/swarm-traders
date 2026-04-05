"""Inter-agent message schema and payload models.

Every message between agents follows the AgentMessage schema.
Payload schemas define the structured data exchanged for specific operations.
"""

from datetime import datetime
from enum import Enum
from uuid import uuid4
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from pydantic import BaseModel, Field


# --- Enums ---

class MessageType(str, Enum):
    SIGNAL = "SIGNAL"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    ALERT = "ALERT"
    COMMAND = "COMMAND"
    HEARTBEAT = "HEARTBEAT"
    SYNTHESIS = "SYNTHESIS"


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


# --- Core Message ---

class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    from_agent: str
    to_agent: str  # agent_id or "broadcast"
    channel: str  # Redis channel name
    type: MessageType
    priority: Priority = Priority.NORMAL
    payload: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(IST).isoformat())
    ttl_seconds: int = 300
    requires_response: bool = False
    correlation_id: str | None = None


# --- Payload Schemas ---

class StrategyConfig(BaseModel):
    """Payload: Orchestrator -> Analyst (strategy config for the day)."""
    strategy_name: str
    watchlist: list[str]
    entry_conditions: dict
    exit_conditions: dict
    capital_allocation_pct: int
    max_trades: int
    bucket: str  # "conservative" or "risk"
    valid_until: str  # "HH:MM" IST


class TradeProposal(BaseModel):
    """Payload: Analyst -> Risk Agent (trade signal for review)."""
    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    exchange: str = "NSE"
    direction: str  # LONG / SHORT
    signal_type: str  # e.g. RSI_OVERSOLD
    entry_price: float
    quantity_suggested: int
    stop_loss: float
    target: float
    signal_confidence: str  # HIGH / MEDIUM / LOW
    indicator_snapshot: dict = Field(default_factory=dict)
    bucket: str  # "conservative" or "risk"
    analyst_note: str = ""


class RiskDecision(BaseModel):
    """Payload: Risk Agent -> Orchestrator (approval/rejection)."""
    proposal_id: str
    decision: str  # APPROVED / REJECTED
    reason: str
    approved_position_size: int = 0
    approved_stop_loss: float = 0.0
    approved_target: float = 0.0
    risk_pct_final: float = 0.0
    flag_human: bool = False


class ApprovedOrder(BaseModel):
    """Payload: Orchestrator -> Execution Agent (execute this order)."""
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    proposal_id: str
    symbol: str
    exchange: str = "NSE"
    transaction_type: str  # BUY / SELL
    quantity: int
    order_type: str  # LIMIT / MARKET
    price: float
    stop_loss_price: float
    target_price: float
    bucket: str
    mode: str  # PAPER / LIVE
    approved_by: str
    approved_at: str = Field(default_factory=lambda: datetime.now(IST).isoformat())


class FillConfirmation(BaseModel):
    """Payload: Execution Agent -> Orchestrator + Compliance (fill result)."""
    order_id: str
    proposal_id: str
    symbol: str
    transaction_type: str
    quantity: int
    fill_price: float
    slippage: float
    brokerage: float
    status: str  # FILLED / FAILED / PARTIAL
    filled_at: str
    stop_loss_placed: bool = False
    mode: str  # PAPER / LIVE


class ConflictResolution(BaseModel):
    """Payload: Orchestrator internal (when Analyst and Risk disagree)."""
    decision: str  # APPROVE_TRADE / REJECT_TRADE / REQUEST_MORE_DATA
    reason: str
    notify_human: bool = False
    urgency: str = "normal"  # high / normal
