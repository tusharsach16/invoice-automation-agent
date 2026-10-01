"""
LLM planner: converts a plain-English goal into a validated ExecutionPlan.

Called exactly once per agent run. All subsequent execution is deterministic.
The LLM never sees individual invoices and never controls browser interactions.

Uses Gemini's structured JSON output mode to reduce parse failures.
"""
import json
import logging
import random
import time
from google import genai
from google.genai import types

from app.core.config import settings
from app.schemas.agent import ExecutionPlan, FilterConfig, ALLOWED_ACTIONS

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY_S = 1.0
_MAX_DELAY_S = 8.0

_SYSTEM_PROMPT = f"""
You are a billing assistant that converts business goals into structured execution plans.

You must respond with a JSON object matching this exact schema:
{{
  "filters": {{
    "min_amount": <number or null>,
    "max_amount": <number or null>,
    "vendor_ids": <list of ints or null>,
    "invoice_numbers": <list of strings or null>
  }},
  "actions": <list of strings>
}}

Allowed actions (use only these exact strings):
{sorted(ALLOWED_ACTIONS)}

Rules:
- If the goal explicitly mentions one or more specific invoice numbers (e.g. "INV-2024-004", "invoice INV-2024-001"), extract them into the "invoice_numbers" list.
- Use "read_invoices" to fetch source invoices matching the filters.
- Use "check_purchase_orders" to verify each invoice against its PO.
- Use "create_invoices" to enter approved invoices into the ERP.
- Use "flag_mismatches" to mark invoices with PO discrepancies.
- Use "generate_report" to produce the final summary.
- Do not invent actions. Do not include explanations. Only output valid JSON.
""".strip()


def _is_transient_error(exc: Exception) -> bool:
    """Return True if the exception represents a transient failure eligible for retry."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        if code in (429, 500, 502, 503, 504):
            return True
        if 400 <= code < 500 and code != 429:
            return False

    err_str = str(exc).lower()
    # Explicit non-retry status/codes
    if any(non_retry in err_str for non_retry in ["400", "401", "403", "404", "invalid_argument", "not_found", "unauthenticated", "permission_denied"]):
        return False

    # Transient error indicators
    if any(transient in err_str for transient in ["503", "429", "unavailable", "resource_exhausted", "high demand", "rate limit", "deadline_exceeded", "timeout"]):
        return True

    return False


def plan(goal: str) -> ExecutionPlan:
    """
    Call the LLM once and return a validated ExecutionPlan.

    Includes bounded retries with exponential backoff and jitter for transient errors (503, 429).
    Non-transient errors (400, 401, 403, 404) fail immediately.
    Raises ValueError if the LLM response cannot be parsed or fails validation.
    The caller (executor) should catch this and mark the run as failed.
    """
    if not settings.google_api_key:
        raise ValueError("GOOGLE_API_KEY is not configured")

    client = genai.Client(api_key=settings.google_api_key)
    prompt = f"Business goal: {goal}\n\nGenerate the execution plan JSON."
    response = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            logger.info("Calling LLM planner (model=%s, attempt=%d/%d)", settings.llm_model, attempt + 1, _MAX_RETRIES + 1)
            response = client.models.generate_content(
                model=settings.llm_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.0,   # deterministic output
                ),
            )
            break
        except Exception as exc:
            if attempt < _MAX_RETRIES and _is_transient_error(exc):
                delay = min(_MAX_DELAY_S, (_BASE_DELAY_S * (2 ** attempt))) + random.uniform(0.1, 0.5)
                logger.warning(
                    "Gemini planner call failed with transient error (attempt %d/%d): %s. Retrying in %.2fs...",
                    attempt + 1, _MAX_RETRIES + 1, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.error("Gemini planner call failed permanently (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES + 1, exc)
                raise ValueError(f"Gemini API request failed: {exc}") from exc

    raw = response.text.strip() if response and response.text else ""
    logger.debug("LLM raw response: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON: {raw[:200]}") from exc

    # Pydantic validates structure, allowlist, and action sequence. Any invalid plan is rejected here.
    return ExecutionPlan(
        filters=FilterConfig(**data.get("filters", {})),
        actions=data.get("actions", []),
    )
