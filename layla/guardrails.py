"""
Security guardrails for the Layla concierge
============================================================================
This module is the security centrepiece of the project. It implements two
defensive layers as ADK callbacks, plus the pure detection functions behind
them (kept pure so they are unit-testable without an ADK runtime — see
../tests/test_guardrails.py).

Threat model (the three things a concierge agent must resist):
  1. PROMPT INJECTION / JAILBREAK
     A user (or text returned by a tool) tries to override Layla's
     instructions: "ignore previous instructions", "you are now DAN", etc.
     -> Caught by `input_guardrail` (an ADK `before_model_callback`).

  2. CONFUSED DEPUTY
     The agent is legitimately allowed to call tools, and an attacker tricks
     it into using that authority to do something it shouldn't — e.g. asking
     the order tool to send a customer's saved address to an arbitrary
     destination. The agent isn't malicious; it's a confused deputy.
     -> Caught by `tool_guardrail` (an ADK `before_tool_callback`) which
        inspects *tool arguments*, not just the user's words.

  3. PII EXFILTRATION
     Personal data (phone, email, full address, card numbers) leaking out
     through a tool argument or into model output.
     -> `scan_args_for_exfiltration` blocks risky destinations and
        `redact_pii` masks anything that should never be echoed back.

These map directly to the EXFILTRATION and CONFUSED_DEPUTY attack predicates
used in agent red-teaming: the most reliable place to stop them is at the
*tool boundary*, by checking where data is allowed to flow, rather than by
hoping the model "behaves". The order tool here (`tools.py`) deliberately
takes the destination as a separate argument so the guardrail can validate it
*before* the model is trusted with it.
============================================================================
"""

import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 1. Prompt-injection / jailbreak detection
# ---------------------------------------------------------------------------
# These patterns are intentionally conservative: they target the structural
# giveaways of an override attempt rather than ordinary fashion vocabulary, to
# keep false positives low. In production you would pair this with a trained
# classifier; regex is the transparent, auditable first line of defence.
_INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions?\b",
    r"\bdisregard\s+(your|the|all)\s+(system\s+)?(prompt|instructions?|rules?)\b",
    r"\byou\s+are\s+now\b.*\b(dan|developer\s+mode|unrestricted|jailbroken)\b",
    r"\bact\s+as\s+(if\s+you\s+are\s+)?an?\s+\w+\s+with\s+no\s+(rules|restrictions|filter)\b",
    r"\b(reveal|print|show|repeat)\s+(your\s+)?(system\s+prompt|instructions?|hidden\s+prompt)\b",
    r"\bnew\s+(system\s+)?instructions?\s*:",
    r"\boverride\s+(your\s+)?(safety|security|guardrails?)\b",
]


def detect_prompt_injection(text: str) -> Optional[str]:
    """Return a short reason string if `text` looks like an injection attempt,
    else None. Returning a *reason* (not just True) makes the audit log useful.
    """
    if not text:
        return None
    lowered = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return f"input matched injection pattern: {pattern}"
    return None


# ---------------------------------------------------------------------------
# 2. PII detection & redaction
# ---------------------------------------------------------------------------
# Patterns tuned for a Pakistani e-commerce context (CNIC, local mobile
# numbers) plus the universal ones (email, card-like number sequences).
_PII_PATTERNS = {
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "phone": r"\b(?:\+92|0)\s?3\d{2}[\s-]?\d{7}\b",          # PK mobile
    "cnic": r"\b\d{5}[-\s]?\d{7}[-\s]?\d\b",                  # PK national ID
    "card": r"\b(?:\d[ -]?){13,16}\b",                       # card-like run
}


def find_pii(text: str) -> list[str]:
    """Return the list of PII *types* found in `text` (no values, for safe logs)."""
    if not text:
        return []
    found = []
    for label, pattern in _PII_PATTERNS.items():
        if re.search(pattern, text):
            found.append(label)
    return found


def redact_pii(text: str) -> str:
    """Mask any PII in `text` so it can never be echoed back to the model or UI."""
    if not text:
        return text
    redacted = text
    for label, pattern in _PII_PATTERNS.items():
        redacted = re.sub(pattern, f"[REDACTED_{label.upper()}]", redacted)
    return redacted


# ---------------------------------------------------------------------------
# 3. Confused-deputy / exfiltration check on TOOL ARGUMENTS
# ---------------------------------------------------------------------------
# An allow-list is safer than a deny-list: we explicitly enumerate where the
# order tool is permitted to deliver. Anything else (a webhook, an attacker
# email, a pastebin URL) is refused even if the model was talked into it.
_ALLOWED_DELIVERY_CHANNELS = {"saved_address", "in_app", "store_pickup"}

# Tools that are allowed to receive PII at all. Everything else must not.
_PII_PERMITTED_TOOLS = {"place_order", "update_delivery_details"}


def scan_args_for_exfiltration(tool_name: str, args: dict[str, Any]) -> Optional[str]:
    """Inspect a pending tool call and return a reason to block it, or None.

    This is the confused-deputy stopgap: it reasons about *where data is going*,
    independent of how persuasive the user's phrasing was.
    """
    # (a) Validate any explicit delivery destination against the allow-list.
    destination = str(args.get("delivery_channel", "") or "").lower().strip()
    if destination and destination not in _ALLOWED_DELIVERY_CHANNELS:
        return (
            f"tool '{tool_name}' asked to deliver to disallowed channel "
            f"'{destination}'"
        )

    # (b) URLs / webhooks inside arguments are a classic exfiltration vector.
    for key, value in args.items():
        if isinstance(value, str) and re.search(r"https?://|webhook|\bcurl\b", value, re.I):
            return f"tool '{tool_name}' argument '{key}' contains an external endpoint"

    # (c) PII appearing in a tool that has no business receiving it.
    if tool_name not in _PII_PERMITTED_TOOLS:
        for key, value in args.items():
            if isinstance(value, str) and find_pii(value):
                return f"tool '{tool_name}' argument '{key}' carries PII it should not receive"

    return None


# ===========================================================================
# ADK callback wrappers
# ---------------------------------------------------------------------------
# These thin functions adapt the pure logic above to ADK's callback signatures.
# Imports are done lazily inside the functions so that the unit tests (which
# only exercise the pure helpers) don't require google-adk to be installed.
# ===========================================================================

_BLOCK_MESSAGE = (
    "I'm sorry, but I can't help with that request. I'm Layla, here to help you "
    "shop and style with Maryam B. — ask me about outfits, fabrics, or your order."
)


def input_guardrail(callback_context, llm_request):
    """ADK `before_model_callback`.

    Runs *before* every model call. If the latest user turn looks like an
    injection attempt, we short-circuit by returning an LlmResponse, so the
    model is never even asked. Returning None lets the request proceed normally.
    """
    from google.adk.models import LlmResponse
    from google.genai import types

    # Pull the most recent user text out of the request contents.
    user_text = ""
    try:
        for content in reversed(llm_request.contents or []):
            if getattr(content, "role", None) == "user":
                user_text = " ".join(
                    part.text for part in content.parts if getattr(part, "text", None)
                )
                break
    except Exception:
        # Fail open on parsing issues, but never fail open on a positive match.
        user_text = ""

    reason = detect_prompt_injection(user_text)
    if reason:
        # In a real deployment, emit `reason` to your audit/observability sink.
        print(f"[guardrail] BLOCKED input — {reason}")
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text=_BLOCK_MESSAGE)],
            )
        )
    return None  # allow the model call to proceed


def tool_guardrail(tool, args, tool_context):
    """ADK `before_tool_callback`.

    Runs *before* any tool executes. Returning a dict short-circuits the tool
    and that dict becomes the tool result; returning None lets it run.
    This is where confused-deputy and exfiltration attempts are actually stopped.
    """
    tool_name = getattr(tool, "name", str(tool))
    reason = scan_args_for_exfiltration(tool_name, args or {})
    if reason:
        print(f"[guardrail] BLOCKED tool call — {reason}")
        return {
            "status": "blocked",
            "reason": "This action was blocked by a security policy.",
        }
    return None  # allow the tool call to proceed
