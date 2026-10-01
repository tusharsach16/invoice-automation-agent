"""
LLM planner: converts a plain-English goal into a validated ExecutionPlan.

Called exactly once per agent run. All subsequent execution is deterministic.
The LLM never sees individual invoices and never controls browser interactions.

Uses Gemini's structured JSON output mode to reduce parse failures.
google.generativeai is imported lazily (inside plan()) to avoid loading grpc
at server startup — grpc's DLL may be blocked in restricted environments.
"""
import json
import logging

from app.core.config import settings
from app.schemas.agent import ExecutionPlan, FilterConfig, ALLOWED_ACTIONS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""
You are a billing assistant that converts business goals into structured execution plans.

You must respond with a JSON object matching this exact schema:
{{
  "filters": {{
    "min_amount": <number or null>,
    "max_amount": <number or null>,
    "vendor_ids": <list of ints or null>
  }},
  "actions": <list of strings>
}}

Allowed actions (use only these exact strings):
{sorted(ALLOWED_ACTIONS)}

Rules:
- Use "read_invoices" to fetch source invoices matching the filters.
- Use "check_purchase_orders" to verify each invoice against its PO.
- Use "create_invoices" to enter approved invoices into the ERP.
- Use "flag_mismatches" to mark invoices with PO discrepancies.
- Use "generate_report" to produce the final summary.
- Do not invent actions. Do not include explanations. Only output valid JSON.
""".strip()


def plan(goal: str) -> ExecutionPlan:
    """
    Call the LLM once and return a validated ExecutionPlan.

    Raises ValueError if the LLM response cannot be parsed or fails validation.
    The caller (executor) should catch this and mark the run as failed.
    """
    # Lazy import: grpc (a transitive dependency of google-generativeai)
    # loads a native DLL that may be blocked by Application Control policies.
    # Deferring to call-time means the ERP server starts even without LLM access.
    import google.generativeai as genai  # noqa: PLC0415

    if not settings.google_api_key:
        raise ValueError("GOOGLE_API_KEY is not configured")

    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(
        model_name=settings.llm_model,
        system_instruction=_SYSTEM_PROMPT,
    )

    prompt = f"Business goal: {goal}\n\nGenerate the execution plan JSON."
    logger.info("Calling LLM planner (model=%s)", settings.llm_model)

    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0,   # deterministic output
        ),
    )


    raw = response.text.strip()
    logger.debug("LLM raw response: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON: {raw[:200]}") from exc

    # Pydantic validates structure and allowlist. Any invalid plan is rejected here.
    return ExecutionPlan(
        filters=FilterConfig(**data.get("filters", {})),
        actions=data.get("actions", []),
    )
