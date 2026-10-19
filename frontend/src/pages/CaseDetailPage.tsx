import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Paper, Button,
  Divider, CircularProgress, Accordion, AccordionSummary, AccordionDetails, Alert, Stack,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import apiClient from '../services/apiClient';
import { caseService } from '../services/caseService';
import { auditService } from '../services/auditService';
import { VendorCase, FormFieldValue } from '../types';

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

const CaseDetailPage: React.FC = () => {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const [caseData, setCaseData] = useState<VendorCase | null>(null);
  const [formFields, setFormFields] = useState<FormFieldValue[]>([]);
  const [verification, setVerification] = useState<any>(null);
  const [policy, setPolicy] = useState<any>(null);
  const [routing, setRouting] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [workflowTrace, setWorkflowTrace] = useState<any[]>([]);
  const [workflowMessage, setWorkflowMessage] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      if (!caseId || !/^\\d+$/.test(caseId)) {
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

  const runWorkflow = async () => {
    if (!caseId) return;
    setRunning(true);
    setWorkflowMessage(null);
    try {
      const workflowResponse = await caseService.runWorkflow(parseInt(caseId));
      const trace = workflowResponse.data?.trace;
      setWorkflowTrace(Array.isArray(trace) ? trace : []);
      setWorkflowMessage(
        Array.isArray(trace) && trace.length > 0
          ? 'Workflow finished. The trace below shows the stages executed in this run.'
          : 'The workflow returned no stage trace. The case may already be in a terminal state.'
      );

      // Refresh the case panels without a full page reload so the execution trace remains visible.
      const id = parseInt(caseId);
      const [c, fa, v, p, r, a] = await Promise.all([
        caseService.getCaseDetail(id),
        apiClient.get(`/workflow/${id}/form-alignment`),
        caseService.getVerification(id),
        caseService.getPolicy(id),
        caseService.getRouting(id),
        auditService.getAuditTrail(id),
      ]);
      setCaseData(c.data);
      setFormFields(fa.data?.fields || []);
      setVerification(v.data);
      setPolicy(p.data);
      setRouting(r.data);
      setAudit(a.data);
    } catch (e) {
      console.error('Workflow run failed', e);
      setWorkflowMessage('Workflow execution failed. Check the backend logs for details.');
    } finally {
      setRunning(false);
    }
  };

  if (loading) return <CircularProgress />;
  if (!caseData) return <Typography>Case not found</Typography>;

  const populated = formFields.filter(f => f.value_text).length;
  const missing = formFields.filter(f => f.status === 'missing').length;
  const conflicting = formFields.filter(f => f.status === 'conflicting').length;

  const sections = Array.from(new Set(formFields.map(f => f.section_name)));

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
          <Button variant="contained" startIcon={<PlayArrowIcon />} onClick={runWorkflow} disabled={running}>
            {running ? 'Running...' : 'Run Workflow'}
          </Button>
        </Box>
      </Box>

      {workflowMessage && (
        <Alert severity={workflowTrace.length > 0 ? 'success' : 'info'} sx={{ mb: 2 }}>
          {workflowMessage}
        </Alert>
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
              {workflowTrace.map((item: any, index: number) => (
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
              {audit?.email_drafts?.length > 0 ? (
                audit.email_drafts.map((e: any) => (
                  <Box key={e.id} sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
                      <Chip label={e.draft_status} size="small" />
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{e.subject_line}</Typography>
                    </Box>
                    <Button size="small" variant="outlined" onClick={() => navigate(`/cases/${caseId}`)}>
                      View Full Email Draft
                    </Button>
                  </Box>
                ))
              ) : (
                <Typography color="text.secondary">No follow-up emails generated yet.</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

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
