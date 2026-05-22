'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import { dashboard, invoices as invoicesApi, customers as customersApi, type Customer } from '@/lib/api';

type RevenuePoint = { month: string; amount: number };
type ClientRow = { name: string; revenue: number; invoices: number; risk: string };

const riskBadge = { low: 'badge-green', medium: 'badge-yellow', high: 'badge-red' } as const;
const riskColor = { low: 'var(--green)', medium: 'var(--yellow)', high: 'var(--red)' } as const;

export default function AnalyticsPage() {
  const router = useRouter();
  const [tab, setTab] = useState<'revenue' | 'clients' | 'late' | 'recurring'>('revenue');
  const [loading, setLoading] = useState(true);
  const [kpis, setKpis] = useState({ total_revenue: 0, collection_rate: 0, avg_invoice_value: 0, overdue_rate: 0 });
  const [revenueData, setRevenueData] = useState<RevenuePoint[]>([]);
  const [topClients, setTopClients] = useState<ClientRow[]>([]);
  const [lateStats, setLateStats] = useState({ overdue_count: 0, overdue_rate_percent: 0, overdue_amount: 0 });

  const fmt = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const base = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000/api/v1';
      const token = localStorage.getItem('token');
      const h = { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };

      const [summaryRes, kpiRes, lateRes, monthlyRes, invoiceList, customerList] = await Promise.all([
        fetch(`${base}/dashboard/summary`, { headers: h }).then(r => r.json()),
        fetch(`${base}/analytics/kpis`, { headers: h }).then(r => r.json()),
        fetch(`${base}/analytics/late-payments`, { headers: h }).then(r => r.json()),
        fetch(`${base}/dashboard/monthly-revenue`, { headers: h }).then(r => r.json()),
        invoicesApi.getAll(),
        customersApi.list(),
      ]);

      // KPIs
      const total = summaryRes.total_revenue || 0;
      const totalInvoiced = kpiRes.total_invoices || 1;
      setKpis({
        total_revenue: total,
        collection_rate: kpiRes.collection_rate_percent || 0,
        avg_invoice_value: kpiRes.average_invoice_value || 0,
        overdue_rate: lateRes.overdue_rate_percent || 0,
      });

      // Revenue chart
      setRevenueData(monthlyRes || []);

      // Late stats
      setLateStats({
        overdue_count: lateRes.overdue_count || 0,
        overdue_rate_percent: lateRes.overdue_rate_percent || 0,
        overdue_amount: lateRes.overdue_amount || 0,
      });

      // Top clients by revenue
      const clientMap: Record<number, { name: string; revenue: number; invoices: number }> = {};
      for (const inv of invoiceList) {
        const cid = inv.client_id ?? inv.customer_id;
        if (!clientMap[cid]) {
          const c = customerList.find((x: Customer) => x.id === cid);
          clientMap[cid] = { name: c?.name || `Client #${cid}`, revenue: 0, invoices: 0 };
        }
        clientMap[cid].revenue += Number(inv.total_amount) || 0;
        clientMap[cid].invoices += 1;
      }
      const sorted = Object.values(clientMap)
        .sort((a, b) => b.revenue - a.revenue)
        .slice(0, 5)
        .map(c => ({
          ...c,
          risk: c.revenue > 80000 ? 'low' : c.invoices > 8 ? 'medium' : 'low',
        }));
      setTopClients(sorted);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }

  const maxRev = Math.max(...revenueData.map(d => d.amount), 1);

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Analytics</div>
          <div className="topbar-right">
            <div className="ai-health-pill">
              <div className="ai-health-dot" />
              <span>Live Data</span>
            </div>
          </div>
        </header>

        <div className="page-content">
          {loading ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 28 }}>
              {[0,1,2].map(i => <div key={i} className="loading-dot" />)}
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading analytics...</span>
            </div>
          ) : (
            <>
              {/* KPI row */}
              <div className="stats-grid" style={{ marginBottom: 24 }}>
                {[
                  { label: 'Total Revenue', value: fmt(kpis.total_revenue), color: 'blue', icon: '₹' },
                  { label: 'Collection Rate', value: `${kpis.collection_rate.toFixed(1)}%`, color: 'green', icon: '✓' },
                  { label: 'Avg Invoice Value', value: fmt(kpis.avg_invoice_value), color: 'purple', icon: '◈' },
                  { label: 'Overdue Rate', value: `${kpis.overdue_rate.toFixed(1)}%`, color: 'yellow', icon: '⚠' },
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
                      <div className="section-title">Monthly Revenue</div>
                      <div className="section-subtitle">Paid invoices by month this year</div>
                    </div>
                  </div>
                  {revenueData.length > 0 ? (
                    <>
                      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: 160, marginBottom: 12 }}>
                        {revenueData.map((d, i) => (
                          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, height: '100%', justifyContent: 'flex-end' }}>
                            <div style={{ width: '100%', height: `${(d.amount / maxRev) * 100}%`, background: 'linear-gradient(180deg, var(--accent), var(--accent2))', borderRadius: '3px 3px 0 0', minHeight: 4 }} title={`${d.month}: ${fmt(d.amount)}`} />
                            <div style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>{d.month}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div style={{ height: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-dim)', fontSize: 13 }}>No revenue data yet — mark invoices as paid to see trends</div>
                  )}
                </div>
              )}

              {tab === 'clients' && (
                <div className="card" style={{ padding: 0 }}>
                  <div style={{ padding: '20px 24px 0' }}>
                    <div className="section-title">Top Clients by Revenue</div>
                    <div className="section-subtitle" style={{ marginBottom: 16 }}>Ranked from your actual invoices</div>
                  </div>
                  <div className="table-wrapper">
                    <table>
                      <thead><tr><th>#</th><th>Client</th><th>Revenue</th><th>Invoices</th><th>Risk</th><th>Share</th></tr></thead>
                      <tbody>
                        {topClients.length === 0 ? (
                          <tr><td colSpan={6}><div className="empty-state"><div className="empty-state-icon">◈</div><div className="empty-state-title">No data yet</div></div></td></tr>
                        ) : topClients.map((c, i) => (
                          <tr key={i}>
                            <td><span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-dim)', fontSize: 13 }}>#{i+1}</span></td>
                            <td><span style={{ fontWeight: 600 }}>{c.name}</span></td>
                            <td><span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)' }}>{fmt(c.revenue)}</span></td>
                            <td>{c.invoices}</td>
                            <td><span className={`badge ${riskBadge[c.risk as keyof typeof riskBadge]}`}>{c.risk}</span></td>
                            <td>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <div style={{ flex: 1, height: 4, background: 'var(--surface)', borderRadius: 2, overflow: 'hidden' }}>
                                  <div style={{ width: `${(c.revenue / (topClients[0]?.revenue || 1)) * 100}%`, height: '100%', background: riskColor[c.risk as keyof typeof riskColor], borderRadius: 2 }} />
                                </div>
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
                      { label: 'Overdue Invoices', value: String(lateStats.overdue_count), color: 'var(--red)' },
                      { label: 'Total Overdue', value: fmt(lateStats.overdue_amount), color: 'var(--yellow)' },
                      { label: 'Overdue Rate', value: `${lateStats.overdue_rate_percent.toFixed(1)}%`, color: 'var(--accent)' },
                    ].map((m, i) => (
                      <div key={i} style={{ padding: '16px 18px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', textAlign: 'center' }}>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{m.label}</div>
                        <div style={{ fontSize: 24, fontWeight: 800, color: m.color, fontFamily: 'var(--font-display)' }}>{m.value}</div>
                      </div>
                    ))}
                  </div>
                  {lateStats.overdue_count === 0 && (
                    <div style={{ padding: '16px', background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 'var(--r-sm)', color: 'var(--green)', fontSize: 14 }}>
                      ✓ No overdue invoices — great work!
                    </div>
                  )}
                </div>
              )}

              {tab === 'recurring' && (
                <div className="card">
                  <div className="section-title" style={{ marginBottom: 16 }}>Recurring Revenue</div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 14 }}>
                    Set up recurring billing schedules in the <a href="/recurring" style={{ color: 'var(--accent)' }}>Recurring</a> section to track MRR and ARR here.
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
