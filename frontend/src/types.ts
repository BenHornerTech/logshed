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
  source_alias: string;
  app_name: string;
  before: LogEntry[];
  target: LogEntry | null;
  after: LogEntry[];
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
  ai_api_key?: string;
  ai_base_url?: string | null;
  pushover_user_key?: string;
  pushover_app_token?: string;
  retention_days: number;
}

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
  history: StorageMetricsSnapshot[];
}

export interface HealthResponse {
  status: string;
  database: string;
  queue_depth: number;
  dropped_logs: number;
}

export interface PruneResponse {
  status: string;
  deleted_logs: number;
  deleted_metrics: number;
  metrics: StorageMetricsSnapshot;
}

export interface AiPreviewRequest {
  log_ids: number[];
}

export interface AiPreviewResponse {
  sanitized_prompt: string;
  estimated_tokens: number;
  provider: string;
  model: string;
  log_count: number;
  source_alias: string;
  app_name: string;
}

export interface AiAnalysisRequest {
  log_ids: number[];
  user_context?: string;
  provider?: string;
  model?: string;
}

export interface AiAnalysisResponse {
  summary: string;
  root_cause: string;
  remediation: string;
  model_used: string;
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
  tokens_used: number;
}

export interface AuthStatusResponse {
  setup_required: boolean;
  authenticated: boolean;
}

export interface LogFilterParams {
  query?: string;
  severity_max?: number;
  source?: string;
  app_name?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}
