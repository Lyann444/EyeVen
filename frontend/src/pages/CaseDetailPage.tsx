import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Table, TableBody,
  TableCell, TableHead, TableRow, Button,
  Divider, CircularProgress, Accordion, AccordionSummary, AccordionDetails, Alert, Stack, Collapse,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, FormControlLabel, Checkbox, MenuItem,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import apiClient from '../services/apiClient';
import { caseService } from '../services/caseService';
import { auditService } from '../services/auditService';
import {
  VendorCase,
  FormFieldValue,
  WorkflowTraceStage,
  WorkflowAgenticAction,
} from '../types';

const statusColor = (s: string) => {
  switch (s) {
    case 'provided_in_form': case 'matched_with_evidence': return 'success';
    case 'mismatched_with_evidence': case 'conflicting': return 'error';
    case 'missing': case 'declaration_missing': return 'default';
    case 'filled_from_supporting_docs': return 'info';
    case 'low_confidence': return 'warning';
    case 'unverifiable': return 'error';
    default: return 'default';
  }
};

const riskColor = (r: string | null) => {
  switch (r) {
    case 'low': return 'success';
    case 'medium': return 'warning';
    case 'high': return 'error';
    case 'critical': return 'error';
    default: return 'default';
  }
};

const fmtStatus = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

interface OcrWarning {
  file_id: number;
  status: string;
  quality_status: string;
  average_confidence: number | null;
  low_confidence_block_count: number;
}

const CaseDetailPage: React.FC = () => {
  const { caseId } = useParams<{ caseId: string }>();
  const [caseData, setCaseData] = useState<VendorCase | null>(null);
  const [formFields, setFormFields] = useState<FormFieldValue[]>([]);
  const [verification, setVerification] = useState<any>(null);
  const [policy, setPolicy] = useState<any>(null);
  const [routing, setRouting] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [workflowTrace, setWorkflowTrace] = useState<WorkflowTraceStage[]>([]);
  const [agenticTrace, setAgenticTrace] = useState<WorkflowAgenticAction[]>([]);
  const [ocrWarnings, setOcrWarnings] = useState<OcrWarning[]>([]);
  const [stoppingReason, setStoppingReason] = useState<string | null>(null);
  const [workflowMessage, setWorkflowMessage] = useState<string | null>(null);
  const [followUpBusy, setFollowUpBusy] = useState(false);
  const [followUpMessage, setFollowUpMessage] = useState<{ severity: 'success' | 'info' | 'warning' | 'error'; text: string } | null>(null);
  const [expandedDraftId, setExpandedDraftId] = useState<number | null>(null);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoveryMessage, setRecoveryMessage] = useState<{ severity: 'success' | 'info' | 'error'; text: string } | null>(null);
  const [editField, setEditField] = useState<FormFieldValue | null>(null);
  const [editFieldValue, setEditFieldValue] = useState('');
  const [editSourceFileId, setEditSourceFileId] = useState('');
  const [confirmedAgainstSource, setConfirmedAgainstSource] = useState(false);
  const [editVendorResponse, setEditVendorResponse] = useState(false);
  const [fieldSourceUploadMessage, setFieldSourceUploadMessage] = useState<string | null>(null);
  const [editDraftId, setEditDraftId] = useState<number | null>(null);
  const [draftRecipient, setDraftRecipient] = useState('');
  const [draftSubject, setDraftSubject] = useState('');
  const [draftBody, setDraftBody] = useState('');

  useEffect(() => {
    const load = async () => {
      if (!caseId || !/^\d+$/.test(caseId)) {
        setCaseData(null);
        setLoading(false);
        return;
      }
      const id = Number(caseId);
      try {
        // Load the case itself separately. A failed secondary panel (e.g. audit)
        // must not make an existing case appear to be missing.
        const caseResponse = await caseService.getCaseDetail(id);
        setCaseData(caseResponse.data);
      } catch (e) {
        console.error('Failed to load case details', e);
        setCaseData(null);
        setLoading(false);
        return;
      }

      const results = await Promise.allSettled([
        apiClient.get(`/workflow/${id}/form-alignment`),
        caseService.getVerification(id),
        caseService.getPolicy(id),
        caseService.getRouting(id),
        auditService.getAuditTrail(id),
      ]);
      const [fa, v, p, r, a] = results;
      if (fa.status === 'fulfilled') setFormFields(fa.value.data?.fields || []);
      else console.error('Failed to load form alignment', fa.reason);
      if (v.status === 'fulfilled') setVerification(v.value.data);
      else console.error('Failed to load verification', v.reason);
      if (p.status === 'fulfilled') setPolicy(p.value.data);
      else console.error('Failed to load policy', p.reason);
      if (r.status === 'fulfilled') setRouting(r.value.data);
      else console.error('Failed to load routing', r.reason);
      if (a.status === 'fulfilled') setAudit(a.value.data);
      else console.error('Failed to load audit trail', a.reason);
      setLoading(false);
    };
    load();
  }, [caseId]);

  const refreshCasePanels = async (id: number) => {
    const results = await Promise.allSettled([
      caseService.getCaseDetail(id),
      apiClient.get(`/workflow/${id}/form-alignment`),
      caseService.getVerification(id),
      caseService.getPolicy(id),
      caseService.getRouting(id),
      auditService.getAuditTrail(id),
    ]);
    const [c, fa, v, p, r, a] = results;
    if (c.status === 'fulfilled') setCaseData(c.value.data);
    else console.error('Failed to refresh case details', c.reason);
    if (fa.status === 'fulfilled') setFormFields(fa.value.data?.fields || []);
    else console.error('Failed to refresh form alignment', fa.reason);
    if (v.status === 'fulfilled') setVerification(v.value.data);
    else console.error('Failed to refresh verification', v.reason);
    if (p.status === 'fulfilled') setPolicy(p.value.data);
    else console.error('Failed to refresh policy', p.reason);
    if (r.status === 'fulfilled') setRouting(r.value.data);
    else console.error('Failed to refresh routing', r.reason);
    if (a.status === 'fulfilled') setAudit(a.value.data);
    else console.error('Failed to refresh audit trail', a.reason);
  };

  const runWorkflow = async () => {
    if (!caseId) return;
    setRunning(true);
    setWorkflowMessage(null);
    setOcrWarnings([]);
    try {
      const workflowResponse = await caseService.runWorkflow(parseInt(caseId));
      const trace = workflowResponse.data?.trace;
      const agentTrace = workflowResponse.data?.agentic_trace;
      const routingResult = workflowResponse.data?.results?.routing;
      const extractionResult = workflowResponse.data?.results?.extraction;
      setWorkflowTrace(Array.isArray(trace) ? trace : []);
      setAgenticTrace(Array.isArray(agentTrace) ? agentTrace : []);
      setOcrWarnings(
        Array.isArray(extractionResult?.ocr_warnings)
          ? extractionResult.ocr_warnings
          : []
      );
      setStoppingReason(workflowResponse.data?.stopping_reason || null);
      setWorkflowMessage(
        workflowResponse.data?.stopping_reason
          ? `Workflow stopped: ${fmtStatus(workflowResponse.data.stopping_reason)}.`
          : Array.isArray(trace) && trace.length > 0
            ? 'Workflow finished. The trace below shows the stages executed in this run.'
            : 'The workflow returned no stage trace. The case may already be in a terminal state.'
      );
      if (routingResult?.decision_code) {
        setRouting({
          decision_code: routingResult.decision_code,
          recommended_procurement_action: routingResult.recommended_procurement_action,
          decision_reasoning_summary: routingResult.reasoning,
          decision_evidence: routingResult.decision_evidence || [],
          confidence_score: routingResult.confidence,
        });
      }

      // Refresh the case panels without a full page reload so the execution trace remains visible.
      const id = parseInt(caseId);
      await refreshCasePanels(id);
    } catch (e) {
      console.error('Workflow run failed', e);
      setStoppingReason('workflow_request_failed');
      setWorkflowMessage('Workflow execution failed. Check the backend logs for details.');
    } finally {
      setRunning(false);
    }
  };

  const uploadRecoveryDocuments = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!caseId || !event.target.files?.length) return;
    const formData = new FormData();
    Array.from(event.target.files).forEach(file => formData.append('files', file));
    setRecoveryBusy(true);
    setRecoveryMessage(null);
    try {
      const response = await caseService.uploadRecoveryFiles(Number(caseId), formData);
      const duplicate = response.data?.status === 'duplicate_upload';
      setRecoveryMessage({
        severity: duplicate ? 'info' : 'success',
        text: duplicate
          ? 'This document was already uploaded. No new workflow run was started.'
          : 'Missing document uploaded. Review it above, then click Run Workflow to process the case.',
      });
      await refreshCasePanels(Number(caseId));
    } catch (error: any) {
      setRecoveryMessage({
        severity: 'error',
        text: error.response?.data?.error || 'Document upload failed.',
      });
    } finally {
      setRecoveryBusy(false);
      event.target.value = '';
    }
  };

  const uploadFieldSourceDocument = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!caseId || !file) return;
    const formData = new FormData();
    formData.append('files', file);
    setRecoveryBusy(true);
    setFieldSourceUploadMessage(null);
    try {
      const response = await caseService.uploadRecoveryFiles(Number(caseId), formData);
      const uploadedFile = response.data?.uploaded_files?.[0];
      if (uploadedFile?.id) {
        setEditSourceFileId(String(uploadedFile.id));
      }
      setFieldSourceUploadMessage(
        response.data?.status === 'duplicate_upload'
          ? 'This document is already attached to the case and is selected below.'
          : 'Document uploaded and selected. Run the workflow first if you want EyeVen to extract its contents automatically.'
      );
      await refreshCasePanels(Number(caseId));
    } catch (error: any) {
      setFieldSourceUploadMessage(
        error.response?.data?.error || 'The supporting document could not be uploaded.'
      );
    } finally {
      setRecoveryBusy(false);
      event.target.value = '';
    }
  };

  const saveFieldCorrection = async () => {
    if (!caseId || !editField) return;
    setRecoveryBusy(true);
    setRecoveryMessage(null);
    try {
      const response = await caseService.correctFields(Number(caseId), [{
        field_name: editField.field_name,
        value_text: editFieldValue,
        source_file_id: editSourceFileId ? Number(editSourceFileId) : null,
        confirmed_against_source: confirmedAgainstSource,
        vendor_response: editVendorResponse,
      }]);
      setEditField(null);
      const reprocessStatus = response.data?.reprocessing?.status;
      const processed = reprocessStatus === 'completed' || reprocessStatus === 'skipped_unchanged';
      const supportedFields = response.data?.supporting_document_fields || [];
      setRecoveryMessage({
        severity: processed ? 'success' : 'error',
        text: processed
          ? `Correction saved and the affected verification, policy, and routing stages were reprocessed. You do not need to click Run Workflow again.${supportedFields.includes(editField.field_name) ? ' The selected supporting document gives this value high demo confidence; it is still not external verification.' : ' The value remains marked as manually entered and may require review.'}`
          : `Correction was saved, but reprocessing did not complete: ${response.data?.reprocessing?.error || reprocessStatus || 'unknown failure'}. Retry processing or send the case to manual review.`,
      });
      await refreshCasePanels(Number(caseId));
    } catch (error: any) {
      setRecoveryMessage({
        severity: 'error',
        text: error.response?.data?.error || 'The field correction could not be saved.',
      });
    } finally {
      setRecoveryBusy(false);
    }
  };

  const openDraftEditor = (draft: any) => {
    setEditDraftId(draft.id);
    setDraftRecipient(draft.recipient_email || '');
    setDraftSubject(draft.subject_line || '');
    setDraftBody(draft.body_text || '');
  };

  const saveFollowUpDraft = async () => {
    if (!caseId || editDraftId === null) return;
    setFollowUpBusy(true);
    try {
      await caseService.updateFollowUpDraft(Number(caseId), editDraftId, {
        recipient_email: draftRecipient.trim() || null,
        subject_line: draftSubject,
        body_text: draftBody,
      });
      setEditDraftId(null);
      await refreshCasePanels(Number(caseId));
      setFollowUpMessage({ severity: 'success', text: 'Draft saved for review. It has not been sent.' });
    } catch (error: any) {
      setFollowUpMessage({
        severity: 'error',
        text: error.response?.data?.error || 'The follow-up draft could not be saved.',
      });
    } finally {
      setFollowUpBusy(false);
    }
  };

  const createFollowUpDraft = async () => {
    if (!caseId) return;
    setFollowUpBusy(true);
    setFollowUpMessage(null);
    try {
      const response = await caseService.createFollowUp(parseInt(caseId));
      const status = response.data?.status;
      if (status === 'no_eligible_follow_up_fields') {
        setFollowUpMessage({
          severity: 'info',
          text: 'There are no unresolved fields eligible for follow-up.',
        });
        return;
      }
      const auditResponse = await auditService.getAuditTrail(parseInt(caseId));
      setAudit(auditResponse.data);
      if (response.data?.email_draft_id) {
        setExpandedDraftId(response.data.email_draft_id);
      }
      setFollowUpMessage({
        severity: 'success',
        text: status === 'already_available'
          ? 'An existing follow-up draft is ready to view.'
          : 'Follow-up email draft generated. Review it before sending.',
      });
    } catch (error: any) {
      console.error('Failed to generate follow-up draft', error);
      setFollowUpMessage({
        severity: 'error',
        text: error.response?.data?.error
          || (error.response?.data?.status === 'no_eligible_follow_up_fields'
            ? 'There are no unresolved fields eligible for follow-up.'
            : 'Could not generate the follow-up draft. Please try again.'),
      });
    } finally {
      setFollowUpBusy(false);
    }
  };

  if (loading) return <CircularProgress />;
  if (!caseData) return <Typography>Case not found</Typography>;

  const populated = formFields.filter(f => f.value_text).length;
  const missing = formFields.filter(f => f.status === 'missing').length;
  const conflicting = formFields.filter(f => f.status === 'conflicting').length;
  const fieldsNeedingAttention = formFields.filter(f =>
    ['missing', 'conflicting', 'unverifiable', 'low_confidence', 'declaration_missing', 'mismatched_with_evidence']
      .includes(f.status)
  );
  const sections = Array.from(new Set(formFields.map(f => f.section_name)));
  const openFieldEditor = (field: FormFieldValue) => {
    setEditField(field);
    setEditFieldValue(field.value_text || '');
    setEditSourceFileId('');
    setConfirmedAgainstSource(false);
    setEditVendorResponse(false);
  };

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4">{caseData.case_reference}</Typography>
          <Typography color="text.secondary">{caseData.vendor_display_name || 'Vendor name not yet extracted'}</Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Chip label={fmtStatus(caseData.current_state)} color={
            caseData.current_state === 'completed' ? 'success' :
            caseData.current_state === 'rejected' ? 'error' :
            caseData.current_state === 'flagged_high_risk' ? 'error' :
            'primary'
          } />
          {caseData.risk_band && <Chip label={caseData.risk_band.toUpperCase()} color={riskColor(caseData.risk_band) as any} />}
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            onClick={runWorkflow}
            disabled={running || (
              caseData.current_state === 'awaiting_pic_upload'
              && (caseData.uploaded_files || []).length === 0
            )}
          >
            {running ? 'Running...' : 'Run Workflow'}
          </Button>
        </Box>
      </Box>

      {workflowMessage && (
        <Alert
          severity={
            stoppingReason?.includes('failure')
              ? 'error'
              : stoppingReason === 'terminal_case'
                ? 'success'
                : stoppingReason === 'planner_finish'
                  ? 'info'
                  : stoppingReason
                    ? 'warning'
                    : workflowTrace.length > 0 ? 'success' : 'info'
          }
          sx={{ mb: 2 }}
        >
          {workflowMessage}
        </Alert>
      )}
      {ocrWarnings.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <Typography variant="subtitle2">OCR needs a PIC check</Typography>
          {ocrWarnings.map((warning, index) => (
            <Typography key={`${warning.file_id}-${index}`} variant="body2">
              Document {warning.file_id}: {fmtStatus(warning.quality_status || warning.status)}
              {typeof warning.average_confidence === 'number'
                ? ` · average text recognition score ${(warning.average_confidence * 100).toFixed(0)}%`
                : ''}
              {warning.low_confidence_block_count > 0
                ? ` · ${warning.low_confidence_block_count} low-confidence text lines`
                : ''}
              . Check the extracted fields below and correct any unreadable values before requesting them from the vendor.
            </Typography>
          ))}
        </Alert>
      )}
      {caseData.current_state === 'awaiting_pic_upload' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          EyeVen found unresolved information but no case documents were submitted. Upload the available document before running the workflow.
        </Alert>
      )}

      {(caseData.uploaded_files || []).length === 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>Missing Document</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              No documents have been submitted for this case. Use Follow-Up Communication below to prepare an email requesting the missing document. When it arrives, upload it here, then click Run Workflow.
            </Typography>
            <Button component="label" variant="outlined" disabled={recoveryBusy}>
              {recoveryBusy ? 'Uploading...' : 'Upload Missing Document'}
              <input
                hidden
                type="file"
                multiple
                accept=".pdf,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.zip"
                onChange={uploadRecoveryDocuments}
              />
            </Button>
          </CardContent>
        </Card>
      )}
      {recoveryMessage && (
        <Alert severity={recoveryMessage.severity} sx={{ mb: 2 }}>
          {recoveryMessage.text}
        </Alert>
      )}

      {caseData.current_state === 'pending_follow_up' && fieldsNeedingAttention.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>Enter Information Received from the Vendor</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              After the vendor replies, enter each value below. Choose Save and Reprocess to save it and refresh the affected workflow stages.
            </Typography>
            <Stack spacing={1}>
              {fieldsNeedingAttention.map(field => (
                <Box
                  key={field.field_name}
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 2,
                    p: 1,
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                  }}
                >
                  <Box sx={{ minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 600 }}>{field.field_label}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {field.value_text || 'No value entered'} · {fmtStatus(field.status)}
                    </Typography>
                  </Box>
                  <Button size="small" variant="outlined" onClick={() => openFieldEditor(field)}>
                    {field.value_text ? 'Edit' : 'Enter Value'}
                  </Button>
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Execution trace from the latest workflow run in this page session */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Workflow Execution Trace</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Shows the state transitions returned by the orchestrator for the latest run. This trace is not a historical log; saved verification, policy, and routing runs appear in the audit trail below.
          </Typography>
          {workflowTrace.length === 0 ? (
            <Alert severity="info">
              Run the workflow to display its execution trace here.
            </Alert>
          ) : (
            <Stack spacing={1.5}>
              {workflowTrace.map((item: WorkflowTraceStage, index: number) => (
                <Box key={`${item.stage || 'stage'}-${index}`} sx={{
                  display: 'flex', gap: 2, alignItems: 'flex-start', p: 1.5,
                  border: '1px solid', borderColor: 'divider', borderRadius: 2,
                }}>
                  <Chip label={index + 1} size="small" color="primary" />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 600 }}>{fmtStatus(item.stage || 'unknown stage')}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {fmtStatus(item.state_before || 'unknown')} → {fmtStatus(item.state_after || 'unknown')}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {typeof item.duration_ms === 'number'
                        ? `Duration: ${item.duration_ms.toFixed(2)} ms`
                        : 'Duration unavailable'}
                      {item.started_at
                        ? ` · Started ${new Date(item.started_at).toLocaleTimeString()}`
                        : ''}
                      {item.completed_at
                        ? ` · Finished ${new Date(item.completed_at).toLocaleTimeString()}`
                        : ''}
                    </Typography>
                  </Box>
                  <Chip
                    size="small"
                    label={fmtStatus(item.status || 'completed')}
                    color={['completed', 'success'].includes(item.status) ? 'success' : item.status === 'glm_failed' ? 'error' : 'default'}
                    variant="outlined"
                  />
                </Box>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>

      {agenticTrace.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>Agentic Actions</Typography>
            <Stack spacing={1.5}>
              {agenticTrace.map(action => (
                <Box key={action.sequence} sx={{
                  p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 2,
                }}>
                  <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1 }}>
                    <Chip label={`Action ${action.sequence}`} size="small" color="primary" />
                    <Typography sx={{ fontWeight: 600 }}>
                      {fmtStatus(action.action || 'planner failure')}
                    </Typography>
                    <Chip
                      label={fmtStatus(action.status)}
                      size="small"
                      color={action.status.includes('failed') ? 'error' : 'default'}
                      variant="outlined"
                    />
                    <Chip
                      label={action.progress ? 'Progress made' : 'No progress'}
                      size="small"
                      color={action.progress ? 'success' : 'warning'}
                      variant="outlined"
                    />
                  </Box>
                  <Typography variant="body2">{action.reason}</Typography>
                  {(action.target_fields.length > 0 || action.target_stage) && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                      Target: {action.target_stage || action.target_fields.join(', ')}
                    </Typography>
                  )}
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {fmtStatus(action.state_before)} → {fmtStatus(action.state_after)}
                    {' · '}{action.duration_ms.toFixed(2)} ms
                  </Typography>
                  {action.failure_reason && (
                    <Typography variant="body2" color="error" sx={{ mt: 0.5 }}>
                      {action.failure_reason}
                    </Typography>
                  )}
                  {action.result?.source === 'internal_demo_registry' && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                      Internal demo registry result: {String(action.result.match_status || 'unknown')}
                      {' · External verification was not performed'}
                    </Typography>
                  )}
                </Box>
              ))}
            </Stack>
            {stoppingReason && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                Stopping reason: {fmtStatus(stoppingReason)}
              </Typography>
            )}
          </CardContent>
        </Card>
      )}

      <Grid container spacing={3}>
        {/* Form Alignment Panel */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>EyeVen Form Alignment</Typography>
              <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
                <Chip label={`${populated} Populated`} color="success" size="small" />
                <Chip label={`${missing} Missing`} color="default" size="small" />
                <Chip label={`${conflicting} Conflicting`} color="error" size="small" />
                <Typography variant="body2" color="text.secondary">
                  Intake: {caseData.intake_type === 'procurement_form_present' ? 'Form Present' : 'Form Absent'}
                </Typography>
              </Box>
              {sections.map(section => (
                <Accordion key={section} defaultExpanded={sections.length <= 3}>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography sx={{ fontWeight: 600 }}>{fmtStatus(section)}</Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Field</TableCell>
                          <TableCell>Value</TableCell>
                          <TableCell>Source</TableCell>
                          <TableCell>Status</TableCell>
                          <TableCell>Confidence</TableCell>
                          <TableCell>Correction</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {formFields.filter(f => f.section_name === section).map(f => (
                          <TableRow key={f.field_name}>
                            <TableCell>{f.field_label}</TableCell>
                            <TableCell sx={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {f.value_text || '—'}
                            </TableCell>
                            <TableCell><Chip label={f.value_source} size="small" variant="outlined" /></TableCell>
                            <TableCell><Chip label={fmtStatus(f.status)} size="small" color={statusColor(f.status) as any} /></TableCell>
                            <TableCell>{f.confidence_score != null ? (f.confidence_score * 100).toFixed(0) + '%' : '—'}</TableCell>
                            <TableCell>
                              <Button size="small" onClick={() => {
                                openFieldEditor(f);
                              }}>
                                {f.value_text ? 'Edit' : 'Enter Value'}
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </AccordionDetails>
                </Accordion>
              ))}
            </CardContent>
          </Card>
        </Grid>

        {/* Verification Panel */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Verification Findings</Typography>
              {verification?.status === 'no_verification_run_yet' ? (
                <Typography color="text.secondary">No verification run yet. Run the workflow first.</Typography>
              ) : (
                <>
                  <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
                    <Chip
                      label={verification?.legitimacy_status ? fmtStatus(verification.legitimacy_status) : 'Unknown'}
                      color={verification?.legitimacy_status === 'verified' ? 'success' : 'warning'}
                    />
                    {verification?.mismatch_severity && (
                      <Chip label={`Severity: ${verification.mismatch_severity}`} color={
                        verification.mismatch_severity === 'critical' ? 'error' :
                        verification.mismatch_severity === 'moderate' ? 'warning' : 'info'
                      } size="small" />
                    )}
                  </Box>
                  <Typography variant="body2">{verification?.summary || 'No summary'}</Typography>
                  {verification?.confidence_score != null && (
                    <Typography variant="body2" sx={{ mt: 1 }}>
                      Confidence: {(verification.confidence_score * 100).toFixed(0)}%
                    </Typography>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Policy / Risk Panel */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Policy & Risk Assessment</Typography>
              {policy?.status === 'no_policy_run_yet' ? (
                <Typography color="text.secondary">No policy evaluation yet. Run the workflow first.</Typography>
              ) : (
                <>
                  <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
                    {policy?.risk_band && <Chip label={policy.risk_band.toUpperCase()} color={riskColor(policy.risk_band) as any} />}
                    <Chip label={`${policy?.blocking_issue_count ?? 0} Blocking`} color="error" size="small" />
                    <Chip label={`${policy?.recoverable_issue_count ?? 0} Recoverable`} color="warning" size="small" />
                  </Box>
                  <Typography variant="body2">{policy?.policy_summary || 'No summary'}</Typography>
                  {policy?.risk_flags && (
                    <Box sx={{ mt: 1 }}>
                      <Typography variant="body2" color="text.secondary">Risk Flags:</Typography>
                      {(() => {
                        try {
                          const flags = typeof policy.risk_flags === 'string' ? JSON.parse(policy.risk_flags) : policy.risk_flags;
                          return Array.isArray(flags) ? flags.map((f: any, i: number) => (
                            <Chip key={i} label={f.flag_type || f} size="small" sx={{ mr: 0.5, mb: 0.5 }} color="warning" />
                          )) : null;
                        } catch { return null; }
                      })()}
                    </Box>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Routing Panel */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Routing Decision & Next Action</Typography>
              {routing?.status === 'no_routing_decision_yet' ? (
                <Typography color="text.secondary">No routing decision yet. Run the workflow first.</Typography>
              ) : (
                <>
                  <Box sx={{ display: 'flex', gap: 1, mb: 2, alignItems: 'center' }}>
                    <Chip
                      label={routing?.decision_code ? fmtStatus(routing.decision_code) : 'Unknown'}
                      color={
                        routing?.decision_code === 'complete_verified' ? 'success' :
                        routing?.decision_code === 'follow_up_required' ? 'warning' :
                        routing?.decision_code === 'flagged_high_risk' ? 'error' :
                        routing?.decision_code === 'rejected' ? 'error' : 'info'
                      }
                    />
                    {routing?.confidence_score != null && (
                      <Typography variant="body2">Confidence: {(routing.confidence_score * 100).toFixed(0)}%</Typography>
                    )}
                  </Box>
                  <Typography variant="body1" sx={{ mb: 1 }}>
                    <strong>Recommended Action:</strong> {routing?.recommended_procurement_action || '—'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {routing?.decision_reasoning_summary || 'No reasoning provided'}
                  </Typography>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" gutterBottom>Supporting Decision Evidence</Typography>
                  {Array.isArray(routing?.decision_evidence) && routing.decision_evidence.length > 0 ? (
                    <Stack spacing={1}>
                      {routing.decision_evidence.map((item: { source: string; finding: string }, index: number) => (
                        <Box key={`evidence-${index}`} sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{item.source}</Typography>
                          <Typography variant="body2" color="text.secondary">{item.finding}</Typography>
                        </Box>
                      ))}
                    </Stack>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      No structured decision evidence was returned for this routing decision.
                    </Typography>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Deterministic safety gate and structured audit details */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Safety Gate & Decision Reasons</Typography>
              {audit?.routing_decisions?.length > 0 ? (() => {
                const latestDecision = audit.routing_decisions[audit.routing_decisions.length - 1];
                const gate = latestDecision.safety_gate_result;
                const reasonCodes = Array.isArray(latestDecision.decision_reason_codes)
                  ? latestDecision.decision_reason_codes
                  : [];
                return (
                  <Box>
                    <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 2 }}>
                      <Chip
                        label={gate ? (gate.allowed ? 'Safety Gate Passed' : 'Safety Gate Blocked Approval') : 'Safety Gate Not Invoked'}
                        color={gate ? (gate.allowed ? 'success' : 'error') : 'default'}
                      />
                      <Chip label={`Decision: ${fmtStatus(latestDecision.decision_code || 'unknown')}`} variant="outlined" />
                    </Box>
                    {gate ? (
                      <>
                        <Typography variant="body2" sx={{ mb: 1 }}>
                          {gate.allowed
                            ? 'The deterministic checks allowed the automatic approval recommendation.'
                            : 'The deterministic checks vetoed automatic approval. The persisted routing decision reflects the safer outcome.'}
                        </Typography>
                        {reasonCodes.length > 0 && (
                          <>
                            <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>Reason Codes</Typography>
                            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                              {reasonCodes.map((code: string, index: number) => (
                                <Chip key={`${code}-${index}`} label={code} size="small" color={gate.allowed ? 'success' : 'error'} variant="outlined" />
                              ))}
                            </Box>
                          </>
                        )}
                        {Array.isArray(gate.recoverable_fields) && gate.recoverable_fields.length > 0 && (
                          <Box sx={{ mt: 2 }}>
                            <Typography variant="subtitle2">Recoverable Fields</Typography>
                            <Typography variant="body2">{gate.recoverable_fields.join(', ')}</Typography>
                          </Box>
                        )}
                        {Array.isArray(gate.review_blockers) && gate.review_blockers.length > 0 && (
                          <Box sx={{ mt: 2 }}>
                            <Typography variant="subtitle2">Review Blockers</Typography>
                            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 1 }}>
                              {gate.review_blockers.map((blocker: string, index: number) => (
                                <Chip key={`${blocker}-${index}`} label={blocker} size="small" color="error" variant="outlined" />
                              ))}
                            </Box>
                          </Box>
                        )}
                      </>
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        This decision did not invoke the approval safety gate. Its reason code is still recorded for auditability.
                      </Typography>
                    )}
                    {reasonCodes.length === 0 && (
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                        No structured reason codes were returned for this saved decision.
                      </Typography>
                    )}
                  </Box>
                );
              })() : (
                <Typography color="text.secondary">No saved routing decision is available yet. Run the workflow first.</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Follow-Up Email Panel */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Follow-Up Communication</Typography>
              {followUpMessage && (
                <Alert severity={followUpMessage.severity} sx={{ mb: 2 }}>
                  {followUpMessage.text}
                </Alert>
              )}
              {audit?.email_drafts?.length > 0 ? (
                audit.email_drafts.map((e: any) => (
                  <Box key={e.id} sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
                      <Chip label={e.draft_status} size="small" />
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{e.subject_line}</Typography>
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => setExpandedDraftId(
                        expandedDraftId === e.id ? null : e.id
                      )}
                    >
                      {expandedDraftId === e.id ? 'Hide Email Draft' : 'View Email Draft'}
                    </Button>
                    <Collapse in={expandedDraftId === e.id}>
                      <Box sx={{ mt: 2, p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                        {editDraftId === e.id ? (
                          <Stack spacing={1.5} sx={{ mb: 2 }}>
                            <TextField label="Recipient email" value={draftRecipient} onChange={event => setDraftRecipient(event.target.value)} />
                            <TextField label="Subject" value={draftSubject} onChange={event => setDraftSubject(event.target.value)} />
                            <TextField label="Message" multiline minRows={6} value={draftBody} onChange={event => setDraftBody(event.target.value)} />
                            <Box sx={{ display: 'flex', gap: 1 }}>
                              <Button variant="contained" onClick={saveFollowUpDraft} disabled={followUpBusy}>
                                {followUpBusy ? 'Saving...' : 'Save Draft'}
                              </Button>
                              <Button onClick={() => setEditDraftId(null)}>Cancel</Button>
                            </Box>
                          </Stack>
                        ) : (
                          <>
                            {e.draft_status !== 'sent' && e.draft_status !== 'discarded' && (
                              <Button size="small" sx={{ mb: 1 }} onClick={() => openDraftEditor(e)}>
                                Edit Draft
                              </Button>
                            )}
                            <Typography variant="body2">
                              <strong>To:</strong> {e.recipient_email || 'No contact email available'}
                            </Typography>
                            <Typography variant="body2" sx={{ mt: 1 }}>
                              <strong>Subject:</strong> {e.subject_line}
                            </Typography>
                            <Typography variant="body2" sx={{ mt: 1, whiteSpace: 'pre-wrap' }}>
                              {e.body_text}
                            </Typography>
                          </>
                        )}
                        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
                          <Button
                            size="small"
                            variant="contained"
                            component="a"
                            disabled={editDraftId === e.id}
                            href={`mailto:${encodeURIComponent(e.recipient_email || '')}?subject=${encodeURIComponent(e.subject_line || '')}&body=${encodeURIComponent(e.body_text || '')}`}
                          >
                            Open in Email App
                          </Button>
                        </Box>
                      </Box>
                    </Collapse>
                  </Box>
                ))
              ) : (
                <Stack spacing={1}>
                  <Typography color="text.secondary">No follow-up email draft generated yet.</Typography>
                  <Box>
                    <Button
                      variant="outlined"
                      onClick={createFollowUpDraft}
                      disabled={followUpBusy}
                    >
                      {followUpBusy ? 'Generating Draft...' : 'Draft Email for Missing Information'}
                    </Button>
                  </Box>
                </Stack>
              )}
              <Alert severity="info" sx={{ mt: 2 }}>
                This only prepares a draft; it does not send the email. When the vendor replies, return to EyeVen Form Alignment, enter the values using the field Edit buttons, and choose Save and Reprocess.
              </Alert>
            </CardContent>
          </Card>
        </Grid>

        <Dialog open={Boolean(editField)} onClose={() => setEditField(null)} fullWidth maxWidth="sm">
          <DialogTitle>{editField ? `Correct ${editField.field_label}` : 'Correct field'}</DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>
              Manually entered values are not independently verified. They will be recorded as manual corrections and rechecked through the workflow.
            </Alert>
            <TextField
              autoFocus
              fullWidth
              multiline
              minRows={2}
              label="Correct value"
              value={editFieldValue}
              onChange={event => setEditFieldValue(event.target.value)}
              sx={{ mb: 2 }}
            />
            <TextField
              select
              fullWidth
              label="Supporting document (optional)"
              value={editSourceFileId}
              onChange={event => setEditSourceFileId(event.target.value)}
            >
              <MenuItem value="">No document selected</MenuItem>
              {(caseData.uploaded_files || []).map(file => (
                <MenuItem key={file.id} value={file.id}>{file.original_filename}</MenuItem>
              ))}
            </TextField>
            <Button component="label" variant="outlined" disabled={recoveryBusy} sx={{ mt: 1 }}>
              {recoveryBusy ? 'Uploading...' : 'Upload Supporting Document'}
              <input
                hidden
                type="file"
                accept=".pdf,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.zip"
                onChange={uploadFieldSourceDocument}
              />
            </Button>
            {fieldSourceUploadMessage && (
              <Alert severity="info" sx={{ mt: 1 }}>
                {fieldSourceUploadMessage}
              </Alert>
            )}
            <FormControlLabel
              control={
                <Checkbox
                  checked={confirmedAgainstSource}
                  onChange={event => setConfirmedAgainstSource(event.target.checked)}
                  disabled={editVendorResponse}
                />
              }
              label="I checked this value against the selected source document"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={editVendorResponse}
                  onChange={event => {
                    setEditVendorResponse(event.target.checked);
                    if (event.target.checked) {
                      setEditSourceFileId('');
                      setConfirmedAgainstSource(false);
                    }
                  }}
                />
              }
              label="This value was provided by the vendor in their reply"
            />
            {editVendorResponse && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                EyeVen will not request this same field again. The value is still manually transcribed and may require human review.
              </Typography>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setEditField(null)}>Cancel</Button>
            <Button variant="contained" onClick={saveFieldCorrection} disabled={recoveryBusy}>
              {recoveryBusy ? 'Saving and reprocessing...' : 'Save and Reprocess'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Audit Trail Panel */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Audit Trail</Typography>
              {audit && (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Stage</TableCell>
                      <TableCell>Result</TableCell>
                      <TableCell>Confidence</TableCell>
                      <TableCell>Timestamp</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {audit.verification_runs?.map((v: any) => (
                      <TableRow key={`v-${v.id}`}>
                        <TableCell>Verification</TableCell>
                        <TableCell>{v.legitimacy_status || '—'}</TableCell>
                        <TableCell>{v.confidence_score ? (v.confidence_score * 100).toFixed(0) + '%' : '—'}</TableCell>
                        <TableCell>{v.created_at ? new Date(v.created_at).toLocaleString() : '—'}</TableCell>
                      </TableRow>
                    ))}
                    {audit.policy_runs?.map((p: any) => (
                      <TableRow key={`p-${p.id}`}>
                        <TableCell>Policy Evaluation</TableCell>
                        <TableCell>{p.risk_band || '—'}</TableCell>
                        <TableCell>{p.policy_confidence_score ? (p.policy_confidence_score * 100).toFixed(0) + '%' : '—'}</TableCell>
                        <TableCell>{p.created_at ? new Date(p.created_at).toLocaleString() : '—'}</TableCell>
                      </TableRow>
                    ))}
                    {audit.routing_decisions?.map((r: any) => (
                      <TableRow key={`r-${r.id}`}>
                        <TableCell>Routing</TableCell>
                        <TableCell>{r.decision_code || '—'}</TableCell>
                        <TableCell>{r.confidence_score ? (r.confidence_score * 100).toFixed(0) + '%' : '—'}</TableCell>
                        <TableCell>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</TableCell>
                      </TableRow>
                    ))}
                    {audit.reprocessing_runs?.map((rp: any) => (
                      <TableRow key={`rp-${rp.id}`}>
                        <TableCell>Reprocessing</TableCell>
                        <TableCell>{rp.run_status} ({rp.previous_case_state} → {rp.new_case_state || '...'})</TableCell>
                        <TableCell>—</TableCell>
                        <TableCell>{rp.started_at ? new Date(rp.started_at).toLocaleString() : '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

export default CaseDetailPage;
