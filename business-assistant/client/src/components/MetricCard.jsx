export default function MetricCard({ value, label }) {
  return (
    <div className="metric-card">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
