"""
AI Engine Service for Homelab Log Hub.
Provides unified client abstraction for Google Gemini and OpenAI / OpenAI-compatible
endpoints with prompt construction, structured parsing, and audit logging.
"""

import datetime
import logging
import re
from typing import Any, Optional
import httpx

from app.core.security import decrypt_value

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.
Analyze the following sanitized server/container logs and provide a structured diagnosis in Markdown format.

Your response MUST include the following three sections with exact headers:
## Summary
A concise 1-2 sentence overview of the issue.

## Root Cause
A detailed explanation of why the event or failure occurred based on the log evidence.

## Actionable Remediation
Step-by-step commands, configuration fixes, or debugging steps to resolve the issue."""


def build_analysis_prompt(
    source_alias: str,
    app_name: str,
    sanitized_logs: str,
    log_count: int,
    user_context: Optional[str] = None,
) -> str:
    """
    Construct the full prompt payload sent to the LLM.
    Combines host/container metadata, chronological sanitized logs, and optional operator notes.
    """
    parts = [
        "### System Metadata",
        f"- Host / Source: {source_alias}",
        f"- Container / Service: {app_name}",
        f"- Total Selected Logs: {log_count}",
        "",
    ]

    if user_context and user_context.strip():
        parts.extend([
            "### Situational Context from Operator",
            user_context.strip(),
            "",
        ])

    parts.extend([
        "### Sanitized Log Stream (Chronological)",
        "```",
        sanitized_logs.strip(),
        "```",
        "",
        "Please analyze these logs and provide Summary, Root Cause, and Actionable Remediation.",
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


async def dispatch_gemini_request(
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 60.0,
) -> tuple[str, int]:
    """
    Dispatch request to Google Gemini API via HTTP POST using x-goog-api-key header.
    """
    if not api_key:
        raise ValueError("Google Gemini API key is not configured in settings.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            from app.core.sanitizer import sanitize
            clean_err = sanitize(res.text[:500])
            err_msg = f"Gemini API returned HTTP {res.status_code}: {clean_err}"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        data = res.json()
        try:
            candidate = data["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected Gemini response structure: {data}")
            raise RuntimeError(f"Unexpected response structure from Gemini API: {e}")

        # Extract tokens
        usage = data.get("usageMetadata", {})
        tokens_used = usage.get("totalTokenCount", max(1, len(prompt) // 4 + len(text) // 4))

        return text, tokens_used


async def dispatch_openai_request(
    api_key: str,
    model: str,
    prompt: str,
    base_url: Optional[str] = None,
    timeout: float = 60.0,
) -> tuple[str, int]:
    """
    Dispatch request to OpenAI or OpenAI-compatible endpoint (e.g. Ollama, vLLM, LocalAI).
    """
    effective_base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{effective_base_url}/chat/completions"

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            from app.core.sanitizer import sanitize
            clean_err = sanitize(res.text[:500])
            err_msg = f"OpenAI endpoint returned HTTP {res.status_code}: {clean_err}"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        data = res.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected OpenAI response structure: {data}")
            raise RuntimeError(f"Unexpected response structure from OpenAI endpoint: {e}")

        # Extract tokens
        usage = data.get("usage", {})
        tokens_used = usage.get("total_tokens", max(1, len(prompt) // 4 + len(text) // 4))

        return text, tokens_used


async def execute_ai_analysis(
    provider: str,
    model: str,
    api_key: str,
    base_url: Optional[str],
    source_alias: str,
    app_name: str,
    sanitized_logs: str,
    log_count: int,
    user_context: Optional[str] = None,
) -> tuple[str, str, str, str, int]:
    """
    Unified entrypoint to run on-demand AI analysis.
    Returns (summary, root_cause, remediation, raw_response, tokens_used).
    """
    prompt = build_analysis_prompt(
        source_alias=source_alias,
        app_name=app_name,
        sanitized_logs=sanitized_logs,
        log_count=log_count,
        user_context=user_context,
    )

    norm_provider = (provider or "gemini").lower()

    if norm_provider == "gemini":
        raw_text, tokens = await dispatch_gemini_request(
            api_key=api_key,
            model=model or "gemini-2.5-flash",
            prompt=prompt,
        )
    elif norm_provider in ("openai", "openai_compatible"):
        raw_text, tokens = await dispatch_openai_request(
            api_key=api_key,
            model=model or ("gpt-4o" if norm_provider == "openai" else "llama3.2"),
            prompt=prompt,
            base_url=base_url,
        )
    else:
        raise ValueError(f"Unsupported AI provider: {provider}")

    summary, root_cause, remediation = parse_structured_ai_response(raw_text)
    return summary, root_cause, remediation, raw_text, tokens
