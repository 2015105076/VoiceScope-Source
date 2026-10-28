-- VoiceScope MySQL 8.0 schema. Run once after creating the database.
CREATE DATABASE IF NOT EXISTS voicescope CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE voicescope;

CREATE TABLE IF NOT EXISTS patients (
  id BINARY(16) PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  age TINYINT UNSIGNED NOT NULL,
  gender ENUM('男','女','其他/不便透露') NOT NULL,
  location VARCHAR(128) NOT NULL,
  chief_complaint VARCHAR(255) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  INDEX idx_patients_name (name), INDEX idx_patients_gender (gender), INDEX idx_patients_created (created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversations (
  id BINARY(16) PRIMARY KEY,
  patient_id BINARY(16) NOT NULL,
  status ENUM('active','completed','deleted') NOT NULL DEFAULT 'active',
  case_points_json JSON NOT NULL,
  summary_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_conversations_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
  INDEX idx_conversations_patient_updated (patient_id, updated_at), INDEX idx_conversations_created (created_at), INDEX idx_conversations_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS messages (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  role ENUM('user','assistant','system') NOT NULL,
  source ENUM('user_statement','ai_response','system_response','legacy_assistant') NOT NULL,
  content LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_messages_conversation_created (conversation_id, created_at, id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audio_files (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  message_id BIGINT UNSIGNED NULL,
  vowel_key ENUM('a','o','e','i','u','v') NULL,
  original_filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(128) NOT NULL,
  size_bytes BIGINT UNSIGNED NOT NULL,
  sha256 CHAR(64) NOT NULL,
  duration_seconds DECIMAL(10,3) NULL,
  sample_rate INT UNSIGNED NULL,
  storage_key VARCHAR(512) NOT NULL,
  audio_data LONGBLOB NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_audio_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  CONSTRAINT fk_audio_message FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL,
  INDEX idx_audio_conversation_created (conversation_id, created_at), INDEX idx_audio_sha256 (sha256)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS voice_analysis_results (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  audio_file_id BINARY(16) NULL,
  analysis_type ENUM('acoustic','research_model') NOT NULL,
  predicted_label VARCHAR(128) NULL,
  predicted_probability DECIMAL(8,6) NULL,
  result_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_results_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  CONSTRAINT fk_results_audio FOREIGN KEY (audio_file_id) REFERENCES audio_files(id) ON DELETE SET NULL,
  INDEX idx_results_conversation_created (conversation_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS case_points (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  point_key ENUM('symptom','duration','pattern','smoking','drinking') NOT NULL,
  value TEXT NOT NULL,
  status ENUM('missing','needs_detail','collected') NOT NULL,
  evidence_json JSON NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_case_points_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  UNIQUE KEY uq_case_point (conversation_id, point_key), INDEX idx_case_points_updated (updated_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversation_events (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  source VARCHAR(64) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  payload_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_events_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_events_conversation_created (conversation_id, created_at, id), INDEX idx_events_type (event_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS analysis_records (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  audio_asset_ids_json JSON NOT NULL,
  analysis_type VARCHAR(64) NOT NULL,
  result_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_analysis_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_analysis_conversation (conversation_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS clinical_labels (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  label_type VARCHAR(64) NOT NULL,
  label_value TEXT NOT NULL,
  label_status ENUM('preliminary','confirmed','rejected') NOT NULL,
  annotator VARCHAR(64) NOT NULL,
  source VARCHAR(64) NOT NULL,
  notes TEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_labels_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_labels_conversation (conversation_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS clinical_examinations (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  examination_type VARCHAR(64) NOT NULL,
  examination_date VARCHAR(32),
  findings TEXT NOT NULL,
  clinician VARCHAR(64) NOT NULL,
  structured_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_examinations_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_examinations_conversation (conversation_id, examination_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS follow_up_records (
  id BINARY(16) PRIMARY KEY,
  conversation_id BINARY(16) NOT NULL,
  follow_up_date VARCHAR(32) NOT NULL,
  outcome_status VARCHAR(64) NOT NULL,
  outcome_notes TEXT NOT NULL,
  recorder VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_followups_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  INDEX idx_followups_conversation (conversation_id, follow_up_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS data_quality_checks (
  conversation_id BINARY(16) PRIMARY KEY,
  status ENUM('unchecked','incomplete','ready') NOT NULL,
  issues_json JSON NOT NULL,
  checked_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_quality_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS model_versions (
  id BINARY(16) PRIMARY KEY,
  model_name VARCHAR(128) NOT NULL,
  version VARCHAR(64) NOT NULL,
  model_type VARCHAR(64) NOT NULL,
  artifact_reference VARCHAR(512) NOT NULL,
  artifact_sha256 CHAR(64),
  metrics_json JSON NOT NULL,
  status ENUM('draft','validated','archived') NOT NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_model_version (model_name, version)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS experiment_runs (
  id BINARY(16) PRIMARY KEY,
  model_version_id BINARY(16) NOT NULL,
  dataset_version VARCHAR(128) NOT NULL,
  split_strategy VARCHAR(128) NOT NULL,
  random_seed INT,
  run_status ENUM('running','completed','failed') NOT NULL,
  metrics_json JSON NOT NULL,
  notes TEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_experiments_model FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
  INDEX idx_experiments_model (model_version_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS research_audit (
  id BINARY(16) PRIMARY KEY,
  action VARCHAR(64) NOT NULL,
  conversation_id BINARY(16),
  actor VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  INDEX idx_research_audit_time (created_at),
  INDEX idx_research_audit_conversation (conversation_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sqlite_migration_runs (
  source_sha256 CHAR(64) PRIMARY KEY,
  source_name VARCHAR(512) NOT NULL,
  imported_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;

ALTER TABLE conversations MODIFY status ENUM('active','completed','deleted') NOT NULL DEFAULT 'active';
