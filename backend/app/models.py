"""
Pydantic v2 schemas and models for LogShed API.
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Auth Models
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    """Payload for first-run admin account creation."""
    password: str = Field(..., min_length=8, description="Admin password (min 8 characters)")


class LoginRequest(BaseModel):
    """Payload for session login."""
    password: str = Field(..., description="Admin password")


class PasswordChangeRequest(BaseModel):
    """Payload for changing admin password."""
    current_password: str = Field(..., min_length=1, description="Current password")
    new_password: str = Field(..., min_length=8, description="New password (min 8 characters)")


class AuthStatusResponse(BaseModel):
    """Authentication and setup status."""
    setup_required: bool
    authenticated: bool


class MessageResponse(BaseModel):
    """Generic status response."""
    status: str = "ok"
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Log Models
# ---------------------------------------------------------------------------

class LogEntry(BaseModel):
    """Single structured log record."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    received_at: str
    source_ip: str
    source_alias: str
    app_name: str
    facility: int
    severity: int
    message: str
    raw: str


class LogListResponse(BaseModel):
    """Paginated logs response."""
    logs: list[LogEntry]
    total: int
    limit: int
    offset: int


class LogContextResponse(BaseModel):
    """Surrounding context lines for a given log ID."""
    target_id: int
    logs: list[LogEntry]


# ---------------------------------------------------------------------------
# Settings Models
# ---------------------------------------------------------------------------

class SettingsResponse(BaseModel):
    """Application runtime configuration response with masked secrets."""
    ai_provider: str = "gemini"
    ai_model: str = "gemini-2.5-flash"
    ai_api_key: str = ""
    ai_base_url: Optional[str] = None
    retention_days: int = 30
    has_ai_api_key: bool = False


class SettingsUpdateRequest(BaseModel):
    """Payload for updating runtime settings."""
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    retention_days: Optional[int] = Field(None, ge=1, le=30)


# ---------------------------------------------------------------------------
# Host Aliases Models
# ---------------------------------------------------------------------------

class HostAliasCreate(BaseModel):
    """Payload for creating or updating a host alias."""
    ip: str = Field(..., min_length=1, description="IP address or subnet key")
    alias: str = Field(..., min_length=1, description="Human-readable host name")
    notes: Optional[str] = None


class HostAliasResponse(BaseModel):
    """Host alias mapping details."""
    ip: str
    alias: str
    notes: Optional[str] = None
    created_at: str


# ---------------------------------------------------------------------------
# System, Health & Storage Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Container and ingestion health status."""
    status: str
    db: str
    queue_depth: int
    dropped_logs: int


class StorageMetricItem(BaseModel):
    """Storage metrics snapshot."""
    recorded_at: str
    db_size_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int
    total_logs_count: int


class StorageOverviewResponse(BaseModel):
    """Storage overview with current metrics and history."""
    db_size_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int
    total_logs_count: int
    history: list[StorageMetricItem]


class PruneResponse(BaseModel):
    """Response returned after manual retention prune."""
    status: str = "ok"
    deleted_logs: int
    deleted_metrics: int
    metrics: StorageMetricItem



# ---------------------------------------------------------------------------
# AI Models
# ---------------------------------------------------------------------------

class AiPreviewRequest(BaseModel):
    """Payload for generating sanitized AI prompt preview."""
    log_ids: list[int] = Field(..., min_length=1)


class AiPreviewResponse(BaseModel):
    """Preview of sanitized prompt and token estimate."""
    sanitized_prompt: str
    estimated_tokens: int
    provider: str
    model: str
    log_count: int
    source_alias: str
    app_name: str


class AiAnalyzeRequest(BaseModel):
    """Payload for triggering on-demand AI analysis."""
    log_ids: list[int] = Field(..., min_length=1)
    user_context: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class AiAnalyzeResponse(BaseModel):
    """Structured root-cause diagnosis returned from LLM."""
    summary: str
    root_cause: str
    remediation: str
    model_used: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thoughts: int = 0
    tokens_used: int
    audit_id: Optional[int] = None


class AiAuditItem(BaseModel):
    """Single historical AI analysis record."""
    id: int
    timestamp: str
    source_alias: str
    app_name: str
    log_count: int
    user_context: Optional[str] = None
    model: str
    prompt_sent: str
    response_text: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thoughts: int = 0
    tokens_used: int


class AiAuditListResponse(BaseModel):
    """Paginated list of historical AI analysis audits."""
    items: list[AiAuditItem]
    total: int


class AiAuditDeleteResponse(BaseModel):
    """Status response for deleting AI audit records."""
    status: str = "ok"
    deleted_id: Optional[int] = None
    deleted_count: Optional[int] = None
