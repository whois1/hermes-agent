from gateway.run import GatewayRunner


def _effort(message: str) -> str:
    config, notice = GatewayRunner._auto_reasoning_for_message(message)
    assert notice == f"🧠 {config['effort']}"
    return config["effort"]


def test_auto_reasoning_does_not_match_fix_inside_prefix():
    message = "Photo, categories make it noisy; maybe remove category and prefix signal instead? thoughts?"

    assert _effort(message) == "medium"


def test_auto_reasoning_still_matches_fix_as_word():
    assert _effort("fix gateway auth bug") == "high"


def test_auto_reasoning_matches_phrases_as_phrases():
    assert _effort("please resetgit the broken branch") == "low"
    assert _effort("please git reset the broken branch") == "high"
    assert _effort("set up the preferences") == "medium"


def test_strip_leading_reasoning_effort_label():
    assert GatewayRunner._strip_leading_reasoning_effort_labels("🧠 medium\n\nBody") == "Body"
    assert GatewayRunner._strip_leading_reasoning_effort_labels("🧠 high\n🧠 medium\n\nBody") == "Body"
    assert GatewayRunner._strip_leading_reasoning_effort_labels("No label\n\nBody") == "No label\n\nBody"
