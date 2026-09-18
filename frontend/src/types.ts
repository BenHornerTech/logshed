export interface LogEntry {
  id: number;
  timestamp: string;
  received_at: string;
  source_ip: string;
  source_alias: string;
  app_name: string;
  facility: number;
  severity: number;
  message: string;
  raw: string;
}

export interface LogContextResponse {
  target_id: number;
  logs: LogEntry[];
}

export interface HostAlias {
  ip: string;
  alias: string;
  notes?: string | null;
  created_at: string;
}

export interface SystemSettings {
  ai_provider: 'gemini' | 'openai' | 'openai_compatible';
  ai_model: string;
  ai_fallback_models?: string;
  ai_api_key?: string;
  ai_base_url?: string | null;
  ai_system_prompt?: string;
  retention_days: number;
  max_retention_days?: number;
  retention_overridden?: boolean;
  internal_log_level?: string;
  check_for_updates?: boolean;
}

export type AppTab = 'stream' | 'aliases' | 'storage' | 'settings';
export interface StorageMetricsSnapshot {
  recorded_at: string;
  db_size_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  total_logs_count: number;
}

export interface StorageMetricsResponse {
  db_size_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  total_logs_count?: number;
  history: StorageMetricsSnapshot[];
}

export interface HealthResponse {
  status: string;
  db: string;
  database?: string;
  queue_depth: number;
  dropped_logs: number;
  ingest_rate: number;
}

export interface PruneResponse {
  status: string;
  deleted_logs: number;
  deleted_metrics: number;
  metrics: StorageMetricsSnapshot;
}

export interface AiPreviewRequest {
  log_ids: number[];
  user_context?: string;
  prompt_override?: string;
}

export interface AiPreviewResponse {
  redacted_prompt: string;
  estimated_tokens: number;
  provider: string;
  model: string;
  fallback_models?: string[];
  log_count: number;
  source_alias: string;
  app_name: string;
  system_prompt: string;
}

export interface AiDiagnosisStreamEvent {
  stage: 'init' | 'calling' | 'failover' | 'complete' | 'error';
  model?: string;
  next_model?: string;
  failed_model?: string;
  error?: string;
  message?: string;
  result?: AiDiagnosisResponse;
  fallback_models?: string[];
  attempt?: number;
  total_models?: number;
  is_fallback?: boolean;
}

export interface AiDiagnosisRequest {
  log_ids: number[];
  user_context?: string;
  prompt_override?: string;
  system_prompt_override?: string;
  provider?: string;
  model?: string;
  fallback_models?: string[];
  onEvent?: (event: AiDiagnosisStreamEvent) => void;
}

export interface AiDiagnosisResponse {
  summary: string;
  root_cause: string;
  remediation: string;
  model_used: string;
  fallback_used?: boolean;
  fallback_attempts?: string[];
  tokens_in?: number;
  tokens_out?: number;
  tokens_thoughts?: number;
  tokens_used: number;
  audit_id?: number;
}

export interface AiAuditEntry {
  id: number;
  timestamp: string;
  source_alias: string;
  app_name: string;
  log_count: number;
  user_context?: string | null;
  model: string;
  prompt_sent: string;
  response_text: string;
  tokens_in?: number;
  tokens_out?: number;
  tokens_thoughts?: number;
  tokens_used: number;
  system_prompt?: string | null;
}

export interface AuthStatusResponse {
  setup_required: boolean;
  authenticated: boolean;
}

export interface LogFilterParams {
  query?: string;
  severity_max?: number;
  source?: string | string[];
  sources?: string[];
  app_name?: string | string[];
  apps?: string[];
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface LogFacetsResponse {
  sources: string[];
  apps: string[];
  host_to_apps: Record<string, string[]>;
  app_to_hosts: Record<string, string[]>;
}

export interface AiModelInfo {
  id: string;
  name: string;
  description?: string | null;
  supports_thinking?: boolean;
  is_deprecated?: boolean;
}

export interface AiModelsResponse {
  provider: string;
  models: AiModelInfo[];
  has_api_key: boolean;
  cached_at?: string | null;
  is_live: boolean;
  error?: string | null;
}

export interface VersionInfo {
  current_version: string;
  latest_version?: string | null;
  update_available: boolean;
  check_enabled?: boolean;
  checked_at?: number | null;
}

export interface DropRule {
  id: number;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex: boolean;
  is_enabled: boolean;
  dropped_count: number;
  created_at: string;
}

export interface DropRuleCreate {
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex?: boolean;
  is_enabled?: boolean;
}

export interface DropRuleUpdate {
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern?: string;
  is_regex?: boolean;
  is_enabled?: boolean;
  reset_counter?: boolean;
}

export interface DropRuleTestRequest {
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex?: boolean;
  sample_message: string;
  sample_source?: string | null;
  sample_app?: string | null;
}

export interface DropRuleTestResponse {
  matched: boolean;
  error?: string | null;
}

export interface SavedView {
  id: number;
  name: string;
  query_params: {
    query?: string;
    severity_max?: number;
    source?: string | string[];
    sources?: string[];
    app_name?: string | string[];
    apps?: string[];
    from?: string;
    to?: string;
    [key: string]: any;
  };
  is_pinned: boolean;
  created_at: string;
}

export interface SavedViewCreate {
  name: string;
  query_params: Record<string, any>;
  is_pinned?: boolean;
}

export interface SavedViewUpdate {
  name?: string;
  query_params?: Record<string, any>;
  is_pinned?: boolean;
}



