# guardrails.py — post-processing safety checks for every agent output before storage/display.
# This module adds warnings and redactions, but never blocks output delivery.

import re
from typing import Dict, List, Tuple

# This caveat is appended to high-stakes outputs so users know to verify before acting.
LEGAL_CAVEAT = "\n\n---\nAI-generated summary. Review with a qualified professional before acting."

# Common legal keywords used to detect potentially factual legal claims in a sentence.
LEGAL_TERMS = (
    "act",
    "regulation",
    "section",
    "clause",
    "gdpr",
    "ombudsman",
    "consumer rights",
    "law",
    "statutory",
)

# Simple vendor hint words so business/entity mentions count as factual claims.
VENDOR_HINTS = ("ltd", "limited", "plc", "llp", "bank", "insurance", "broadband", "telecom")

# Citation formats we accept as "grounded" markers.
CITATION_PATTERN = re.compile(r"\[(?:\d+|Source)\]")

# Numeric/date patterns used to detect factual statements.
NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\b")
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)


# Decide whether one sentence looks like a factual claim that should have a citation marker.
def _is_factual_claim(sentence: str) -> bool:
    lower_sentence = sentence.lower()

    # Numbers often indicate factual claims (amounts, deadlines, counts, percentages).
    if NUMBER_PATTERN.search(sentence):
        return True

    # Dates are factual and should be traceable to a source.
    if DATE_PATTERN.search(sentence):
        return True

    # Legal terms usually indicate claims about rights, duties, or regulations.
    if any(term in lower_sentence for term in LEGAL_TERMS):
        return True

    # Vendor/entity mentions are treated as factual claims in this project.
    if any(hint in lower_sentence for hint in VENDOR_HINTS):
        return True

    # A simple title-case company pattern catches names like "SwiftFiber Ltd".
    if re.search(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2}\s+(?:Ltd|Limited|PLC|LLP)\b", sentence):
        return True

    return False


# Split content into sentence-like chunks for checks and annotation.
def _split_sentences(text: str) -> List[str]:
    # This split is intentionally simple: we prioritize robust behavior over linguistic perfection.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


# Find factual sentences that do not include a citation marker like [1] or [Source].
def find_uncited_claims(text: str) -> List[str]:
    offending: List[str] = []
    for sentence in _split_sentences(text):
        if _is_factual_claim(sentence) and not CITATION_PATTERN.search(sentence):
            offending.append(sentence)
    return offending


# Add legal caveat text to high-stakes output types; leave other types untouched.
def inject_legal_caveat(content: str, output_type: str) -> str:
    if output_type in {"risk_assessment", "letter_draft", "legal_finding"}:
        return content + LEGAL_CAVEAT
    return content


# Replace common UK personal identifiers and return both redacted text and what was found.
def redact_pii(content: str) -> Tuple[str, List[str]]:
    redacted_content = content
    found_items: List[str] = []

    # UK National Insurance pattern: two letters, six digits, one letter.
    ni_pattern = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b", re.IGNORECASE)
    ni_matches = ni_pattern.findall(redacted_content)
    if ni_matches:
        found_items.append("National Insurance number")
        redacted_content = ni_pattern.sub("[REDACTED-NI]", redacted_content)

    # UK sort code pattern: XX-XX-XX.
    sort_code_pattern = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
    sort_code_matches = sort_code_pattern.findall(redacted_content)
    if sort_code_matches:
        found_items.append("sort code")
        redacted_content = sort_code_pattern.sub("[REDACTED-SORT-CODE]", redacted_content)

    # Only redact 8-digit account numbers when "account" appears nearby to reduce false positives.
    account_near_pattern = re.compile(r"(?i)(account[^0-9]{0,25})(\d{8})")
    account_matches = account_near_pattern.findall(redacted_content)
    if account_matches:
        found_items.append("account number")

        # Keep surrounding words but replace just the number group.
        def _replace_account(match: re.Match) -> str:
            return f"{match.group(1)}[REDACTED-ACCOUNT]"

        redacted_content = account_near_pattern.sub(_replace_account, redacted_content)

    return redacted_content, found_items


# Check if content suggests escalation beyond this system's authority.
def check_scope_escalation(content: str) -> Tuple[bool, str]:
    escalation_phrases = (
        "take legal action",
        "sue",
        "file a police report",
        "contact the financial ombudsman",
        "go to court",
        "file a claim",
        "seek legal counsel",
    )
    lowered = content.lower()
    if any(phrase in lowered for phrase in escalation_phrases):
        return True, "This situation may require professional legal or financial advice."
    return False, ""


# Run all guardrail checks and return annotated output without blocking the user flow.
def apply_guardrails(output: Dict, output_type: str) -> Dict:
    # Ensure required structure exists even if caller sends partial dictionaries.
    content = str(output.get("content", ""))
    warnings = output.get("warnings")
    if not isinstance(warnings, list):
        warnings = []

    # 1) Citation check: mark uncited factual claims and add warning context.
    uncited_claims = find_uncited_claims(content)
    if uncited_claims:
        warnings.append(
            f"Some factual claims appear without citations: {len(uncited_claims)} sentence(s) flagged."
        )
        for sentence in uncited_claims:
            # Replace each exact sentence once so we do not over-mark repeated fragments.
            content = content.replace(sentence, f"⚠ {sentence}", 1)

    # 2) Always run caveat injection for relevant output types.
    content = inject_legal_caveat(content, output_type)

    # 3) PII redaction: redact in place and explain what was removed.
    content, pii_found = redact_pii(content)
    if pii_found:
        # Remove duplicates but keep readable wording for end users.
        unique_items = sorted(set(pii_found))
        warnings.append(f"Sensitive identifiers were redacted: {', '.join(unique_items)}.")

    # 4) Scope escalation check: flag high-risk advice language.
    should_escalate, reason = check_scope_escalation(content)
    if should_escalate:
        output["escalate"] = True
        output["escalation_reason"] = reason

    output["content"] = content
    output["warnings"] = warnings
    return output
