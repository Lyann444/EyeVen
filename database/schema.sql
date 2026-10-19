-- ============================================================
-- EyeVen Database Schema
-- Minimal table set per eyeven_data_and_state.md
-- ============================================================

CREATE DATABASE IF NOT EXISTS eyeven
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE eyeven;

-- -----------------------------------------------------------
-- 1. vendor_cases
-- Main case table — one record per vendor onboarding case.
-- -----------------------------------------------------------
CREATE TABLE vendor_cases (
    id                  BIGINT          NOT NULL AUTO_INCREMENT,
    case_reference      VARCHAR(50)     NOT NULL,
    vendor_display_name VARCHAR(255)    NULL,
    intake_type         ENUM('procurement_form_present','procurement_form_absent') NOT NULL,
    registration_type   ENUM('new_registration','re_registration') NULL,
    current_vendor_number VARCHAR(100)  NULL,
    current_state       VARCHAR(100)    NOT NULL,
    risk_band           ENUM('low','medium','high','critical') NULL,
    latest_verification_status  VARCHAR(100) NULL,
    latest_policy_status        VARCHAR(100) NULL,
    latest_routing_decision     VARCHAR(100) NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    closed_at           DATETIME        NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uk_case_reference (case_reference),
    KEY idx_current_state (current_state),
    KEY idx_risk_band (risk_band),
    KEY idx_state_updated (current_state, updated_at)
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 2. uploaded_files
-- All uploaded file metadata, including corrected uploads.
-- -----------------------------------------------------------
CREATE TABLE uploaded_files (
    id                      BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id          BIGINT      NOT NULL,
    reprocessing_run_id     BIGINT      NULL,
    original_filename       VARCHAR(255) NOT NULL,
    stored_filename         VARCHAR(255) NOT NULL,
    file_type               VARCHAR(50) NOT NULL,
    mime_type               VARCHAR(100) NOT NULL,
    file_size_bytes         BIGINT      NOT NULL,
    source_category         ENUM('procurement_form','supporting_document','zip_bundle','corrected_upload') NOT NULL,
    storage_path            VARCHAR(500) NOT NULL,
    checksum_sha256         VARCHAR(64) NOT NULL,
    is_zip_parent           BOOLEAN     NOT NULL DEFAULT FALSE,
    parent_uploaded_file_id BIGINT      NULL,
    upload_status           VARCHAR(50) NOT NULL,
    uploaded_at             DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),
    KEY idx_reprocessing_run_id (reprocessing_run_id),
    KEY idx_parent_file (parent_uploaded_file_id),

    CONSTRAINT fk_uploaded_files_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_uploaded_files_parent
        FOREIGN KEY (parent_uploaded_file_id) REFERENCES uploaded_files(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 3. eyeven_form_field_values
-- Canonical structured EyeVen procurement form per case.
-- UNIQUE on (vendor_case_id, field_name) — one current value per field.
-- -----------------------------------------------------------
CREATE TABLE eyeven_form_field_values (
    id                      BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id          BIGINT      NOT NULL,
    section_name            VARCHAR(100) NOT NULL,
    field_name              VARCHAR(100) NOT NULL,
    field_label             VARCHAR(150) NOT NULL,
    value_text              TEXT        NULL,
    normalized_value_text   TEXT        NULL,
    value_source            ENUM('procurement_form','supporting_document','derived_from_multiple_sources','unknown') NOT NULL,
    status                  ENUM('provided_in_form','matched_with_evidence','mismatched_with_evidence','filled_from_supporting_docs','missing','conflicting','unverifiable','low_confidence','declaration_missing') NOT NULL,
    confidence_score        DECIMAL(5,4) NULL,
    source_file_id          BIGINT      NULL,
    source_excerpt          TEXT        NULL,
    notes                   TEXT        NULL,
    updated_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uk_case_field (vendor_case_id, field_name),
    KEY idx_field_name (field_name),

    CONSTRAINT fk_form_values_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_form_values_source_file
        FOREIGN KEY (source_file_id) REFERENCES uploaded_files(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 4. verification_runs
-- Append-only — each run produces a new row.
-- -----------------------------------------------------------
CREATE TABLE verification_runs (
    id                      BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id          BIGINT      NOT NULL,
    reprocessing_run_id     BIGINT      NULL,
    run_status              VARCHAR(50) NOT NULL,
    legitimacy_status       ENUM('verified','verified_with_minor_gaps','conflicting','partially_verifiable','unverifiable','suspicious') NULL,
    mismatch_severity       ENUM('minor','moderate','critical') NULL,
    summary                 TEXT        NULL,
    key_findings            LONGTEXT    NULL,
    confidence_score        DECIMAL(5,4) NULL,
    created_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at            DATETIME    NULL,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),
    KEY idx_reprocessing_run_id (reprocessing_run_id),

    CONSTRAINT fk_verification_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 5. policy_evaluation_runs
-- Append-only — each run produces a new row.
-- -----------------------------------------------------------
CREATE TABLE policy_evaluation_runs (
    id                          BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id              BIGINT      NOT NULL,
    verification_run_id         BIGINT      NULL,
    reprocessing_run_id         BIGINT      NULL,
    risk_band                   ENUM('low','medium','high','critical') NULL,
    blocking_issue_count        INT         NOT NULL DEFAULT 0,
    recoverable_issue_count     INT         NOT NULL DEFAULT 0,
    policy_summary              TEXT        NULL,
    risk_flags                  LONGTEXT    NULL,
    policy_confidence_score     DECIMAL(5,4) NULL,
    created_at                  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at                DATETIME    NULL,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),
    KEY idx_verification_run_id (verification_run_id),
    KEY idx_reprocessing_run_id (reprocessing_run_id),

    CONSTRAINT fk_policy_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_policy_verification_run
        FOREIGN KEY (verification_run_id) REFERENCES verification_runs(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 6. routing_decisions
-- Append-only — preserves routing history over time.
-- -----------------------------------------------------------
CREATE TABLE routing_decisions (
    id                          BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id              BIGINT      NOT NULL,
    verification_run_id         BIGINT      NULL,
    policy_evaluation_run_id    BIGINT      NULL,
    decision_code               ENUM('complete_verified','follow_up_required','flagged_high_risk','rejected','manual_review_required') NOT NULL,
    recommended_procurement_action VARCHAR(255) NULL,
    decision_reasoning_summary  TEXT        NOT NULL,
    decision_reason_codes      LONGTEXT    NULL,
    safety_gate_result         LONGTEXT    NULL,
    confidence_score            DECIMAL(5,4) NULL,
    created_at                  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),
    KEY idx_case_created (vendor_case_id, created_at),

    CONSTRAINT fk_routing_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_routing_verification_run
        FOREIGN KEY (verification_run_id) REFERENCES verification_runs(id)
        ON DELETE SET NULL,
    CONSTRAINT fk_routing_policy_run
        FOREIGN KEY (policy_evaluation_run_id) REFERENCES policy_evaluation_runs(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 7. email_drafts
-- Follow-up email drafts generated by the Ticketing Agent.
-- -----------------------------------------------------------
CREATE TABLE email_drafts (
    id                      BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id          BIGINT      NOT NULL,
    routing_decision_id     BIGINT      NULL,
    draft_type              ENUM('vendor_follow_up') NOT NULL,
    recipient_email         VARCHAR(255) NULL,
    subject_line            VARCHAR(255) NOT NULL,
    body_text               LONGTEXT    NOT NULL,
    draft_status            ENUM('generated','reviewed','sent','discarded') NOT NULL DEFAULT 'generated',
    created_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at                 DATETIME    NULL,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),

    CONSTRAINT fk_email_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_email_routing_decision
        FOREIGN KEY (routing_decision_id) REFERENCES routing_decisions(id)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- 8. reprocessing_runs
-- Tracks corrected-submission reprocessing cycles.
-- Prevents corrected submissions from becoming duplicate cases.
-- -----------------------------------------------------------
CREATE TABLE reprocessing_runs (
    id                      BIGINT      NOT NULL AUTO_INCREMENT,
    vendor_case_id          BIGINT      NOT NULL,
    trigger_type            ENUM('corrected_upload','manual_rerun') NOT NULL,
    reason_summary          TEXT        NULL,
    previous_case_state     VARCHAR(100) NOT NULL,
    new_case_state          VARCHAR(100) NULL,
    run_status              ENUM('started','completed','failed') NOT NULL,
    started_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at            DATETIME    NULL,

    PRIMARY KEY (id),
    KEY idx_vendor_case_id (vendor_case_id),

    CONSTRAINT fk_reprocessing_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- -----------------------------------------------------------
-- Add reprocessing_run_id FK to uploaded_files now that
-- reprocessing_runs exists.
-- -----------------------------------------------------------
ALTER TABLE uploaded_files
    ADD CONSTRAINT fk_uploaded_files_reprocessing_run
    FOREIGN KEY (reprocessing_run_id) REFERENCES reprocessing_runs(id)
    ON DELETE SET NULL;

-- Add reprocessing_run_id FK to verification_runs
ALTER TABLE verification_runs
    ADD CONSTRAINT fk_verification_reprocessing_run
    FOREIGN KEY (reprocessing_run_id) REFERENCES reprocessing_runs(id)
    ON DELETE SET NULL;

-- Add reprocessing_run_id FK to policy_evaluation_runs
ALTER TABLE policy_evaluation_runs
    ADD CONSTRAINT fk_policy_reprocessing_run
    FOREIGN KEY (reprocessing_run_id) REFERENCES reprocessing_runs(id)
    ON DELETE SET NULL;
