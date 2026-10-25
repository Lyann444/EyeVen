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

  uploadRecoveryFiles: (id: number, formData: FormData) =>
    apiClient.post(`/uploads/${id}/recovery`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  correctFields: (id: number, fields: Array<{
    field_name: string;
    value_text: string;
    source_file_id?: number | null;
    confirmed_against_source?: boolean;
    vendor_response?: boolean;
  }>) => apiClient.patch(`/cases/${id}/fields`, { fields }),

  updateFollowUpDraft: (caseId: number, draftId: number, values: {
    recipient_email: string | null;
    subject_line: string;
    body_text: string;
  }) => apiClient.patch(`/workflow/${caseId}/follow-up/${draftId}`, values),
};
