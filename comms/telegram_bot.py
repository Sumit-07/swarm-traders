"""Telegram bot for human interface.

Handles sending messages, receiving commands, and the approval flow.
Commands publish to channel:orchestrator on Redis.
"""

import asyncio
import threading

from tools.logger import get_agent_logger

logger = get_agent_logger("telegram_bot")


class TelegramBot:
    """Telegram bot wrapper.

    Can operate in two modes:
    - Full mode: with python-telegram-bot (requires TELEGRAM_BOT_TOKEN)
    - Stub mode: logs messages to console (when no token is configured)
    """

    def __init__(self, token: str = "", chat_id: str = "",
                 redis_store=None):
        self.token = token
        self.chat_id = chat_id
        self.redis = redis_store
        self._app = None
        self._running = False
        self._loop = None
        self._stub_mode = not token

        if self._stub_mode:
            logger.warning("Telegram bot running in STUB mode (no token)")

    def start(self):
        """Start the Telegram bot in a background thread."""
        if self._stub_mode:
            logger.info("Telegram stub mode — messages will be logged to console")
            return

        try:
            from telegram.ext import (
                ApplicationBuilder, CommandHandler, MessageHandler, filters,
            )

            self._app = ApplicationBuilder().token(self.token).build()

            # Register command handlers
            commands = {
                "status": self._cmd_status,
                "positions": self._cmd_positions,
                "halt": self._cmd_halt,
                "resume": self._cmd_resume,
                "paper": self._cmd_paper,
                "live": self._cmd_live,
                "pnl": self._cmd_pnl,
                "strategy": self._cmd_strategy,
                "report": self._cmd_report,
                "agents": self._cmd_agents,
                "authenticate": self._cmd_authenticate,
                "optimizer": self._cmd_optimizer,
                "catchup": self._cmd_catchup,
                "dryrun": self._cmd_dryrun,
                "lt_scan": self._cmd_lt_scan,
            }
            for cmd_name, handler in commands.items():
                self._app.add_handler(CommandHandler(cmd_name, handler))

            # Handle approve/reject with arguments
            self._app.add_handler(CommandHandler("approve", self._cmd_approve))
            self._app.add_handler(CommandHandler("reject", self._cmd_reject))

            # Handle plain text (YES/NO/EDIT responses)
            self._app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
            )

            # Run in background thread
            thread = threading.Thread(
                target=self._run_polling, daemon=True,
                name="telegram-bot",
            )
            thread.start()
            self._running = True
            logger.info("Telegram bot started")

        except ImportError:
            logger.warning("python-telegram-bot not installed — using stub mode")
            self._stub_mode = True
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")
            self._stub_mode = True

    def _run_polling(self):
        """Run the bot polling loop in a separate thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_polling())

    async def _async_polling(self):
        """Async polling loop that doesn't register signal handlers."""
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        # Block until stop() is called
        self._stop_event = asyncio.Event()
        await self._stop_event.wait()
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    def send_message(self, text: str):
        """Send a message to the configured chat."""
        if self._stub_mode:
            logger.info(f"[TELEGRAM] {text}")
            return

        try:
            import asyncio

            async def _send():
                await self._app.bot.send_message(
                    chat_id=self.chat_id, text=text,
                )

            if self._loop and self._loop.is_running():
                # Schedule on the bot's own event loop (thread-safe)
                future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
                future.result(timeout=10)
            else:
                # Fallback: create a new loop (e.g. during startup)
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_send())
                loop.close()
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            logger.info(f"[TELEGRAM FALLBACK] {text}")

    def send_auth_request(self, message: str, url: str, button_text: str) -> None:
        """Send a Telegram message with an inline button that opens a URL.

        Used for broker OAuth authentication (Kite Connect).

        Args:
            message: Text message to display above the button
            url: URL the button opens (broker auth URL)
            button_text: Label shown on the button
        """
        if self._stub_mode:
            logger.info(f"[TELEGRAM] {message}")
            print(f"[AUTH URL — open this in your browser]\n{url}")
            return

        try:
            import httpx

            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": button_text, "url": url}]
                    ]
                },
            }

            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=payload,
                timeout=10,
            )

            if not response.is_success:
                logger.warning(
                    f"Telegram send_auth_request failed: {response.status_code} "
                    f"{response.text}"
                )
                print(f"[AUTH URL — open this in your browser]\n{url}")
        except Exception as e:
            logger.error(f"Failed to send auth request via Telegram: {e}")
            print(f"[AUTH URL — open this in your browser]\n{url}")

    def send_approval_request(self, proposal: dict):
        """Send a trade proposal requiring approval."""
        from comms.message_templates import trade_proposal
        text = trade_proposal(
            symbol=proposal.get("symbol", ""),
            direction=proposal.get("direction", ""),
            entry_price=proposal.get("entry_price", 0),
            stop_loss=proposal.get("stop_loss", 0),
            target=proposal.get("target", 0),
            quantity=proposal.get("quantity", 0),
            bucket=proposal.get("bucket", ""),
            confidence=proposal.get("confidence", ""),
            note=proposal.get("note", ""),
        )
        self.send_message(text)

    def _publish_command(self, command: str, **kwargs):
        """Publish a command to the orchestrator channel."""
        if self.redis:
            import json
            from agents.message import AgentMessage, MessageType, Priority
            msg = AgentMessage(
                from_agent="human",
                to_agent="orchestrator",
                channel="channel:orchestrator",
                type=MessageType.COMMAND,
                priority=Priority.HIGH,
                payload={"command": command, **kwargs},
            )
            self.redis.publish("channel:orchestrator", msg.model_dump())

    # --- Command Handlers ---

    async def _cmd_status(self, update, context):
        self._publish_command("STATUS")
        await update.message.reply_text("Status request sent.")

    async def _cmd_positions(self, update, context):
        self._publish_command("POSITIONS")
        await update.message.reply_text("Positions request sent.")

    async def _cmd_halt(self, update, context):
        self._publish_command("HALT", reason="Human command via Telegram")
        await update.message.reply_text("HALT command sent. All trading stopped.")

    async def _cmd_resume(self, update, context):
        self._publish_command("RESUME")
        await update.message.reply_text("RESUME command sent.")

    async def _cmd_paper(self, update, context):
        self._publish_command("SET_MODE", mode="PAPER")
        await update.message.reply_text("Switching to PAPER mode.")

    async def _cmd_live(self, update, context):
        await update.message.reply_text(
            "WARNING: Switching to LIVE mode. "
            "Send /live_confirm to confirm."
        )

    async def _cmd_pnl(self, update, context):
        self._publish_command("PNL")
        await update.message.reply_text("P&L request sent.")

    async def _cmd_strategy(self, update, context):
        self._publish_command("STRATEGY")
        await update.message.reply_text("Strategy request sent.")

    async def _cmd_report(self, update, context):
        self._publish_command("REPORT")
        await update.message.reply_text("Report request sent.")

    async def _cmd_agents(self, update, context):
        self._publish_command("AGENTS_STATUS")
        await update.message.reply_text("Agent status request sent.")

    async def _cmd_approve(self, update, context):
        args = context.args
        proposal_id = args[0] if args else ""
        self._publish_command("APPROVE", proposal_id=proposal_id)
        await update.message.reply_text(f"Approved: {proposal_id}")

    async def _cmd_reject(self, update, context):
        args = context.args
        proposal_id = args[0] if args else ""
        self._publish_command("REJECT", proposal_id=proposal_id)
        await update.message.reply_text(f"Rejected: {proposal_id}")

    async def _cmd_authenticate(self, update, context):
        self._publish_command("AUTHENTICATE")
        await update.message.reply_text(
            "Re-authentication requested. Check for auth button shortly."
        )

    async def _cmd_optimizer(self, update, context):
        self._publish_command("OPTIMIZER")
        await update.message.reply_text(
            "Optimizer meeting requested. Guards will be checked."
        )

    async def _cmd_catchup(self, update, context):
        self._publish_command("CATCHUP")
        await update.message.reply_text(
            "Catchup started: auth → wake agents → morning strategy. "
            "Signal loop will resume on next 5-min tick."
        )

    async def _cmd_dryrun(self, update, context):
        self._publish_command("DRY_RUN")
        await update.message.reply_text(
            "Dry run started: morning graph → signal loop → position monitor. "
            "Will report results when complete."
        )

    async def _cmd_lt_scan(self, update, context):
        self._publish_command("LT_SCAN")
        await update.message.reply_text(
            "LT scan started. Will message you if opportunity found."
        )

    async def _handle_text(self, update, context):
        """Handle plain text responses (YES/NO/EDIT)."""
        text = update.message.text.strip().upper()
        if text in ("YES", "NO", "EDIT"):
            self._publish_command("MORNING_RESPONSE", response=text)
            await update.message.reply_text(f"Response recorded: {text}")
        else:
            await update.message.reply_text(
                "Commands: /status /positions /halt /resume /pnl /strategy\n"
                "/catchup — run full morning sequence manually\n"
                "/dryrun — test full pipeline (morning → signals → monitor)\n"
                "/authenticate — re-authenticate Kite\n"
                "/lt_scan — run LT investment opportunity scan\n"
                "Or reply YES/NO/EDIT to pending proposals."
            )
