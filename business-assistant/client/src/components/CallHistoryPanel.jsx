function formatDuration(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function CallHistoryPanel({ calls }) {
  return (
    <div className="table-scroll">
      <div className="card">
        {calls.length === 0 ? (
          <div className="empty">Звонков пока нет</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Телефон</th>
                <th>Направление</th>
                <th>Итог</th>
                <th>Длительность</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}>
                  <td>{c.date}</td>
                  <td>{c.phone}</td>
                  <td>{c.direction}</td>
                  <td>{c.outcome}</td>
                  <td>{formatDuration(c.durationSec)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="hint">
        Демо-данные — интеграция со звонками через LPTracker пока не подключена (Этап 5).
      </p>
    </div>
  );
}
