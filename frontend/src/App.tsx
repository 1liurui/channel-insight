import { useEffect, useState } from 'react'
import './styles.css'
import { AskPanel } from './components/AskPanel'
import { AttributionPanel } from './components/AttributionPanel'
import { MetricsPanel } from './components/MetricsPanel'

type Tab = 'ask' | 'attribute' | 'metrics'
const TABS: { id: Tab; label: string }[] = [
  { id: 'ask', label: '自助取数' },
  { id: 'attribute', label: '指标归因' },
  { id: 'metrics', label: '指标口径' },
]

export default function App() {
  const [tab, setTab] = useState<Tab>('ask')
  const [health, setHealth] = useState<{ metrics: number; columns: number } | null>(null)

  useEffect(() => {
    fetch('/api/health').then((r) => r.json()).then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">渠道运营数据助手<span>问数 · 归因 · 稽核</span></div>
        <div className="health">
          {health ? <>数仓在线 · <b>{health.metrics}</b> 项口径 · <b>{health.columns}</b> 个字段</>
                  : '服务未连接'}
        </div>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} className="tab" role="tab" aria-selected={tab === t.id}
                    onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
      </header>
      <main className="main">
        {tab === 'ask' && <AskPanel />}
        {tab === 'attribute' && <AttributionPanel />}
        {tab === 'metrics' && <MetricsPanel />}
      </main>
    </div>
  )
}
