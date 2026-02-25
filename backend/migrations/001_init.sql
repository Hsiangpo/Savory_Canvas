-- Savory Canvas 初始迁移
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS session (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  content_mode TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  content TEXT,
  file_path TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transcript_result (
  asset_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  text TEXT,
  segments TEXT,
  error_code TEXT,
  error_message TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (asset_id) REFERENCES asset(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS style_profile (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  name TEXT NOT NULL,
  style_payload TEXT NOT NULL,
  is_builtin INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inspiration_state (
  session_id TEXT PRIMARY KEY,
  stage TEXT NOT NULL,
  style_stage TEXT NOT NULL,
  is_locked INTEGER NOT NULL,
  image_count INTEGER,
  style_prompt TEXT,
  style_payload TEXT NOT NULL,
  asset_candidates TEXT NOT NULL,
  allocation_plan TEXT NOT NULL DEFAULT '[]',
  draft_style_id TEXT,
  requirement_ready INTEGER NOT NULL DEFAULT 1,
  transcript_seen_ids TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE,
  FOREIGN KEY (draft_style_id) REFERENCES style_profile(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inspiration_message (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  text TEXT NOT NULL,
  attachments TEXT NOT NULL,
  options TEXT,
  asset_candidates TEXT,
  style_context TEXT,
  stage TEXT NOT NULL,
  fallback_used INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS generation_job (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  style_profile_id TEXT,
  image_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  progress_percent INTEGER NOT NULL,
  current_stage TEXT NOT NULL,
  stage_message TEXT NOT NULL,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE,
  FOREIGN KEY (style_profile_id) REFERENCES style_profile(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS job_stage_log (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  stage_message TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES generation_job(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS image_result (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  image_index INTEGER NOT NULL,
  asset_refs TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  image_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES generation_job(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS copy_result (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  intro TEXT NOT NULL,
  guide_sections TEXT NOT NULL,
  ending TEXT NOT NULL,
  full_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES generation_job(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS generation_asset_breakdown (
  job_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  content_mode TEXT NOT NULL,
  source_assets TEXT NOT NULL,
  extracted TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES generation_job(id) ON DELETE CASCADE,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS export_task (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  export_format TEXT NOT NULL,
  status TEXT NOT NULL,
  file_path TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES generation_job(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS provider_config (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key TEXT NOT NULL,
  api_key_masked TEXT NOT NULL,
  api_protocol TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_routing_config (
  id TEXT PRIMARY KEY,
  image_model_provider_id TEXT NOT NULL,
  image_model_name TEXT NOT NULL,
  text_model_provider_id TEXT NOT NULL,
  text_model_name TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (image_model_provider_id) REFERENCES provider_config(id) ON DELETE CASCADE,
  FOREIGN KEY (text_model_provider_id) REFERENCES provider_config(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_asset_session_created ON asset(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_job_session_created ON generation_job(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_image_result_job_index ON image_result(job_id, image_index);
CREATE INDEX IF NOT EXISTS idx_copy_result_job ON copy_result(job_id);
CREATE INDEX IF NOT EXISTS idx_asset_breakdown_session ON generation_asset_breakdown(session_id);
CREATE INDEX IF NOT EXISTS idx_export_session_created ON export_task(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_inspiration_message_session_created ON inspiration_message(session_id, created_at);
