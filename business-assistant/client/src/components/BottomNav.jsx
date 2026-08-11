export default function BottomNav({ tabs, active, onChange }) {
  return (
    <nav className="bottom-nav">
      {Object.entries(tabs).map(([key, { label, icon }]) => (
        <button
          key={key}
          className={key === active ? 'active' : ''}
          onClick={() => onChange(key)}
        >
          <span className="icon">{icon}</span>
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}
