"""
Pydantic v2 schemas and models for LogShed API.
"""

from datetime import datetime
import ipaddress
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import DEFAULT_AI_MODEL, get_max_retention_days



# ---------------------------------------------------------------------------
# Auth Models
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    """Payload for first-run admin account creation."""
    password: str = Field(..., min_length=8, max_length=128, description="Admin password (8-128 characters)")


class LoginRequest(BaseModel):
    """Payload for session login."""
    password: str = Field(..., max_length=128, description="Admin password")


class PasswordChangeRequest(BaseModel):
    """Payload for changing admin password."""
    current_password: str = Field(..., min_length=1, max_length=128, description="Current password")
    new_password: str = Field(..., min_length=8, max_length=128, description="New password (8-128 characters)")


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


class LogFacetsResponse(BaseModel):
    """Distinct sources, apps, and bidirectional mappings across the database."""
    sources: list[str]
    apps: list[str]
    host_to_apps: dict[str, list[str]]
    app_to_hosts: dict[str, list[str]]


# ---------------------------------------------------------------------------
# Settings Models
# ---------------------------------------------------------------------------

class SettingsResponse(BaseModel):
    """Application runtime configuration response with masked secrets."""
    ai_provider: str = "gemini"
    ai_model: str = DEFAULT_AI_MODEL
    ai_fallback_models: str = ""
    ai_api_key: str = ""
    ai_base_url: Optional[str] = None
    ai_system_prompt: str = ""
    retention_days: int = 14
    max_retention_days: int = Field(default_factory=get_max_retention_days)
    retention_overridden: bool = False
    has_ai_api_key: bool = False
    internal_log_level: str = "WARNING"
    check_for_updates: bool = True

    @model_validator(mode="after")
    def clamp_retention_days(self) -> "SettingsResponse":
        if self.retention_overridden:
            self.retention_days = self.max_retention_days
        elif self.retention_days > self.max_retention_days:
            self.retention_days = self.max_retention_days
        elif self.retention_days < 1:
            self.retention_days = 1
        return self


class SettingsUpdateRequest(BaseModel):
    """Payload for updating runtime settings."""
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_fallback_models: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_system_prompt: Optional[str] = None
    retention_days: Optional[int] = Field(
        None,
        description=(
            "Log retention period in days (1 to MAX_RETENTION_DAYS, default 14)."
        ),
    )
    internal_log_level: Optional[str] = Field(
        None,
        description="Internal application log capture level (DEBUG, INFO, WARNING, ERROR, CRITICAL, DISABLED).",
    )
    check_for_updates: Optional[bool] = Field(
        None,
        description="Whether to check GHCR periodically for new stable releases.",
    )

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip()
            if not v_clean:
                return ""
            from urllib.parse import urlparse
            parsed = urlparse(v_clean)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("ai_base_url must be a valid HTTP or HTTPS URL")
            return v_clean
        return v

    @field_validator("retention_days")
    @classmethod
    def validate_retention_days(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            max_days = get_max_retention_days()
            if v < 1 or v > max_days:
                raise ValueError(f"Retention days must be between 1 and {max_days}")
        return v

    @field_validator("internal_log_level")
    @classmethod
    def validate_internal_log_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_clean = v.strip().upper()
            from app.core.config import VALID_LOG_LEVELS, to_canonical_log_level_name
            if v_clean not in VALID_LOG_LEVELS:
                valid_keys = ", ".join(sorted(VALID_LOG_LEVELS.keys()))
                raise ValueError(f"Invalid internal_log_level '{v}'. Must be one of: {valid_keys}")
            return to_canonical_log_level_name(v_clean)
        return v


SettingsUpdate = SettingsUpdateRequest



# ---------------------------------------------------------------------------
# Host Aliases Models
# ---------------------------------------------------------------------------

class HostAliasCreate(BaseModel):
    """Payload for creating or updating a host alias."""
    ip: str = Field(..., min_length=1, description="IP address or subnet key")
    alias: str = Field(..., min_length=1, description="Human-readable host name")
    notes: Optional[str] = None

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        clean = v.strip()
        if clean.lower() == "docker":
            return clean.lower()
        try:
            ipaddress.ip_address(clean)
        except ValueError:
            raise ValueError("Invalid IP address format. Expected valid IPv4 or IPv6 address.")
        return clean


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
    db: Optional[str] = None
    queue_depth: Optional[int] = None
    dropped_logs: Optional[int] = None
    ingest_rate: Optional[float] = None


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


class VersionResponse(BaseModel):
    """Application version and GHCR update availability."""
    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    check_enabled: bool = True
    checked_at: Optional[float] = None



# ---------------------------------------------------------------------------
# AI Models
# ---------------------------------------------------------------------------

class AiPreviewRequest(BaseModel):
    """Payload for generating redacted AI prompt preview."""
    log_ids: list[int] = Field(..., min_length=1, max_length=200)
    user_context: Optional[str] = None
    prompt_override: Optional[str] = None


class AiPreviewResponse(BaseModel):
    """Preview of redacted prompt and token estimate."""
    redacted_prompt: str
    estimated_tokens: int
    provider: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    log_count: int
    source_alias: str
    app_name: str
    system_prompt: str = ""


class AiDiagnosisRequest(BaseModel):
    """Payload for triggering on-demand AI diagnosis."""
    log_ids: list[int] = Field(..., min_length=1, max_length=200)
    user_context: Optional[str] = None
    prompt_override: Optional[str] = None
    system_prompt_override: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_models: Optional[list[str]] = None


class AiDiagnosisResponse(BaseModel):
    """Structured root-cause diagnosis returned from LLM."""
    summary: str
    root_cause: str
    remediation: str
    model_used: str
    fallback_used: bool = False
    fallback_attempts: list[str] = Field(default_factory=list)
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
    system_prompt: Optional[str] = None


class AiAuditListResponse(BaseModel):
    """Paginated list of historical AI analysis audits."""
    items: list[AiAuditItem]
    total: int


class AiAuditDeleteResponse(BaseModel):
    """Status response for deleting AI audit records."""
    status: str = "ok"
    deleted_id: Optional[int] = None
    deleted_count: Optional[int] = None


class AiModelInfo(BaseModel):
    """Information regarding an available model discovered from a provider."""
    id: str
    name: str
    description: Optional[str] = None
    supports_thinking: bool = False
    is_deprecated: bool = False


class AiModelsResponse(BaseModel):
    """Response payload containing available models for a provider."""
    provider: str
    models: list[AiModelInfo] = Field(default_factory=list)
    has_api_key: bool = True
    cached_at: Optional[str] = None
    is_live: bool = True
    error: Optional[str] = None

