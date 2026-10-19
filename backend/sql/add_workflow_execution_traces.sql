-- EyeVen workflow execution trace history
-- Apply once to the configured MySQL database before deploying this feature.
CREATE TABLE workflow_execution_traces (
    id BIGINT NOT NULL AUTO_INCREMENT,
    vendor_case_id BIGINT NOT NULL,
    stage VARCHAR(80) NOT NULL,
    state_before VARCHAR(100) NOT NULL,
    state_after VARCHAR(100) NOT NULL,
    status VARCHAR(80) NOT NULL,
    started_at DATETIME NULL,
    completed_at DATETIME NOT NULL,
    duration_ms FLOAT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX ix_workflow_execution_traces_vendor_case_id (vendor_case_id),
    CONSTRAINT fk_workflow_execution_traces_vendor_case
        FOREIGN KEY (vendor_case_id) REFERENCES vendor_cases (id)
        ON DELETE CASCADE
);
