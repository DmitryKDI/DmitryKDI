import { useState } from 'react';
import BottomNav from './components/BottomNav.jsx';
import Dashboard from './components/Dashboard.jsx';
import SalesPanel from './components/SalesPanel.jsx';
import ChatPanel from './components/ChatPanel.jsx';

const TABS = {
  dashboard: { label: 'Дашборд', icon: '📊' },
  sales: { label: 'Продажи', icon: '💼' },
  chat: { label: 'Ассистент', icon: '💬' },
};

export default function App() {
  const [tab, setTab] = useState('dashboard');

  return (
    <div className="app">
      <header className="app-header">
        <div className="logo" />
        <h1>{TABS[tab].label}</h1>
        <span className="badge">Демо · мок-данные</span>
      </header>

      <main className={`app-main${tab === 'chat' ? ' app-main--chat' : ''}`}>
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'sales' && <SalesPanel />}
        {tab === 'chat' && <ChatPanel />}
      </main>

      <BottomNav tabs={TABS} active={tab} onChange={setTab} />
    </div>
  );
}
