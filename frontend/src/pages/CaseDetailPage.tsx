import React, { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Paper, Button,
  Divider, CircularProgress, Accordion, AccordionSummary, AccordionDetails,
  TextField,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ReplayIcon from '@mui/icons-material/Replay';
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
    case 'not_applicable': return 'default';
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
  const [followUpFiles, setFollowUpFiles] = useState<File[]>([]);
  const [followUpReason, setFollowUpReason] = useState('');
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessError, setReprocessError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const load = async () => {
      if (!caseId) return;
      try {
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
        console.error('Failed to load case', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [caseId]);

  const [runError, setRunError] = useState<string | null>(null);

  const runWorkflow = async () => {
    if (!caseId) return;
    setRunning(true);
    setRunError(null);
    try {
      const res = await caseService.runWorkflow(parseInt(caseId));
      console.log('Workflow result:', res.data);
      window.location.reload();
    } catch (e: any) {
      const msg = e?.response?.data?.error || e?.message || 'Workflow run failed';
      console.error('Workflow run failed', e);
      setRunError(msg);
    } finally {
      setRunning(false);
    }
  };
  const flagSeverityColor = (severity?: string) => {
    switch (severity) {
      case "blocking":
        return "error";
      case "warning":
        return "warning";
      case "informational":
        return "info";
      default:
        return "default";
    }
  };
  const handleGenerateEmail = async () => {
    if (!caseId) return;
    try {
      // Calls the POST route you just added to the backend to make the AI write the draft
      await caseService.createFollowUp(parseInt(caseId));
      // Reload the page to fetch the newly generated draft into the UI
      window.location.reload(); 
    } catch (e) {
      console.error("Failed to generate email", e);
      alert("Failed to generate email. Check the console for errors.");
    }
  };

  const handleOpenOutlook = (recipientEmail: string, subjectLine: string, bodyText: string) => {
    // encodeURIComponent ensures spaces and line-breaks format perfectly in Outlook
    const subject = encodeURIComponent(subjectLine || "");
    const body = encodeURIComponent(bodyText || "");
    const recipient = recipientEmail || "";
    
    // Natively opens the default email client (Outlook)
    window.location.href = `mailto:${recipient}?subject=${subject}&body=${body}`;
  };

  const handleFollowUpUpload = async () => {
    if (!caseId || followUpFiles.length === 0) return;
    setReprocessing(true);
    setReprocessError(null);
    try {
      await caseService.uploadFollowUpFiles(parseInt(caseId), followUpFiles, followUpReason || undefined);
      await caseService.reprocessFollowUp(parseInt(caseId));
      window.location.reload();
    } catch (e: any) {
      const msg = e?.response?.data?.error || e?.message || 'Follow-up reprocessing failed';
      console.error('Follow-up reprocessing failed', e);
      setReprocessError(msg);
    } finally {
      setReprocessing(false);
    }
  };

  if (loading) return <CircularProgress />;
  if (!caseData) return <Typography>Case not found</Typography>;

  const populated = formFields.filter(f => f.value_text && f.status !== 'not_applicable').length;
  const missing = formFields.filter(f => f.status === 'missing').length;
  const notApplicable = formFields.filter(f => f.status === 'not_applicable').length;
  const issues = formFields.filter(f =>
    ['conflicting', 'mismatched_with_evidence', 'unverifiable', 'low_confidence'].includes(f.status)
  ).length;

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
          {runError && <Typography color="error" variant="body2" sx={{ ml: 1 }}>{runError}</Typography>}
        </Box>
      </Box>

      <Grid container spacing={3}>
        {/* Form Alignment Panel */}
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>EyeVen Form Alignment</Typography>
              <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
                <Chip label={`${populated} Populated`} color="success" size="small" />
                <Chip label={`${missing} Missing`} color="default" size="small" />
                <Chip label={`${issues} Issues`} color="error" size="small" />
                {notApplicable > 0 && <Chip label={`${notApplicable} N/A`} color="default" size="small" variant="outlined" />}
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
                            <Chip key={i} label={f.flag_type || f} size="small" sx={{ mr: 0.5, mb: 0.5 }} color={flagSeverityColor(f.severity) as any} />
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

        {/* Follow-Up Reprocessing Panel — only shown when case is pending_follow_up */}
        {caseData.current_state === 'pending_follow_up' && (
          <Grid size={12}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>Follow-Up Reprocessing</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  This case requires additional information. Upload the missing documents and reprocess the case.
                </Typography>

                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Button
                      variant="outlined"
                      startIcon={<UploadFileIcon />}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      Choose Files
                    </Button>
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      hidden
                      onChange={(e) => {
                        const fileList = e.target.files;
                        if (fileList) setFollowUpFiles(Array.from(fileList));
                      }}
                    />
                    {followUpFiles.length > 0 && (
                      <Typography variant="body2">
                        {followUpFiles.length} file(s) selected: {followUpFiles.map(f => f.name).join(', ')}
                      </Typography>
                    )}
                  </Box>

                  <TextField
                    label="Reason Summary (optional)"
                    placeholder="e.g. Vendor supplied missing SSM certificate"
                    size="small"
                    fullWidth
                    value={followUpReason}
                    onChange={(e) => setFollowUpReason(e.target.value)}
                  />

                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Button
                      variant="contained"
                      startIcon={<ReplayIcon />}
                      onClick={handleFollowUpUpload}
                      disabled={reprocessing || followUpFiles.length === 0}
                    >
                      {reprocessing ? 'Reprocessing...' : 'Upload & Reprocess'}
                    </Button>
                    {reprocessError && (
                      <Typography color="error" variant="body2">{reprocessError}</Typography>
                    )}
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        )}

        {/* Follow-Up Email Panel */}
        {caseData.current_state === 'pending_follow_up' && (
        <Grid size={12}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
                <Typography variant="h6">Follow-Up Communication</Typography>
                <Button 
                  variant="outlined" 
                  color="primary" 
                  onClick={handleGenerateEmail}
                >
                  Draft Follow-up Email
                </Button>
              </Box>

              {audit?.email_drafts?.length > 0 ? (
                audit.email_drafts.map((e: any) => (
                  <Box key={e.id} sx={{ mb: 3, p: 2, border: '1px solid #e0e0e0', borderRadius: 2 }}>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      <strong>To:</strong> {e.recipient_email || "[Vendor Email]"}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      <strong>Subject:</strong> {e.subject_line}
                    </Typography>

                    <Paper elevation={0} sx={{ p: 2, my: 2, bgcolor: '#f8f9fa', whiteSpace: 'pre-wrap', fontSize: '0.875rem' }}>
                      {e.body_text}
                    </Paper>

                    <Button 
                      variant="contained" 
                      color="primary"
                      onClick={() => handleOpenOutlook(e.recipient_email, e.subject_line, e.body_text)}
                    >
                      Send in Outlook
                    </Button>
                  </Box>
                ))
              ) : (
                <Typography color="text.secondary">
                  No follow-up emails generated yet. Click the button above to generate one.
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
        )}

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
