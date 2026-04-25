export type IntakeType = 'procurement_form_present' | 'procurement_form_absent';
export type RegistrationType = 'new_registration' | 're_registration';
export type RiskBand = 'low' | 'medium' | 'high' | 'critical';
export type FieldStatus =
  | 'provided_in_form'
  | 'matched_with_evidence'
  | 'mismatched_with_evidence'
  | 'filled_from_supporting_docs'
  | 'missing'
  | 'conflicting'
  | 'unverifiable'
  | 'low_confidence'
  | 'declaration_missing'
  | 'not_applicable';
export type ValueSource =
  | 'procurement_form'
  | 'supporting_document'
  | 'derived_from_multiple_sources'
  | 'unknown';
export type RoutingDecisionCode =
  | 'complete_verified'
  | 'follow_up_required'
  | 'flagged_high_risk'
  | 'rejected'
  | 'manual_review_required';
export type LegitimacyStatus =
  | 'verified'
  | 'verified_with_minor_gaps'
  | 'conflicting'
  | 'partially_verifiable'
  | 'unverifiable'
  | 'suspicious';
export type MismatchSeverity = 'minor' | 'moderate' | 'critical';

export interface VendorCase {
  id: number;
  case_reference: string;
  vendor_display_name: string | null;
  intake_type: IntakeType;
  registration_type: RegistrationType | null;
  current_vendor_number: string | null;
  current_state: string;
  risk_band: RiskBand | null;
  latest_verification_status: string | null;
  latest_policy_status: string | null;
  latest_routing_decision: string | null;
  created_at: string;
  updated_at: string;
}

export interface FormFieldValue {
  id: number;
  vendor_case_id: number;
  section_name: string;
  field_name: string;
  field_label: string;
  value_text: string | null;
  normalized_value_text: string | null;
  value_source: ValueSource;
  status: FieldStatus;
  confidence_score: number | null;
  source_file_id: number | null;
  source_excerpt: string | null;
  notes: string | null;
  updated_at: string;
}

export interface UploadedFile {
  id: number;
  vendor_case_id: number;
  original_filename: string;
  file_type: string;
  source_category: 'procurement_form' | 'supporting_document' | 'zip_bundle' | 'corrected_upload' | 'follow_up_upload';
  upload_status: string;
  uploaded_at: string;
}

export interface VerificationRun {
  id: number;
  vendor_case_id: number;
  run_status: string;
  legitimacy_status: LegitimacyStatus | null;
  mismatch_severity: MismatchSeverity | null;
  summary: string | null;
  key_findings: string | null;
  confidence_score: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface PolicyEvaluationRun {
  id: number;
  vendor_case_id: number;
  risk_band: RiskBand | null;
  blocking_issue_count: number;
  recoverable_issue_count: number;
  policy_summary: string | null;
  risk_flags: string | null;
  policy_confidence_score: number | null;
  created_at: string;
}

export interface RoutingDecision {
  id: number;
  vendor_case_id: number;
  decision_code: RoutingDecisionCode;
  recommended_procurement_action: string | null;
  decision_reasoning_summary: string;
  confidence_score: number | null;
  created_at: string;
}

export interface EmailDraft {
  id: number;
  vendor_case_id: number;
  draft_type: 'vendor_follow_up';
  recipient_email: string | null;
  subject_line: string;
  body_text: string;
  draft_status: 'generated' | 'reviewed' | 'sent' | 'discarded';
  created_at: string;
}

export interface ReprocessingRun {
  id: number;
  vendor_case_id: number;
  trigger_type: 'corrected_upload' | 'manual_rerun' | 'follow_up_upload';
  reason_summary: string | null;
  previous_case_state: string;
  new_case_state: string | null;
  run_status: 'started' | 'completed' | 'failed';
  started_at: string;
  completed_at: string | null;
}
