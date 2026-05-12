from agents.guardrails import apply_guardrails


def test_legal_caveat_injected_for_letter_draft():
    result = apply_guardrails({"content": "This is a test letter.", "warnings": []}, "letter_draft")
    assert "AI-generated" in result["content"] or "professional" in result["content"]


def test_pii_redacted():
    result = apply_guardrails(
        {"content": "NI number AB123456C and account 12345678 noted.", "warnings": []},
        "letter_draft",
    )
    assert "AB123456C" not in result["content"]
    assert len(result["warnings"]) > 0


def test_scope_escalation_flagged():
    result = apply_guardrails(
        {"content": "You should take legal action against them immediately.", "warnings": []},
        "risk_assessment",
    )
    assert result.get("escalate") is True
    assert "escalation_reason" in result


def test_no_extra_warnings_for_clean_output():
    result = apply_guardrails(
        {"content": "The contract expires on 1 January 2026.", "warnings": []},
        "letter_draft",
    )
    assert isinstance(result["warnings"], list)
    assert not any("redacted" in w.lower() for w in result["warnings"])


def test_empty_content_handled_gracefully():
    result = apply_guardrails({"content": "", "warnings": []}, "letter_draft")
    assert "content" in result
    assert isinstance(result["warnings"], list)
