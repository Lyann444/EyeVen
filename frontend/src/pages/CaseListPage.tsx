import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Paper, Chip, CircularProgress, TextField,
} from '@mui/material';
import { caseService } from '../services/caseService';
import { VendorCase } from '../types';

const riskChipColor = (r: string | null) => {
  switch (r) {
    case 'low': return 'success';
    case 'medium': return 'warning';
    case 'high': return 'error';
    case 'critical': return 'error';
    default: return 'default';
  }
};

const statusLabel = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

const CaseListPage: React.FC = () => {
  const navigate = useNavigate();
  const [cases, setCases] = useState<VendorCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const res = await caseService.listCases();
        setCases(res.data);
      } catch (e) {
        console.error('Failed to load cases', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const filtered = cases.filter(c =>
    (c.vendor_display_name || '').toLowerCase().includes(search.toLowerCase()) ||
    c.case_reference.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) return <CircularProgress />;

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Vendor Cases</Typography>

      <TextField
        label="Search by vendor name or case reference"
        value={search}
        onChange={e => setSearch(e.target.value)}
        fullWidth
        sx={{ mb: 3 }}
      />

      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Reference</TableCell>
              <TableCell>Vendor</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Intake Type</TableCell>
              <TableCell>Risk</TableCell>
              <TableCell>Last Updated</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center">
                  <Typography color="text.secondary">No cases found</Typography>
                </TableCell>
              </TableRow>
            ) : (
              filtered.map(c => (
                <TableRow
                  key={c.id}
                  hover
                  onClick={() => navigate(`/cases/${c.id}`)}
                  sx={{ cursor: 'pointer' }}
                >
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{c.case_reference}</Typography></TableCell>
                  <TableCell>{c.vendor_display_name || '—'}</TableCell>
                  <TableCell>
                    <Chip
                      label={statusLabel(c.current_state)}
                      size="small"
                      color={
                        c.current_state === 'completed' ? 'success' :
                        c.current_state === 'rejected' ? 'error' :
                        c.current_state === 'flagged_high_risk' ? 'error' :
                        c.current_state === 'pending_follow_up' ? 'warning' :
                        'default'
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={c.intake_type === 'procurement_form_present' ? 'Form Present' : 'Form Absent'}
                      size="small"
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>
                    {c.risk_band ? (
                      <Chip label={c.risk_band.toUpperCase()} size="small" color={riskChipColor(c.risk_band) as any} />
                    ) : '—'}
                  </TableCell>
                  <TableCell>{c.updated_at ? new Date(c.updated_at).toLocaleDateString() : '—'}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
};

export default CaseListPage;
