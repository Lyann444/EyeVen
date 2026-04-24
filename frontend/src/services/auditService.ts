import apiClient from './apiClient';

export const auditService = {
  getAuditTrail: (caseId: number) =>
    apiClient.get(`/audit/${caseId}`),

  getReprocessingHistory: (caseId: number) =>
    apiClient.get(`/audit/${caseId}/reprocessing`),
};
