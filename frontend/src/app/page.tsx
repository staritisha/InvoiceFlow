'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';

import { dashboard, invoices as invoicesApi, type DashboardAnalytics, type Invoice } from '@/lib/api';
function StatCard({ label, value, delta, color, icon }: {
  label: string; value: string; delta?: string; deltaUp?: boolean; color: string; icon: string;
}) {
  return (
    <div className={`stat-card c-${color}`}>
      <div className={`stat-icon c-${color}`}>{icon}</div>
      <div className="stat-label">{label}</div>
      <div className={`stat-value c-${color}`}>{value}</div>
      {delta && <div className="stat-delta up">{delta}</div>}
    </div>
  );
}

function MiniChart({ data }: { data: { month: string; amount: number }[] }) {
  const max = Math.max(...data.map(d => d.amount), 1);
  return (
    <div className="chart-bar-wrap">
      {data.map((d, i) => (
        <div
          key={i}
          className="chart-bar"
          style={{ height: `${Math.max((d.amount / max) * 100, 6)}%` }}
          title={`${d.month}: ₹${d.amount.toLocaleString()}`}
        />
      ))}
    </div>
  );
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    paid: 'badge-green', sent: 'badge-blue', draft: 'badge-gray',
    overdue: 'badge-red', cancelled: 'badge-yellow',
  };
  return map[status] || 'badge-gray';
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    dashboard.analytics()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const fmt = (n?: number) =>
  `₹${(n ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Dashboard</div>
          <div className="topbar-right">
            <span style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {new Date().toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}
            </span>
            <a href={invoicesApi.exportCsv()} className="btn btn-secondary btn-sm">
              ↓ Export CSV
            </a>
          </div>
        </header>

        <div className="page-content">
          {loading && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 28 }}>
              {[0,1,2].map(i => <div key={i} className="loading-dot" />)}
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Fetching analytics...</span>
            </div>
          )}

          {error && (
            <div style={{ background: 'rgba(255,90,90,0.08)', border: '1px solid rgba(255,90,90,0.2)', borderRadius: 'var(--r)', padding: '14px 18px', marginBottom: 24, color: 'var(--red)', fontSize: 14 }}>
              ⚠ {error} — Make sure your FastAPI backend is running on port 8000.
            </div>
          )}

          {/* Stats */}
          <div className="stats-grid">
            <StatCard
              label="Total Revenue"
              value={data ? fmt(data.total_revenue) : '—'}
              delta="↑ This period"
              color="blue"
              icon="₹"
            />
            <StatCard
              label="Pending Amount"
              value={data ? fmt(data.pending_amount) : '—'}
              color="yellow"
              icon="⏳"
            />
            <StatCard
              label="Total Customers"
              value={data ? String(data.total_customers) : '—'}
              color="purple"
              icon="◎"
            />
            <StatCard
              label="Paid Invoices"
              value={data ? `${data.paid_invoices}/${data.total_invoices}` : '—'}
              color="green"
              icon="✓"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 20 }}>
            {/* Recent invoices */}
            <div className="card">
              <div className="section-header">
                <div>
                  <div className="section-title">Recent Invoices</div>
                  <div className="section-subtitle">Latest billing activity</div>
                </div>
                <a href="/invoices" className="btn btn-ghost btn-sm">View all →</a>
              </div>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Invoice #</th>
                      <th>Customer</th>
                      <th>Amount</th>
                      <th>Due</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data?.recent_invoices?.length ? data.recent_invoices.map((inv: Invoice) => (
                      <tr key={inv.id} style={{ cursor: 'pointer' }} onClick={() => router.push('/invoices')}>
                        <td><span className="mono text-accent">#{inv.invoice_number}</span></td>
                        <td>{inv.customer?.name || `Customer #${inv.customer_id}`}</td>
                        <td><span style={{ fontWeight: 600 }}>{fmt(inv.total_amount)}</span></td>
                        <td><span className="text-muted" style={{ fontSize: 13 }}>{new Date(inv.due_date).toLocaleDateString('en-IN')}</span></td>
                        <td><span className={`badge ${statusBadge(inv.status)}`}>{inv.status}</span></td>
                      </tr>
                    )) : (
                      <tr>
                        <td colSpan={5} className="empty-state">
                          <div className="empty-state-icon">◈</div>
                          <div className="empty-state-title">No invoices yet</div>
                          <div className="empty-state-desc">Create your first invoice to see it here</div>
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Revenue chart + quick stats */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="card card-glow">
                <div className="section-title" style={{ marginBottom: 16 }}>Monthly Revenue</div>
                {data?.monthly_revenue?.length ? (
                  <MiniChart data={data.monthly_revenue} />
                ) : (
                  <div style={{ height: 80, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-dim)', fontSize: 13 }}>
                    No data yet
                  </div>
                )}
                <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)' }}>
                  <span>6-month trend</span>
                  {data?.monthly_revenue?.[data.monthly_revenue.length - 1] && (
                    <span className="text-accent">{fmt(data.monthly_revenue[data.monthly_revenue.length - 1].amount)}</span>
                  )}
                </div>
              </div>

              <div className="card">
                <div className="section-title" style={{ marginBottom: 14 }}>Overview</div>
                {[
                  { label: 'Overdue', value: data?.overdue_invoices ?? '—', color: 'var(--red)' },
                  { label: 'Sent', value: data ? data.total_invoices - data.paid_invoices - (data.overdue_invoices || 0) : '—', color: 'var(--accent)' },
                  { label: 'Total', value: data?.total_invoices ?? '—', color: 'var(--text)' },
                ].map(row => (
                  <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 14 }}>
                    <span style={{ color: 'var(--text-muted)' }}>{row.label}</span>
                    <span style={{ color: row.color, fontWeight: 600 }}>{row.value}</span>
                  </div>
                ))}
              </div>

              <div className="card" style={{ background: 'linear-gradient(135deg, rgba(99,210,255,0.08), rgba(124,108,252,0.06))' }}>
                <div style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>Quick Actions</div>
                <a href="/invoices" className="btn btn-primary btn-sm w-full" style={{ marginBottom: 8, justifyContent: 'center' }}>+ New Invoice</a>
                <a href="/customers" className="btn btn-secondary btn-sm w-full" style={{ justifyContent: 'center' }}>+ Add Customer</a>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
