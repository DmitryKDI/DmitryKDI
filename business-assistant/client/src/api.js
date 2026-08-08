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
};
