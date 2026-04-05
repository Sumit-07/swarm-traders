"""Optimizer Agent — Post-market learning system.

Chairs a 3-round meeting after market close with Strategist, Risk Strategist,
and Analyst. Extracts actionable learnings and writes them to the knowledge graph.
Never touches live trading. Post-market only.
"""

from agents.base_agent import BaseAgent
from agents.message import AgentMessage


class OptimizerAgent(BaseAgent):
    """Optimizer agent — delegates meeting logic to meeting_subgraph.py."""

    def __init__(self, redis_store, sqlite_store):
        super().__init__("optimizer", redis_store, sqlite_store)

    def on_start(self):
        self.logger.info("Optimizer agent started. Waiting for 3:50 PM trigger.")

    def on_stop(self):
        pass

    def on_message(self, message: AgentMessage):
        # Optimizer doesn't process incoming messages — meeting is graph-driven
        self.logger.debug(
            "Ignoring message from %s (optimizer is graph-driven)",
            message.from_agent,
        )

    def run(self, state: dict) -> dict:
        """Called as LangGraph node if needed. Meeting logic is in subgraph."""
        return state
