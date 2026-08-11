async function request(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Ошибка запроса: ${res.status}`);
  }
  return data;
}

export const api = {
  getDashboard: () => request('/api/dashboard'),
  getSalesManagers: () => request('/api/sales/managers'),
  getSalesOverview: () => request('/api/sales/overview'),
  getManagerContacts: (manager) => request(`/api/sales/contacts?manager=${encodeURIComponent(manager)}`),
  sendChatMessage: (history, message) =>
    request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ history, message }),
    }),

  getDocuments: (dealId) => request(`/api/deals/${encodeURIComponent(dealId)}/documents`),
  generateDocument: (dealId, type) =>
    request(`/api/deals/${encodeURIComponent(dealId)}/documents/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type }),
    }),
  generateSmeta: (dealId, area, level) =>
    request(`/api/deals/${encodeURIComponent(dealId)}/documents/smeta`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ area, level }),
    }),
  uploadDocument: (dealId, type, files) => {
    const formData = new FormData();
    formData.append('type', type);
    for (const file of files) formData.append('files', file);
    return request(`/api/deals/${encodeURIComponent(dealId)}/documents/upload`, {
      method: 'POST',
      body: formData,
    });
  },
  deleteDocument: (id) => request(`/api/documents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  documentDownloadUrl: (id) => `/api/documents/${encodeURIComponent(id)}/download`,
  documentFileDownloadUrl: (fileId) => `/api/documents/files/${encodeURIComponent(fileId)}/download`,
};
