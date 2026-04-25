import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ThemeProvider, CssBaseline } from '@mui/material';
import theme from './theme/theme';
import AppLayout from './components/layout/AppLayout';
import UploadCasePage from './pages/UploadCasePage';
import DashboardPage from './pages/DashboardPage';
import CaseListPage from './pages/CaseListPage';
import CaseDetailPage from './pages/CaseDetailPage';

const App: React.FC = () => (
  <ThemeProvider theme={theme}>
    <CssBaseline />
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<UploadCasePage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/cases" element={<CaseListPage />} />
          <Route path="/cases/:caseId" element={<CaseDetailPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </ThemeProvider>
);

export default App;
