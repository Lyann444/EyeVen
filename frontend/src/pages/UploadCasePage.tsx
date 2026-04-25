import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Button, TextField, Card, CardContent, Alert, LinearProgress,
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
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

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setSuccess('');

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
      setTimeout(() => navigate(`/cases/${newCaseId}`), 1500);
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

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Upload Vendor Submission</Typography>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Case Information</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Case reference will be generated automatically on submission.
          </Typography>
          <TextField
            label="Vendor Display Name"
            value={vendorName}
            onChange={e => setVendorName(e.target.value)}
            fullWidth
            placeholder="e.g., Acme Technologies Sdn Bhd"
          />
        </CardContent>
      </Card>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Upload Files</Typography>
          <Button variant="outlined" component="label" startIcon={<CloudUploadIcon />} sx={{ mb: 2 }}>
            Select Files
            <input type="file" hidden multiple accept=".pdf,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.zip"
              onChange={e => setFiles(e.target.files)} />
          </Button>
          {files && (
            <Typography variant="body2" color="text.secondary">
              {files.length} file(s) selected: {Array.from(files).map(f => f.name).join(', ')}
            </Typography>
          )}
        </CardContent>
      </Card>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Vendor Email (Optional)</Typography>
          <TextField
            label="Paste email content from vendor"
            value={emailContent}
            onChange={e => setEmailContent(e.target.value)}
            fullWidth
            multiline
            rows={4}
            placeholder="Copy and paste the vendor's introductory email here..."
          />
        </CardContent>
      </Card>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }}>{success}</Alert>}
      {loading && <LinearProgress sx={{ mb: 2 }} />}

      <Button variant="contained" size="large" onClick={handleSubmit} disabled={loading}>
        Create Case & Upload
      </Button>
    </Box>
  );
};

export default UploadCasePage;
