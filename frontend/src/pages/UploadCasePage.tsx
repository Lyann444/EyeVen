import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, TextField, Card, CardContent, Alert, LinearProgress,
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import apiClient from '../services/apiClient';
import { caseService } from '../services/caseService';

const UploadCasePage: React.FC = () => {
  const navigate = useNavigate();
  const [files, setFiles] = useState<FileList | null>(null);
  const [emailContent, setEmailContent] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // Post-upload state
  const [createdCaseId, setCreatedCaseId] = useState<number | null>(null);
  const [workflowRunning, setWorkflowRunning] = useState(false);
  const [workflowError, setWorkflowError] = useState('');

  // Drag state
  const [dragActive, setDragActive] = useState(false);

  const generateCaseReference = () => {
    const now = new Date();
    const ts = now.getFullYear().toString() +
      (now.getMonth() + 1).toString().padStart(2, '0') +
      now.getDate().toString().padStart(2, '0') + '-' +
      now.getHours().toString().padStart(2, '0') +
      now.getMinutes().toString().padStart(2, '0') +
      now.getSeconds().toString().padStart(2, '0');
    return `VC-${ts}`;
  };

  const handleFiles = useCallback((fileList: FileList | null) => {
    if (fileList && fileList.length > 0) {
      setFiles(fileList);
      setError('');
    }
  }, []);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setSuccess('');
    setCreatedCaseId(null);
    setWorkflowError('');

    try {
      const caseReference = generateCaseReference();
      const createRes = await caseService.createCase({
        case_reference: caseReference,
        vendor_display_name: vendorName || null,
        intake_type: 'procurement_form_absent',
      });

      const newCaseId = createRes.data.id;

      if (files && files.length > 0) {
        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
          formData.append('files', files[i]);
        }
        if (emailContent) {
          formData.append('email_content', emailContent);
        }
        await apiClient.post(`/uploads/${newCaseId}`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
      }

      setSuccess(`Case created successfully`);
      setCreatedCaseId(newCaseId);
    } catch (e: any) {
      const serverError = e?.response?.data?.error;
      if (serverError === 'Case reference already exists') {
        setError('This case reference already exists. Use a different reference or go to the existing case.');
      } else {
        setError(serverError || 'Failed to create case');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleRunWorkflow = async () => {
    if (!createdCaseId) return;
    setWorkflowRunning(true);
    setWorkflowError('');
    try {
      await caseService.runWorkflow(createdCaseId);
      navigate(`/cases/${createdCaseId}`);
    } catch (e: any) {
      const msg = e?.response?.data?.error || e?.message || 'Workflow run failed';
      setWorkflowError(msg);
    } finally {
      setWorkflowRunning(false);
    }
  };

  const resetForm = () => {
    setFiles(null);
    setEmailContent('');
    setVendorName('');
    setSuccess('');
    setError('');
    setCreatedCaseId(null);
    setWorkflowError('');
  };

  // Post-upload view: show Run Workflow button prominently
  if (createdCaseId) {
    return (
      <Box>
        <Alert severity="success" sx={{ mb: 3, fontSize: '1.1rem', py: 2 }}>
          Case created & files uploaded successfully!
        </Alert>

        <Card sx={{ mb: 3, p: 2, textAlign: 'center' }}>
          <CardContent>
            <Typography variant="h5" gutterBottom sx={{ fontWeight: 700 }}>
              Ready to analyze your vendor submission
            </Typography>
            <Typography color="text.secondary" sx={{ mb: 3 }}>
              Run the workflow to extract data, verify the vendor, assess risk, and determine routing.
            </Typography>

            <Box sx={{ display: 'flex', justifyContent: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Button
                variant="contained"
                size="large"
                startIcon={<PlayArrowIcon />}
                onClick={handleRunWorkflow}
                disabled={workflowRunning}
                sx={{ px: 5, py: 1.5, fontSize: '1.1rem' }}
              >
                {workflowRunning ? 'Running Workflow...' : 'Run Workflow Now'}
              </Button>
              <Button
                variant="outlined"
                size="large"
                onClick={() => navigate(`/cases/${createdCaseId}`)}
                sx={{ px: 4, py: 1.5 }}
              >
                View Case Details
              </Button>
            </Box>

            {workflowError && (
              <Alert severity="error" sx={{ mt: 2 }}>{workflowError}</Alert>
            )}
            {workflowRunning && <LinearProgress sx={{ mt: 2 }} />}
          </CardContent>
        </Card>

        <Box sx={{ textAlign: 'center', mt: 2 }}>
          <Button variant="text" onClick={resetForm}>
            Upload Another Case
          </Button>
        </Box>
      </Box>
    );
  }

  return (
    <Box>
      {/* Hero / Upload Zone */}
      <Box sx={{ textAlign: 'center', mb: 4 }}>
        <Typography variant="h4" gutterBottom sx={{ fontWeight: 700 }}>
          Upload Vendor Submission
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Drag & drop your vendor files below, or click to browse. We'll analyze them instantly.
        </Typography>
      </Box>

      {/* Large Drag-and-Drop Upload Zone */}
      <Box
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={() => document.getElementById('file-input')?.click()}
        sx={{
          border: '2px dashed',
          borderColor: dragActive ? 'primary.main' : 'divider',
          borderRadius: 3,
          p: 6,
          mb: 3,
          textAlign: 'center',
          cursor: 'pointer',
          bgcolor: dragActive ? 'primary.50' : 'background.paper',
          transition: 'all 0.2s ease',
          '&:hover': {
            borderColor: 'primary.main',
            bgcolor: 'primary.50',
          },
        }}
      >
        <CloudUploadIcon sx={{ fontSize: 56, color: 'primary.main', mb: 2 }} />
        <Typography variant="h6" gutterBottom>
          {dragActive ? 'Drop files here...' : 'Drag & drop files here'}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          or click to browse
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Supported: PDF, XLSX, XLS, CSV, PNG, JPG, ZIP
        </Typography>
        <input
          id="file-input"
          type="file"
          hidden
          multiple
          accept=".pdf,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.zip"
          onChange={e => handleFiles(e.target.files)}
        />
      </Box>

      {/* Selected Files Display */}
      {files && files.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                {files.length} file(s) selected
              </Typography>
              <Button size="small" onClick={() => setFiles(null)}>
                Clear
              </Button>
            </Box>
            {Array.from(files).map((f, i) => (
              <Typography key={i} variant="body2" color="text.secondary">
                {f.name} ({(f.size / 1024).toFixed(1)} KB)
              </Typography>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Vendor Name */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <TextField
            label="Vendor Display Name"
            value={vendorName}
            onChange={e => setVendorName(e.target.value)}
            fullWidth
            placeholder="e.g., Acme Technologies Sdn Bhd"
            helperText="Optional — will be auto-extracted if left blank"
          />
        </CardContent>
      </Card>

      {/* Email Content */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            Vendor Email (Optional)
          </Typography>
          <TextField
            label="Paste email content from vendor"
            value={emailContent}
            onChange={e => setEmailContent(e.target.value)}
            fullWidth
            multiline
            rows={3}
            placeholder="Copy and paste the vendor's introductory email here..."
          />
        </CardContent>
      </Card>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }}>{success}</Alert>}
      {loading && <LinearProgress sx={{ mb: 2 }} />}

      {/* Submit Button — prominent */}
      <Button
        variant="contained"
        size="large"
        onClick={handleSubmit}
        disabled={loading || (!files || files.length === 0)}
        startIcon={<CloudUploadIcon />}
        sx={{ px: 5, py: 1.5, fontSize: '1.05rem' }}
      >
        Create Case & Upload
      </Button>
    </Box>
  );
};

export default UploadCasePage;
