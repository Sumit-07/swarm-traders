"""BaseAgent — abstract base class for all trading swarm agents.

Provides lifecycle management, Redis pub/sub messaging with routing validation,
heartbeat, markdown/prompt loading, and LangGraph integration.
"""

import re
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from agents.message import AgentMessage, MessageType, Priority
from config import ALLOWED_COMMUNICATION_PATHS, REDIS_CHANNELS
from memory.redis_store import RedisStore
from memory.sqlite_store import SQLiteStore
from tools.logger import get_agent_logger


class AgentState(str, Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class CommunicationError(Exception):
    pass


class BaseAgent(ABC):
    def __init__(self, agent_id: str, redis_store: RedisStore,
                 sqlite_store: SQLiteStore):
        self.agent_id = agent_id
        self.redis = redis_store
        self.sqlite = sqlite_store
        self.state = AgentState.IDLE
        self.logger = get_agent_logger(agent_id)
        self.scheduler = BackgroundScheduler(daemon=True)
        self._pending_responses: dict = {}
        self._message_thread: threading.Thread | None = None
        self._running = False
        self._last_action: str = "initialized"
        self._llm_call_count: int = 0

        # Load soul.md and prompts.md from agent's directory
        self.soul = self._load_markdown("soul.md")
        self.prompts = self._load_prompts("prompts.md")

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    def start(self):
        """Start the agent: register, listen, heartbeat."""
        self._running = True
        self.state = AgentState.ACTIVE
        self._register()
        self._start_listener()
        self._schedule_heartbeat()
        self.scheduler.start()
        self.on_start()
        self._last_action = "started"
        self.logger.info(f"{self.agent_id} started")

    def stop(self):
        """Stop the agent gracefully."""
        self._running = False
        self.state = AgentState.OFFLINE
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        self.on_stop()
        self._deregister()
        self._last_action = "stopped"
        self.logger.info(f"{self.agent_id} stopped")

    def sleep(self):
        """Enter idle mode — stop processing but keep heartbeat."""
        self.state = AgentState.IDLE
        self._last_action = "sleeping"
        self.on_sleep()
        self.logger.info(f"{self.agent_id} entering sleep mode")

    def wake(self):
        """Return from idle mode."""
        self.state = AgentState.ACTIVE
        self._last_action = "woke up"
        self.on_wake()
        self.logger.info(f"{self.agent_id} waking up")

    # ----------------------------------------------------------------
    # Communication
    # ----------------------------------------------------------------

    def send_message(self, to_agent: str, msg_type: MessageType,
                     payload: dict, priority: Priority = Priority.NORMAL,
                     requires_response: bool = False,
                     correlation_id: str | None = None) -> str:
        """Send a message to another agent via Redis pub/sub.

        Validates routing against ALLOWED_COMMUNICATION_PATHS.
        Logs to SQLite for audit trail.
        Returns the message_id.
        """
        # Any agent can always message orchestrator
        if to_agent != "orchestrator":
            allowed = ALLOWED_COMMUNICATION_PATHS.get(self.agent_id, [])
            if to_agent not in allowed and to_agent != "broadcast":
                raise CommunicationError(
                    f"{self.agent_id} is not allowed to send to {to_agent}. "
                    f"Allowed: {allowed}"
                )

        channel = REDIS_CHANNELS.get(to_agent, f"channel:{to_agent}")
        msg = AgentMessage(
            from_agent=self.agent_id,
            to_agent=to_agent,
            channel=channel,
            type=msg_type,
            priority=priority,
            payload=payload,
            requires_response=requires_response,
            correlation_id=correlation_id,
        )

        # Publish to Redis
        self.redis.publish(channel, msg.model_dump())
        self.logger.debug(f"Sent {msg_type.value} to {to_agent}: {msg.message_id}")

        # Audit trail
        try:
            self.sqlite.log_message({
                **msg.model_dump(),
                "status": "DELIVERED",
            })
        except Exception as e:
            self.logger.warning(f"Failed to log message to SQLite: {e}")

        # Track pending responses
        if requires_response:
            self._pending_responses[msg.message_id] = {
                "sent_at": datetime.now(),
                "timeout": timedelta(seconds=30),
                "retries": 0,
                "to_agent": to_agent,
                "msg": msg.model_dump(),
            }

        self._last_action = f"sent {msg_type.value} to {to_agent}"
        return msg.message_id

    def _start_listener(self):
        """Start background thread to listen for incoming messages."""
        self._message_thread = threading.Thread(
            target=self._message_listener, daemon=True,
            name=f"{self.agent_id}-listener",
        )
        self._message_thread.start()

    def _message_listener(self):
        """Listen on agent's channel and broadcast channel."""
        pubsub = self.redis.get_pubsub()
        my_channel = REDIS_CHANNELS.get(self.agent_id, f"channel:{self.agent_id}")
        pubsub.subscribe(my_channel, "channel:broadcast")
        self.logger.debug(f"Listening on {my_channel} and channel:broadcast")

        for raw_message in pubsub.listen():
            if not self._running:
                break
            if raw_message["type"] != "message":
                continue

            try:
                import json
                data = json.loads(raw_message["data"])
                msg = AgentMessage(**data)

                # Check TTL
                msg_time = datetime.fromisoformat(msg.timestamp)
                age = (datetime.now() - msg_time).total_seconds()
                if age > msg.ttl_seconds:
                    self.logger.warning(
                        f"Expired message from {msg.from_agent}: "
                        f"age={age:.0f}s > ttl={msg.ttl_seconds}s"
                    )
                    try:
                        self.sqlite.log_message({
                            **msg.model_dump(), "status": "EXPIRED",
                        })
                    except Exception:
                        pass
                    continue

                # Check if this is a response to a pending request
                if msg.correlation_id and msg.correlation_id in self._pending_responses:
                    del self._pending_responses[msg.correlation_id]

                # Route to handler
                self._handle_message(msg)

            except Exception as e:
                self.logger.error(f"Error processing message: {e}")

    def _handle_message(self, msg: AgentMessage):
        """Route message to the appropriate handler."""
        if msg.type == MessageType.HEARTBEAT:
            return  # Ignore heartbeats from others

        self.logger.info(
            f"Received {msg.type.value} from {msg.from_agent} "
            f"[priority={msg.priority.value}]"
        )
        self._last_action = f"received {msg.type.value} from {msg.from_agent}"
        self.on_message(msg)

    def _check_pending_responses(self):
        """Check for timed-out pending responses. Called every 10 seconds."""
        now = datetime.now()
        expired = []
        for msg_id, meta in self._pending_responses.items():
            if now - meta["sent_at"] > meta["timeout"]:
                if meta["retries"] < 1:
                    # Retry once
                    meta["retries"] += 1
                    meta["sent_at"] = now
                    self.logger.warning(
                        f"Retrying message {msg_id} to {meta['to_agent']}"
                    )
                    channel = REDIS_CHANNELS.get(
                        meta["to_agent"], f"channel:{meta['to_agent']}"
                    )
                    self.redis.publish(channel, meta["msg"])
                else:
                    # Two failures — notify Orchestrator
                    expired.append(msg_id)
                    if self.agent_id != "orchestrator":
                        try:
                            self.send_message(
                                to_agent="orchestrator",
                                msg_type=MessageType.ALERT,
                                payload={
                                    "alert": "response_timeout",
                                    "original_message_id": msg_id,
                                    "target_agent": meta["to_agent"],
                                },
                                priority=Priority.HIGH,
                            )
                        except Exception as e:
                            self.logger.error(f"Failed to alert orchestrator: {e}")

        for msg_id in expired:
            del self._pending_responses[msg_id]

    # ----------------------------------------------------------------
    # Heartbeat
    # ----------------------------------------------------------------

    def _schedule_heartbeat(self):
        self.scheduler.add_job(
            self._send_heartbeat, "interval", seconds=60,
            id=f"{self.agent_id}_heartbeat",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._check_pending_responses, "interval", seconds=10,
            id=f"{self.agent_id}_pending_check",
            replace_existing=True,
        )

    def _send_heartbeat(self):
        self.redis.set_state(f"agent:{self.agent_id}:heartbeat", {
            "agent_id": self.agent_id,
            "state": self.state.value,
            "timestamp": datetime.now().isoformat(),
            "last_action": self._last_action,
            "llm_calls_today": self._llm_call_count,
        })

    # ----------------------------------------------------------------
    # Registration
    # ----------------------------------------------------------------

    def _register(self):
        """Register agent in the global agent status store."""
        agents = self.redis.get_state("state:all_agents") or {}
        agents[self.agent_id] = {
            "state": self.state.value,
            "started_at": datetime.now().isoformat(),
            "last_action": self._last_action,
        }
        self.redis.set_state("state:all_agents", agents)

    def _deregister(self):
        """Mark agent as offline in global status."""
        agents = self.redis.get_state("state:all_agents") or {}
        if self.agent_id in agents:
            agents[self.agent_id]["state"] = AgentState.OFFLINE.value
            agents[self.agent_id]["stopped_at"] = datetime.now().isoformat()
            self.redis.set_state("state:all_agents", agents)

    # ----------------------------------------------------------------
    # Markdown loading
    # ----------------------------------------------------------------

    def _load_markdown(self, filename: str) -> str:
        """Load a markdown file from the agent's directory."""
        path = Path("agents") / self.agent_id / filename
        if path.exists():
            return path.read_text()
        return ""

    def _load_prompts(self, filename: str) -> dict:
        """Parse prompts.md into a dict of {PROMPT_NAME: template_string}.

        Splits on ## PROMPT_ or ## SYSTEM_PROMPT headings.
        """
        content = self._load_markdown(filename)
        if not content:
            return {}

        prompts = {}
        # Split on ## headings that start with PROMPT_ or SYSTEM_PROMPT
        sections = re.split(r"(?=^## (?:PROMPT_|SYSTEM_PROMPT))", content, flags=re.MULTILINE)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            # Extract heading name
            match = re.match(r"^## (PROMPT_\w+|SYSTEM_PROMPT)", section)
            if match:
                name = match.group(1)
                # Extract template content between ``` fences
                template_match = re.search(
                    r"### Template\s*```\s*(.*?)\s*```",
                    section, re.DOTALL,
                )
                if template_match:
                    prompts[name] = template_match.group(1).strip()
                elif name == "SYSTEM_PROMPT":
                    # System prompt might just be between ``` fences
                    sp_match = re.search(r"```\s*(.*?)\s*```", section, re.DOTALL)
                    if sp_match:
                        prompts[name] = sp_match.group(1).strip()

        return prompts

    # ----------------------------------------------------------------
    # LLM
    # ----------------------------------------------------------------

    def call_llm(self, prompt_name: str, variables: dict,
                 expect_json: bool = True) -> dict | str:
        """Call the LLM with a named prompt template.

        Loads the system prompt and the named prompt from prompts.md,
        renders variables, calls the model routed by AGENT_LLM_MODELS.

        Args:
            prompt_name: Key from self.prompts (e.g., "PROMPT_CONFLICT_RESOLUTION")
            variables: Dict of template variables to substitute
            expect_json: If True, parse response as JSON dict

        Returns: Parsed JSON dict or raw string
        """
        from tools.llm import call_llm as _call_llm, render_prompt

        # Build system prompt
        system_template = self.prompts.get("SYSTEM_PROMPT", "")
        system_prompt = render_prompt(system_template, variables) if system_template else ""

        # Build user prompt
        user_template = self.prompts.get(prompt_name, "")
        if not user_template:
            raise ValueError(
                f"Prompt '{prompt_name}' not found for agent {self.agent_id}. "
                f"Available: {list(self.prompts.keys())}"
            )
        user_prompt = render_prompt(user_template, variables)

        self._llm_call_count += 1
        self._last_action = f"calling LLM ({prompt_name})"
        self.logger.info(f"LLM call #{self._llm_call_count}: {prompt_name}")

        result = _call_llm(
            agent_id=self.agent_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_json=expect_json,
        )

        self._last_action = f"LLM returned ({prompt_name})"
        return result

    # ----------------------------------------------------------------
    # Graph integration
    # ----------------------------------------------------------------

    def run(self, state: dict) -> dict:
        """Called by LangGraph when this agent is a graph node.

        Receives SwarmState, does the agent's work, returns updated state.
        Subclasses should override this with their logic.
        """
        return state

    # ----------------------------------------------------------------
    # Subclass hooks
    # ----------------------------------------------------------------

    @abstractmethod
    def on_start(self):
        """Called after agent starts. Set up scheduled jobs here."""
        ...

    @abstractmethod
    def on_stop(self):
        """Called before agent stops. Clean up here."""
        ...

    @abstractmethod
    def on_message(self, message: AgentMessage):
        """Called when a message is received on this agent's channel."""
        ...

    def on_sleep(self):
        """Called when agent enters idle mode. Override if needed."""
        pass

    def on_wake(self):
        """Called when agent returns from idle. Override if needed."""
        pass
