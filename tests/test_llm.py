"""Tests for the LLM integration layer — prompt rendering, JSON parsing,
model routing, and BaseAgent.call_llm() with mocked API calls."""

from unittest.mock import MagicMock, patch

import pytest

from tools.llm import render_prompt, parse_json_response, get_chat_model


# --- Prompt rendering ---

class TestRenderPrompt:
    def test_simple_substitution(self):
        template = "Hello {name}, you are a {role}."
        result = render_prompt(template, {"name": "Alice", "role": "trader"})
        assert result == "Hello Alice, you are a trader."

    def test_missing_variable_stays(self):
        template = "Mode: {mode}, VIX: {vix}"
        result = render_prompt(template, {"mode": "PAPER"})
        assert result == "Mode: PAPER, VIX: {vix}"

    def test_numeric_variable(self):
        template = "Capital: {capital}"
        result = render_prompt(template, {"capital": 25000})
        assert result == "Capital: 25000"

    def test_empty_variables(self):
        template = "No vars here."
        assert render_prompt(template, {}) == "No vars here."

    def test_multiline_template(self):
        template = "Line 1: {a}\nLine 2: {b}"
        result = render_prompt(template, {"a": "X", "b": "Y"})
        assert result == "Line 1: X\nLine 2: Y"


# --- JSON parsing ---

class TestParseJsonResponse:
    def test_clean_json(self):
        text = '{"decision": "APPROVED", "reason": "all good"}'
        result = parse_json_response(text)
        assert result["decision"] == "APPROVED"

    def test_json_in_code_fence(self):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```\nDone.'
        result = parse_json_response(text)
        assert result["key"] == "value"

    def test_json_in_plain_fence(self):
        text = '```\n{"x": 42}\n```'
        result = parse_json_response(text)
        assert result["x"] == 42

    def test_json_with_surrounding_text(self):
        text = 'Analysis complete. {"signal_valid": true, "confidence": "HIGH"} End.'
        result = parse_json_response(text)
        assert result["signal_valid"] is True

    def test_unparseable_returns_raw(self):
        text = "This is not JSON at all"
        result = parse_json_response(text)
        assert result["_parse_error"] is True
        assert "not JSON" in result["_raw"]

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = parse_json_response(text)
        assert result["outer"]["inner"] == [1, 2, 3]


# --- Model routing ---

class TestModelRouting:
    @patch("tools.llm._get_openai_model")
    def test_orchestrator_gets_gpt4o(self, mock_get):
        mock_get.return_value = MagicMock()
        model = get_chat_model("orchestrator")
        mock_get.assert_called_with("gpt-4o")

    @patch("tools.llm._get_openai_model")
    def test_analyst_gets_gpt4o_mini(self, mock_get):
        mock_get.return_value = MagicMock()
        model = get_chat_model("analyst")
        mock_get.assert_called_with("gpt-4o-mini")

    @patch("tools.llm._get_gemini_model")
    def test_data_agent_gets_gemini(self, mock_get):
        mock_get.return_value = MagicMock()
        model = get_chat_model("data_agent")
        mock_get.assert_called_with("gemini-2.5-flash")


# --- BaseAgent.call_llm() integration ---

class TestBaseAgentCallLLM:
    @patch("tools.llm.call_llm")
    def test_call_llm_renders_and_calls(self, mock_call):
        """call_llm() should render templates and call the LLM provider."""
        mock_call.return_value = {"decision": "APPROVED"}

        # Create a concrete subclass for testing
        from agents.base_agent import BaseAgent
        from agents.message import AgentMessage

        class TestAgent(BaseAgent):
            def on_start(self): pass
            def on_stop(self): pass
            def on_message(self, msg): pass

        # Mock redis and sqlite
        agent = TestAgent.__new__(TestAgent)
        agent.agent_id = "test_agent"
        agent.prompts = {
            "SYSTEM_PROMPT": "You are a {role}.",
            "PROMPT_TEST": "Analyze {symbol} at {price}.",
        }
        agent._llm_call_count = 0
        agent._last_action = ""
        agent.logger = MagicMock()

        result = agent.call_llm("PROMPT_TEST", {
            "role": "analyst",
            "symbol": "RELIANCE",
            "price": 2800,
        })

        assert result["decision"] == "APPROVED"
        assert agent._llm_call_count == 1
        mock_call.assert_called_once()

        # Verify rendered prompts were passed
        call_kwargs = mock_call.call_args
        assert "RELIANCE" in call_kwargs.kwargs["user_prompt"]
        assert "2800" in call_kwargs.kwargs["user_prompt"]
        assert "analyst" in call_kwargs.kwargs["system_prompt"]

    @patch("tools.llm.call_llm")
    def test_call_llm_missing_prompt_raises(self, mock_call):
        from agents.base_agent import BaseAgent
        from agents.message import AgentMessage

        class TestAgent(BaseAgent):
            def on_start(self): pass
            def on_stop(self): pass
            def on_message(self, msg): pass

        agent = TestAgent.__new__(TestAgent)
        agent.agent_id = "test_agent"
        agent.prompts = {"SYSTEM_PROMPT": "system"}
        agent._llm_call_count = 0
        agent._last_action = ""
        agent.logger = MagicMock()

        with pytest.raises(ValueError, match="not found"):
            agent.call_llm("PROMPT_NONEXISTENT", {})

    @patch("tools.llm.call_llm")
    def test_call_llm_expect_json_false(self, mock_call):
        mock_call.return_value = "Plain text response"

        from agents.base_agent import BaseAgent

        class TestAgent(BaseAgent):
            def on_start(self): pass
            def on_stop(self): pass
            def on_message(self, msg): pass

        agent = TestAgent.__new__(TestAgent)
        agent.agent_id = "test_agent"
        agent.prompts = {
            "SYSTEM_PROMPT": "You are a bot.",
            "PROMPT_TEXT": "Say hello.",
        }
        agent._llm_call_count = 0
        agent._last_action = ""
        agent.logger = MagicMock()

        result = agent.call_llm("PROMPT_TEXT", {}, expect_json=False)
        assert result == "Plain text response"
