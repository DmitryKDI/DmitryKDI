import { useEffect, useState } from 'react';
import { api } from '../api.js';

const OWNER_VIEW = '__owner__';

function formatMoney(n) {
  return new Intl.NumberFormat('ru-RU').format(n || 0) + ' ₽';
}

export default function SalesPanel() {
  const [managers, setManagers] = useState([]);
  const [view, setView] = useState(OWNER_VIEW);
  const [overview, setOverview] = useState(null);
  const [contacts, setContacts] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .getSalesManagers()
      .then((d) => setManagers(d.managers))
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    setError(null);
    if (view === OWNER_VIEW) {
      setContacts(null);
      api
        .getSalesOverview()
        .then(setOverview)
        .catch((err) => setError(err.message));
    } else {
      setOverview(null);
      api
        .getManagerContacts(view)
        .then((d) => setContacts(d.contacts))
        .catch((err) => setError(err.message));
    }
  }, [view]);

  return (
    <>
      <div className="role-select-row">
        <select value={view} onChange={(e) => setView(e.target.value)}>
          <option value={OWNER_VIEW}>👑 Начальник отдела продаж (все менеджеры)</option>
          {managers.map((m) => (
            <option key={m} value={m}>
              {m} (закреплённые контакты)
            </option>
          ))}
        </select>
      </div>

      <div className="privacy-note">
        🔒 Демо-переключатель ролей — в MVP ещё нет логина (Этап 6). В боевой версии
        доступ к телефонам клиентов будет жёстко привязан к роли: менеджер видит
        только своих закреплённых клиентов в Битрикс24.
      </div>

      {error && <div className="error-banner">{error}</div>}

      {view === OWNER_VIEW && overview && (
        <>
          <h2>Рейтинг менеджеров · закрытые сделки</h2>
          <div className="card">
            {overview.ranking.map((row, i) => (
              <div className="ranking-row" key={row.manager}>
                <div className="ranking-rank">{i + 1}</div>
                <div className="ranking-info">
                  <div className="name">{row.manager}</div>
                  <div className="sub">
                    {row.dealsWonCount} закрытых сделок · {row.activeDealsCount} в работе
                  </div>
                </div>
                <div className="ranking-sum">{formatMoney(row.dealsWonSum)}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {view !== OWNER_VIEW && contacts && (
        <>
          <h2>Закреплённые клиенты — {view}</h2>
          <div className="table-scroll">
            <div className="card">
              <table>
                <thead>
                  <tr>
                    <th>Клиент</th>
                    <th>Телефон</th>
                    <th>Объект</th>
                    <th>Этап</th>
                    <th>Бюджет</th>
                  </tr>
                </thead>
                <tbody>
                  {contacts.map((c) => (
                    <tr key={c.dealId}>
                      <td>{c.clientName}</td>
                      <td>{c.clientPhone}</td>
                      <td>{c.address}</td>
                      <td>
                        <span className={`stage-pill${c.stage === 'завершено' ? ' done' : ''}`}>{c.stage}</span>
                      </td>
                      <td>{c.budget ? formatMoney(c.budget) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  );
}
