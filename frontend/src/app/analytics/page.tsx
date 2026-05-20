'use client';
import { useState } from 'react';
import Sidebar from '@/components/Sidebar';

const REVENUE_DATA = [
  { month: 'Oct', revenue: 180000, collected: 160000, overdue: 20000 },
  { month: 'Nov', revenue: 220000, collected: 195000, overdue: 25000 },
  { month: 'Dec', revenue: 195000, collected: 180000, overdue: 15000 },
  { month: 'Jan', revenue: 260000, collected: 230000, overdue: 30000 },
  { month: 'Feb', revenue: 240000, collected: 215000, overdue: 25000 },
  { month: 'Mar', revenue: 310000, collected: 280000, overdue: 30000 },
];

const TOP_CLIENTS = [
  { name: 'Acme Corp', revenue: 124000, invoices: 8, risk: 'low' },
  { name: 'Infosys Ltd', revenue: 98000, invoices: 5, risk: 'low' },
  { name: 'TechStart Inc', revenue: 76000, invoices: 12, risk: 'medium' },
  { name: 'Global Media', revenue: 54000, invoices: 4, risk: 'high' },
  { name: 'Sunrise Hotels', revenue: 41000, invoices: 7, risk: 'medium' },
];

const riskColor = { low: 'var(--green)', medium: 'var(--yellow)', high: 'var(--red)' } as const;
const riskBadge = { low: 'badge-green', medium: 'badge-yellow', high: 'badge-red' } as const;

export default function AnalyticsPage() {
  const [tab, setTab] = useState<'revenue' | 'clients' | 'late' | 'recurring'>('revenue');
  const fmt = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  const maxRev = Math.max(...REVENUE_DATA.map(d => d.revenue));

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Analytics</div>
          <div className="topbar-right">
            <div className="ai-health-pill">
              <div className="ai-health-dot" />
              <span>AI-Powered Analytics</span>
            </div>
          </div>
        </header>

        <div className="page-content">
          {/* KPI row */}
          <div className="stats-grid" style={{ marginBottom: 24 }}>
            {[
              { label: 'Total Revenue (6mo)', value: fmt(REVENUE_DATA.reduce((s,d) => s+d.revenue, 0)), color: 'blue', icon: '₹' },
              { label: 'Collection Rate', value: '89.4%', color: 'green', icon: '✓' },
              { label: 'Avg Invoice Value', value: fmt(48000), color: 'purple', icon: '◈' },
              { label: 'Overdue Rate', value: '11.6%', color: 'yellow', icon: '⚠' },
            ].map(kpi => (
              <div key={kpi.label} className={`stat-card c-${kpi.color}`}>
                <div className={`stat-icon c-${kpi.color}`}>{kpi.icon}</div>
                <div className="stat-label">{kpi.label}</div>
                <div className={`stat-value c-${kpi.color}`}>{kpi.value}</div>
              </div>
            ))}
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface)', padding: 4, borderRadius: 'var(--r-sm)', width: 'fit-content' }}>
            {(['revenue', 'clients', 'late', 'recurring'] as const).map(t => (
              <button key={t} onClick={() => setTab(t)} style={{
                padding: '6px 16px', borderRadius: 6, border: 'none', fontSize: 12, fontWeight: 600,
                background: tab === t ? 'var(--bg3)' : 'transparent',
                color: tab === t ? 'var(--text)' : 'var(--text-muted)',
                textTransform: 'capitalize', cursor: 'pointer', transition: 'all 0.15s',
              }}>
                {t === 'late' ? 'Late Payments' : t === 'recurring' ? 'Recurring Revenue' : t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>

          {tab === 'revenue' && (
            <div className="card">
              <div className="section-header">
                <div>
                  <div className="section-title">Revenue Trend</div>
                  <div className="section-subtitle">6-month breakdown — collected vs overdue</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: 160, marginBottom: 12 }}>
                {REVENUE_DATA.map((d, i) => (
                  <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, height: '100%', justifyContent: 'flex-end' }}>
                    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 2, height: `${(d.revenue/maxRev)*100}%`, justifyContent: 'flex-end' }}>
                      <div style={{ flex: d.overdue, background: 'rgba(255,90,90,0.4)', borderRadius: '3px 3px 0 0', minHeight: 4 }} title={`Overdue: ${fmt(d.overdue)}`} />
                      <div style={{ flex: d.collected, background: 'linear-gradient(180deg, var(--accent), var(--accent2))', borderRadius: '3px 3px 0 0', minHeight: 8 }} title={`Collected: ${fmt(d.collected)}`} />
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>{d.month}</div>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 20, fontSize: 12, color: 'var(--text-muted)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--accent)', display: 'inline-block' }}/> Collected</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: 'rgba(255,90,90,0.4)', display: 'inline-block' }}/> Overdue</span>
              </div>
            </div>
          )}

          {tab === 'clients' && (
            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: '20px 24px 0' }}>
                <div className="section-title">Top Clients Leaderboard</div>
                <div className="section-subtitle" style={{ marginBottom: 16 }}>Ranked by revenue — AI risk score included</div>
              </div>
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>#</th><th>Client</th><th>Revenue</th><th>Invoices</th><th>AI Risk</th><th>Share</th></tr></thead>
                  <tbody>
                    {TOP_CLIENTS.map((c, i) => (
                      <tr key={i}>
                        <td><span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-dim)', fontSize: 13 }}>#{i+1}</span></td>
                        <td><span style={{ fontWeight: 600 }}>{c.name}</span></td>
                        <td><span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)' }}>{fmt(c.revenue)}</span></td>
                        <td>{c.invoices}</td>
                        <td><span className={`badge ${riskBadge[c.risk as keyof typeof riskBadge]}`}>{c.risk}</span></td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div style={{ flex: 1, height: 4, background: 'var(--surface)', borderRadius: 2, overflow: 'hidden' }}>
                              <div style={{ width: `${(c.revenue/TOP_CLIENTS[0].revenue)*100}%`, height: '100%', background: riskColor[c.risk as keyof typeof riskColor], borderRadius: 2 }} />
                            </div>
                            <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{Math.round((c.revenue/REVENUE_DATA.reduce((s,d)=>s+d.revenue,0))*100)}%</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'late' && (
            <div className="card">
              <div className="section-title" style={{ marginBottom: 16 }}>Late Payment Analytics</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16, marginBottom: 20 }}>
                {[
                  { label: 'Avg Days Late', value: '18 days', color: 'var(--yellow)' },
                  { label: 'Total Overdue', value: '₹1,45,000', color: 'var(--red)' },
                  { label: 'Recovery Rate', value: '76%', color: 'var(--green)' },
                ].map((m,i) => (
                  <div key={i} style={{ padding: '16px 18px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', textAlign: 'center' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{m.label}</div>
                    <div style={{ fontSize: 24, fontWeight: 800, color: m.color, fontFamily: 'var(--font-display)' }}>{m.value}</div>
                  </div>
                ))}
              </div>
              <div className="ai-summary-banner">
                <div className="ai-summary-left">
                  <div className="ai-summary-badge">✦ AI Prediction</div>
                  <div className="ai-summary-text">Based on payment history patterns, Global Media (INV-041) has a 78% probability of late payment. Recommend sending a pre-emptive reminder 7 days before due date.</div>
                </div>
              </div>
            </div>
          )}

          {tab === 'recurring' && (
            <div className="card">
              <div className="section-title" style={{ marginBottom: 16 }}>Recurring Revenue Analytics</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                {[
                  { label: 'MRR (Monthly Recurring)', value: '₹85,000', trend: '+12%', up: true },
                  { label: 'ARR Projection', value: '₹10,20,000', trend: '+18%', up: true },
                  { label: 'Active Subscriptions', value: '7', trend: '+2', up: true },
                  { label: 'Churn Risk', value: '1 client', trend: 'Monitor', up: false },
                ].map((m,i) => (
                  <div key={i} style={{ padding: '18px 20px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{m.label}</div>
                    <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--text)', fontFamily: 'var(--font-display)', marginBottom: 4 }}>{m.value}</div>
                    <div style={{ fontSize: 12, color: m.up ? 'var(--green)' : 'var(--yellow)' }}>{m.trend} vs last period</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
