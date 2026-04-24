import React, { useEffect, useState } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, CircularProgress,
} from '@mui/material';
import { Pie } from 'react-chartjs-2';
import {
  Chart as ChartJS, ArcElement, Tooltip, Legend,
} from 'chart.js';
import { dashboardService } from '../services/dashboardService';

ChartJS.register(ArcElement, Tooltip, Legend);

const statusColors: Record<string, string> = {
  completed: '#2e7d32',
  pending_follow_up: '#ed6c02',
  flagged_high_risk: '#d32f2f',
  rejected: '#7b1fa2',
  manual_review_required: '#1565c0',
  reprocessing: '#0288d1',
  uploaded: '#9e9e9e',
  intake_received: '#757575',
  intake_classified: '#616161',
  field_alignment_completed: '#43a047',
  incomplete_after_autofill: '#ff9800',
  verification_in_progress: '#1e88e5',
  policy_review_in_progress: '#5e35b1',
};

const riskColors: Record<string, string> = {
  low: '#2e7d32',
  medium: '#ed6c02',
  high: '#d32f2f',
  critical: '#7b1fa2',
};

const DashboardPage: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);
  const [statusDist, setStatusDist] = useState<any>(null);
  const [riskDist, setRiskDist] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [s, st, r] = await Promise.all([
          dashboardService.getSummary(),
          dashboardService.getStatusDistribution(),
          dashboardService.getRiskDistribution(),
        ]);
        setSummary(s.data);
        setStatusDist(st.data);
        setRiskDist(r.data);
      } catch (e) {
        console.error('Dashboard load error', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) return <CircularProgress />;

  const statusChartData = {
    labels: Object.keys(statusDist?.distribution || {}),
    datasets: [{
      data: Object.values(statusDist?.distribution || {}),
      backgroundColor: Object.keys(statusDist?.distribution || {}).map(
        k => statusColors[k] || '#9e9e9e'
      ),
    }],
  };

  const riskChartData = {
    labels: Object.keys(riskDist?.distribution || {}),
    datasets: [{
      data: Object.values(riskDist?.distribution || {}),
      backgroundColor: Object.keys(riskDist?.distribution || {}).map(
        k => riskColors[k] || '#9e9e9e'
      ),
    }],
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>Procurement Dashboard</Typography>

      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <Card><CardContent>
            <Typography color="text.secondary" variant="body2">Total Cases</Typography>
            <Typography variant="h4">{summary?.total_cases ?? 0}</Typography>
          </CardContent></Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <Card><CardContent>
            <Typography color="text.secondary" variant="body2">Pending Follow-Up</Typography>
            <Typography variant="h4">{summary?.pending_follow_up ?? 0}</Typography>
            <Chip label="Action Needed" color="warning" size="small" sx={{ mt: 1 }} />
          </CardContent></Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <Card><CardContent>
            <Typography color="text.secondary" variant="body2">Flagged High Risk</Typography>
            <Typography variant="h4">{summary?.flagged_high_risk ?? 0}</Typography>
            <Chip label="Review Required" color="error" size="small" sx={{ mt: 1 }} />
          </CardContent></Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <Card><CardContent>
            <Typography color="text.secondary" variant="body2">Completed</Typography>
            <Typography variant="h4">{summary?.completed ?? 0}</Typography>
            <Chip label="Done" color="success" size="small" sx={{ mt: 1 }} />
          </CardContent></Card>
        </Grid>
      </Grid>

      <Grid container spacing={3}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card><CardContent>
            <Typography variant="h6" gutterBottom>Case Status Distribution</Typography>
            {statusDist?.distribution && Object.keys(statusDist.distribution).length > 0 ? (
              <Pie data={statusChartData} />
            ) : (
              <Typography color="text.secondary">No cases yet</Typography>
            )}
          </CardContent></Card>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card><CardContent>
            <Typography variant="h6" gutterBottom>Risk Band Distribution</Typography>
            {riskDist?.distribution && Object.keys(riskDist.distribution).length > 0 ? (
              <Pie data={riskChartData} />
            ) : (
              <Typography color="text.secondary">No risk assessments yet</Typography>
            )}
          </CardContent></Card>
        </Grid>
      </Grid>
    </Box>
  );
};

export default DashboardPage;
