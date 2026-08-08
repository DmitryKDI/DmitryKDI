import { useEffect, useState } from 'react';
import { api } from '../api.js';
import MetricCard from './MetricCard.jsx';
import StageChart from './StageChart.jsx';
import DealsTable from './DealsTable.jsx';
import CallHistoryPanel from './CallHistoryPanel.jsx';

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .getDashboard()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  if (error) return <div className="error-banner">Не удалось загрузить дашборд: {error}</div>;
  if (!data) return <div className="loading">Загрузка…</div>;

  const { metrics, deals, callHistory } = data;

  return (
    <>
      <h2>Показатели</h2>
      <div className="metrics-grid">
        <MetricCard value={metrics.newDealsThisMonth} label="Новых заявок за 30 дней" />
        <MetricCard value={`${metrics.conversionRate}%`} label="Конверсия в завершённые" />
        <MetricCard value={metrics.activeDeals} label="Объектов в работе" />
        <MetricCard value={metrics.openTasksCount} label="Открытых задач" />
      </div>

      <h2>Объекты по этапам</h2>
      <StageChart data={metrics.dealsByStage} />

      <h2>Сделки (объекты)</h2>
      <DealsTable deals={deals} />

      <h2>История звонков</h2>
      <CallHistoryPanel calls={callHistory} />
    </>
  );
}
