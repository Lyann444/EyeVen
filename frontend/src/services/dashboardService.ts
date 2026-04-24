import apiClient from './apiClient';

export const dashboardService = {
  getSummary: () =>
    apiClient.get('/dashboard/summary'),

  getStatusDistribution: () =>
    apiClient.get('/dashboard/status-distribution'),

  getRiskDistribution: () =>
    apiClient.get('/dashboard/risk-distribution'),
};
