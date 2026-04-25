import apiClient from './apiClient';
import { VendorCase } from '../types';

export const caseService = {
  createCase: (data: Partial<VendorCase>) =>
    apiClient.post<VendorCase>('/cases', data),

  listCases: () =>
    apiClient.get<VendorCase[]>('/cases'),

  getCaseDetail: (id: number) =>
    apiClient.get<VendorCase>(`/cases/${id}`),

  markReviewed: (id: number) =>
    apiClient.post(`/cases/${id}/review`),

  runWorkflow: (id: number) =>
    apiClient.post(`/workflow/${id}/run`),

  rerunStages: (id: number) =>
    apiClient.post(`/workflow/${id}/rerun`),

  getVerification: (id: number) =>
    apiClient.get(`/workflow/${id}/verification`),

  getPolicy: (id: number) =>
    apiClient.get(`/workflow/${id}/policy`),

  getRouting: (id: number) =>
    apiClient.get(`/workflow/${id}/routing`),

  createFollowUp: (id: number) =>
    apiClient.post(`/workflow/${id}/follow-up`),

  uploadFollowUpFiles: (id: number, files: File[], reasonSummary?: string) => {
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    if (reasonSummary) formData.append('reason_summary', reasonSummary);
    return apiClient.post(`/uploads/${id}/follow-up`, formData);
  },

  reprocessFollowUp: (id: number) =>
    apiClient.post(`/workflow/${id}/follow-up/reprocess`),
};
