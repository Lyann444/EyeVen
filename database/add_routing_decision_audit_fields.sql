-- Additive migration for structured routing decision audit data.
-- Run once against an existing EyeVen database before deploying this branch.
ALTER TABLE routing_decisions
    ADD COLUMN decision_reason_codes LONGTEXT NULL,
    ADD COLUMN safety_gate_result LONGTEXT NULL;
