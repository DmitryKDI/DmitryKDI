function formatMoney(n) {
  if (n === null || n === undefined) return '—';
  return new Intl.NumberFormat('ru-RU').format(n) + ' ₽';
}

export default function DealsTable({ deals, onSelect }) {
  return (
    <div className="table-scroll">
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Объект</th>
              <th>Клиент</th>
              <th>Тип</th>
              <th>Этап</th>
              <th>Бюджет</th>
              <th>Менеджер</th>
            </tr>
          </thead>
          <tbody>
            {deals.map((d) => (
              <tr key={d.id} className={onSelect ? 'row-clickable' : ''} onClick={() => onSelect && onSelect(d)}>
                <td>{d.address}</td>
                <td>{d.clientName}</td>
                <td>{d.type}</td>
                <td>
                  <span className={`stage-pill${d.stage === 'завершено' ? ' done' : ''}`}>{d.stage}</span>
                </td>
                <td>{formatMoney(d.budget)}</td>
                <td>{d.manager}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {onSelect && <p className="hint">Нажмите на объект, чтобы открыть карточку и документы.</p>}
    </div>
  );
}
