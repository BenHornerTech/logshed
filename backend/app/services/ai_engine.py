"""
AI Engine Service for LogShed.
Provides unified client abstraction for Google Gemini and OpenAI / OpenAI-compatible
endpoints with prompt construction, structured parsing, and audit logging.

Uses the google-genai SDK for Gemini and the openai SDK for OpenAI-compatible
endpoints, as required by SPEC §4.2 and AGENTS.md.
"""

import logging
import re
from typing import Any, Optional

from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI

from app.core.config import is_debug_or_dev
from app.core.redactor import redact

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.
Review the following redacted server/container logs and provide a structured diagnosis in Markdown format.

Your response MUST include the following three sections with exact headers:
## Summary
A concise 1-2 sentence overview of the issue.

## Root Cause
A detailed explanation of why the event or failure occurred based on the log evidence.

## Actionable Remediation
Step-by-step commands, configuration fixes, or debugging steps to resolve the issue."""

SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT


def build_analysis_prompt(
    source_alias: str,
    app_name: str,
    redacted_logs: str,
    log_count: int,
    user_context: Optional[str] = None,
    host_notes: Optional[str] = None,
) -> str:
    """
    Construct the full prompt payload sent to the LLM.
    Combines host/container metadata, optional host notes, chronological redacted logs, and optional operator notes.
    """
    parts = [
        "### System Metadata",
        f"- Host / Source: {source_alias}",
        f"- Container / Service: {app_name}",
        f"- Total Selected Logs: {log_count}",
    ]

    if host_notes and host_notes.strip():
        parts.append(f"- Host Notes: {host_notes.strip()}")

    parts.append("")

    if user_context and user_context.strip():
        parts.extend([
            "### Situational Context from Operator",
            user_context.strip(),
            "",
        ])

    parts.extend([
        "### Redacted Log Stream (Chronological)",
        "```",
        redacted_logs.strip(),
        "```",
        "",
        "Please review these logs and provide Summary, Root Cause, and Actionable Remediation.",
    ])

    return "\n".join(parts)


def parse_structured_ai_response(text: str) -> tuple[str, str, str]:
    """
    Parse a Markdown AI response into (summary, root_cause, remediation).
    Falls back gracefully if exact section headers are not strictly formatted.
    """
    clean_text = text.strip()

    # Pattern matching for ## Summary, ## Root Cause, ## Actionable Remediation
    summary_pattern = r"##\s*Summary\s*([\s\S]*?)(?=##\s*Root\s*Cause|$)"
    root_cause_pattern = r"##\s*Root\s*Cause(?:\s*Analysis)?\s*([\s\S]*?)(?=##\s*Actionable\s*Remediation|$)"
    remediation_pattern = r"##\s*Actionable\s*Remediation\s*([\s\S]*?)$"

    summary_match = re.search(summary_pattern, clean_text, re.IGNORECASE)
    root_cause_match = re.search(root_cause_pattern, clean_text, re.IGNORECASE)
    remediation_match = re.search(remediation_pattern, clean_text, re.IGNORECASE)

    summary = summary_match.group(1).strip() if summary_match else ""
    root_cause = root_cause_match.group(1).strip() if root_cause_match else ""
    remediation = remediation_match.group(1).strip() if remediation_match else ""

    # Fallback if regex parsing missed sections
    if not summary and not root_cause and not remediation:
        paragraphs = [p.strip() for p in clean_text.split("\n\n") if p.strip()]
        if paragraphs:
            summary = paragraphs[0]
            if len(paragraphs) > 1:
                root_cause = "\n\n".join(paragraphs[1:-1]) if len(paragraphs) > 2 else paragraphs[1]
                remediation = paragraphs[-1] if len(paragraphs) > 2 else "Inspect service logs and system metrics."
            else:
                root_cause = "Refer to summary for diagnosis."
                remediation = "Inspect service logs and system metrics."
        else:
            summary = clean_text or "No summary generated."
            root_cause = "No root cause details returned."
            remediation = "No remediation steps provided."

    return summary, root_cause, remediation


def _get_token_count(obj: Any, *attr_names: str) -> int:
    """Safely extracts an integer token count from an SDK response/usage object."""
    if obj is None:
        return 0
    for name in attr_names:
        val = getattr(obj, name, None)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        if isinstance(val, (float, str)):
            try:
                return int(val)
            except ValueError:
                pass
    return 0


async def dispatch_gemini_request(
    api_key: str,
    model: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    timeout: float = 60.0,
) -> tuple[str, int, int, int, int]:
    """
    Dispatch request to Google Gemini API via the google-genai SDK.
    Uses client.aio for async operations with API key authentication.
    """
    if not api_key:
        raise ValueError("Google Gemini API key is not configured in settings.")

    effective_system_prompt = (system_prompt and system_prompt.strip()) or DEFAULT_SYSTEM_PROMPT

    try:
        client = genai.Client(api_key=api_key)

        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=effective_system_prompt,
                temperature=0.2,
            ),
        )

        text = response.text
        if not text:
            raise RuntimeError("Gemini API returned empty response text.")

        # Extract token usage from response metadata across all Google GenAI SDK versions
        usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
        tokens_in = 0
        tokens_out = 0
        tokens_thoughts = 0
        tokens_used = 0
        if usage:
            tokens_in = _get_token_count(usage, "prompt_token_count", "total_input_tokens", "input_tokens")
            tokens_out = _get_token_count(usage, "candidates_token_count", "total_output_tokens", "output_tokens")
            tokens_thoughts = _get_token_count(usage, "thoughts_token_count", "total_thought_tokens", "thought_tokens", "thinking_tokens")
            tokens_used = _get_token_count(usage, "total_token_count", "total_tokens") or (tokens_in + tokens_out + tokens_thoughts)
            if tokens_thoughts == 0 and tokens_used > (tokens_in + tokens_out):
                tokens_thoughts = tokens_used - (tokens_in + tokens_out)

        if tokens_used == 0:
            # Fallback estimation if usage metadata is unavailable
            tokens_in = max(1, len(prompt) // 4)
            tokens_out = max(1, len(text) // 4)
            tokens_used = tokens_in + tokens_out

        return text, tokens_in, tokens_out, tokens_thoughts, tokens_used

    except ValueError:
        raise
    except Exception as e:
        clean_err = str(redact(str(e)[:500]))
        err_msg = f"Gemini API error: {clean_err}"
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise RuntimeError(err_msg)


async def dispatch_openai_request(
    api_key: str,
    model: str,
    prompt: str,
    base_url: Optional[str] = None,
    system_prompt: Optional[str] = None,
    timeout: float = 60.0,
) -> tuple[str, int, int, int, int]:
    """
    Dispatch request to OpenAI or OpenAI-compatible endpoint (e.g. Ollama, vLLM, LocalAI)
    via the openai SDK with configurable base_url.
    """
    effective_base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
    effective_system_prompt = (system_prompt and system_prompt.strip()) or DEFAULT_SYSTEM_PROMPT

    try:
        client = AsyncOpenAI(
            api_key=api_key or "not-needed",
            base_url=effective_base_url,
            timeout=timeout,
        )

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": effective_system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        choice = response.choices[0]
        text = choice.message.content
        if not text:
            raise RuntimeError("OpenAI endpoint returned empty response content.")

        # Extract token usage
        tokens_in = 0
        tokens_out = 0
        tokens_thoughts = 0
        tokens_used = 0
        if response.usage:
            tokens_in = _get_token_count(response.usage, "prompt_tokens", "prompt_token_count", "input_tokens")
            tokens_out = _get_token_count(response.usage, "completion_tokens", "candidates_token_count", "output_tokens")
            details = getattr(response.usage, "completion_tokens_details", None)
            tokens_thoughts = _get_token_count(details, "reasoning_tokens", "thought_tokens", "thinking_tokens") if details else 0
            if tokens_thoughts == 0:
                tokens_thoughts = _get_token_count(response.usage, "reasoning_tokens", "thoughts_token_count")
            tokens_used = _get_token_count(response.usage, "total_tokens", "total_token_count") or (tokens_in + tokens_out + tokens_thoughts)
            if tokens_thoughts == 0 and tokens_used > (tokens_in + tokens_out):
                tokens_thoughts = tokens_used - (tokens_in + tokens_out)
        if tokens_used == 0:
            tokens_in = max(1, len(prompt) // 4)
            tokens_out = max(1, len(text) // 4)
            tokens_used = tokens_in + tokens_out

        return text, tokens_in, tokens_out, tokens_thoughts, tokens_used

    except ValueError:
        raise
    except Exception as e:
        clean_err = str(redact(str(e)[:500]))
        err_msg = f"OpenAI endpoint error: {clean_err}"
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise RuntimeError(err_msg)


MAX_LOG_TEXT_CHARS = 100_000
TRUNCATION_NOTICE = "[... Truncated older logs to fit token budget ...]\n"


def truncate_logs_to_budget(logs_text: str, max_chars: int = MAX_LOG_TEXT_CHARS) -> str:
    """
    Defensively truncate concatenated log text if it exceeds max_chars.
    Retains the most recent logs fitting within the budget, prepending an explicit notice.
    """
    if len(logs_text) <= max_chars:
        return logs_text

    budget = max_chars - len(TRUNCATION_NOTICE)
    if budget <= 0:
        return TRUNCATION_NOTICE.strip()

    truncated = logs_text[-budget:]
    nl_idx = truncated.find("\n")
    if nl_idx != -1 and nl_idx < 500:
        truncated = truncated[nl_idx + 1:]

    return TRUNCATION_NOTICE + truncated


async def execute_ai_analysis(
    provider: str,
    model: str,
    api_key: str,
    base_url: Optional[str],
    source_alias: str,
    app_name: str,
    redacted_logs: str,
    log_count: int,
    user_context: Optional[str] = None,
    host_notes: Optional[str] = None,
    prompt_override: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> tuple[str, str, str, str, str, int, int, int, int]:
    """
    Unified entrypoint to run on-demand AI analysis.
    Returns (summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used).
    """
    redacted_logs = truncate_logs_to_budget(redacted_logs)

    if prompt_override and prompt_override.strip():
        prompt = prompt_override.strip()
    else:
        prompt = build_analysis_prompt(
            source_alias=source_alias,
            app_name=app_name,
            redacted_logs=redacted_logs,
            log_count=log_count,
            user_context=user_context,
            host_notes=host_notes,
        )

    norm_provider = (provider or "gemini").lower()

    if norm_provider == "gemini":
        raw_text, tokens_in, tokens_out, tokens_thoughts, tokens_used = await dispatch_gemini_request(
            api_key=api_key,
            model=model or "gemini-2.5-flash",
            prompt=prompt,
            system_prompt=system_prompt,
        )
    elif norm_provider in ("openai", "openai_compatible"):
        raw_text, tokens_in, tokens_out, tokens_thoughts, tokens_used = await dispatch_openai_request(
            api_key=api_key,
            model=model or ("gpt-4o" if norm_provider == "openai" else "llama3.2"),
            prompt=prompt,
            base_url=base_url,
            system_prompt=system_prompt,
        )
    else:
        raise ValueError(f"Unsupported AI provider: {provider}")

    summary, root_cause, remediation = parse_structured_ai_response(raw_text)
    return summary, root_cause, remediation, raw_text, prompt, tokens_in, tokens_out, tokens_thoughts, tokens_used
