import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';

export default function StageChart({ data }) {
  return (
    <div className="card chart-card">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#262b3a" vertical={false} />
          <XAxis
            dataKey="stage"
            tick={{ fill: '#8890a6', fontSize: 11 }}
            interval={0}
            angle={-20}
            textAnchor="end"
            height={60}
          />
          <YAxis tick={{ fill: '#8890a6', fontSize: 11 }} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: '#161a26', border: '1px solid #262b3a', borderRadius: 10, color: '#eef0f6' }}
            cursor={{ fill: 'rgba(109,91,255,0.08)' }}
          />
          <Bar dataKey="count" name="Объектов" fill="#6d5bff" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
