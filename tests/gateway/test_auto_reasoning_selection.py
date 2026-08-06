"""Gateway-only automatic reasoning policy behaviour."""

from gateway.run import (
    GatewayRunner,
    _normalize_empty_agent_response,
)


def _selection(message: str) -> tuple[dict, str]:
    config, label = GatewayRunner._auto_reasoning_for_message(message)
    assert label == f"🧠 {config['effort']}"
    return config, label


def _bare_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._session_reasoning_overrides = {}
    return runner


def test_auto_reasoning_keeps_low_for_incidental_and_simple_language():
    for message in (
        "Hello, how are you?",
        "Why is the sky blue?",
        "Should I bring an umbrella?",
        "What are your thoughts?",
        "Decode this Morse code: ... --- ...",
        "Who won the cricket Test?",
        "How many tokens are in this sentence?",
        "What is a gateway?",
    ):
        assert _selection(message)[0]["effort"] == "low"


def test_auto_reasoning_escalates_clear_multi_step_work():
    assert _selection("Compare the tradeoffs and recommend an implementation plan")[0]["effort"] == "medium"
    assert _selection("Investigate and diagnose why this service keeps failing")[0]["effort"] == "medium"


def test_auto_reasoning_uses_high_for_explicit_engineering_and_risky_config():
    for message in (
        "Implement the payment handler and add regression tests",
        "Debug the failing production deployment",
        "Change the firewall configuration to expose port 22",
        "Run sudo and update the authentication configuration",
        "Rotate the security secrets and access tokens",
    ):
        assert _selection(message)[0]["effort"] == "high"


def test_auto_reasoning_covers_ordinary_high_task_formulations():
    for message in (
        "Migrate the database schema",
        "Debug why login fails",
        "Fix the Python function",
        "Audit the authentication flow",
        "Write a Python script",
        "Add a button to the website",
        "Modify this function",
        "Review these OAuth settings",
        "Check this authentication policy",
    ):
        assert _selection(message)[0]["effort"] == "high"


def test_auto_reasoning_covers_operational_medium_tasks_without_keyword_leakage():
    for message in (
        "Set up a production deployment with monitoring and backups",
        "Plan a multi-region rollout",
        "Troubleshoot intermittent network connectivity across three services",
        "Restart three services and verify their health",
        "Help me set up this integration",
    ):
        assert _selection(message)[0]["effort"] == "medium"
    assert _selection("What is Morse code?")[0]["effort"] == "low"
    assert _selection("How many tests are in cricket?")[0]["effort"] == "low"


def test_auto_reasoning_honours_explicit_high_request():
    assert _selection("Use high reasoning and draft a reply")[0]["effort"] == "high"
    assert _selection("Please think deeply about this")[0]["effort"] == "high"


def test_exactly_one_gateway_owned_label_without_corrupting_body():
    reply = GatewayRunner._prepend_reasoning_effort_label(
        "Intro\n🧠 high\nordinary body", "🧠 high"
    )
    assert reply == "🧠 high\n\nIntro\n🧠 high\nordinary body"

    duplicate = GatewayRunner._prepend_reasoning_effort_label(
        "🧠 high\n:brain: medium\n\nAnswer", "🧠 low"
    )
    assert duplicate == "🧠 low\n\nAnswer"
    assert duplicate.count("🧠") == 1


def test_unknown_provider_effort_has_no_invented_label():
    assert GatewayRunner._reasoning_effort_notice(None) is None
    assert GatewayRunner._reasoning_effort_notice({"enabled": True}) is None
    assert GatewayRunner._prepend_reasoning_effort_label("answer", None) == "answer"


def test_delivery_labels_only_success_after_classification_and_normalisation():
    incomplete = {
        "partial": True,
        "api_calls": 1,
        "error": "Codex response remained incomplete after 3 continuation attempts",
        "final_response": "Codex response remained incomplete after 3 continuation attempts",
    }
    assert GatewayRunner._label_successful_gateway_response(
        incomplete, incomplete["final_response"], "🧠 high"
    ) == incomplete["final_response"]

    failed = {"failed": True, "error": "provider unavailable", "api_calls": 1}
    failed_text = _normalize_empty_agent_response(failed, "")
    assert not GatewayRunner._label_successful_gateway_response(
        failed, failed_text, "🧠 high"
    ).startswith("🧠")

    success = {"completed": True, "api_calls": 1}
    normalised = _normalize_empty_agent_response(success, "")
    assert GatewayRunner._label_successful_gateway_response(
        success, normalised, "🧠 low"
    ) == f"🧠 low\n\n{normalised}"


def test_session_override_wins_over_auto(monkeypatch):
    runner = _bare_runner()
    session_key = "agent:main:telegram:dm:1"
    runner._session_reasoning_overrides[session_key] = {"enabled": True, "effort": "low"}
    monkeypatch.setattr(
        "gateway.run._load_gateway_runtime_config",
        lambda: {"agent": {"reasoning_effort": "auto"}},
    )
    config, label = runner._resolve_gateway_turn_reasoning(
        session_key=session_key,
        model="test-model",
        message="Use high reasoning to debug the auth migration",
    )
    assert config == {"enabled": True, "effort": "low"}
    assert label == "🧠 low"


def test_context_local_runtime_config_and_per_model_precedence(monkeypatch):
    runner = _bare_runner()
    observed = []

    def load_context_config():
        observed.append("profile-b")
        return {
            "agent": {
                "reasoning_effort": "auto",
                "reasoning_overrides": {"pinned-model": "medium"},
            }
        }

    monkeypatch.setattr("gateway.run._load_gateway_runtime_config", load_context_config)
    config, label = runner._resolve_gateway_turn_reasoning(
        session_key="new-session",
        model="pinned-model",
        message="Use high reasoning",
    )
    assert observed == ["profile-b"]
    assert config == {"enabled": True, "effort": "medium"}
    assert label == "🧠 medium"

    config, label = runner._resolve_gateway_turn_reasoning(
        session_key="other-session",
        model="other-model",
        message="Use high reasoning",
    )
    assert config == {"enabled": True, "effort": "high"}
    assert label == "🧠 high"
