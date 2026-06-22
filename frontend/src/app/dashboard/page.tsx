'use client';
import { useEffect, useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import { dashboard, invoices as invoicesApi, type DashboardAnalytics, type Invoice } from '@/lib/api';

function StatCard({ label, value, delta, deltaUp, color, icon, sub }: {
  label: string; value: string; delta?: string; deltaUp?: boolean; color: string; icon: string; sub?: string;
}) {
  return (
    <div className={`stat-card c-${color}`}>
      <div className={`stat-icon c-${color}`}>{icon}</div>
      <div className="stat-label">{label}</div>
      <div className={`stat-value c-${color}`}>{value}</div>
      {delta && <div className={`stat-delta ${deltaUp ? 'up' : 'down'}`}>{delta}</div>}
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function MiniChart({ data }: { data: { month: string; amount: number }[] }) {
  const max = Math.max(...data.map(d => d.amount), 1);
  return (
    <div className="chart-bar-wrap">
      {data.map((d, i) => (
        <div key={i} className="chart-bar-col">
          <div
            className="chart-bar"
            style={{ height: `${Math.max((d.amount / max) * 100, 6)}%` }}
            title={`${d.month}: ₹${d.amount.toLocaleString()}`}
          />
          <div className="chart-bar-label">{d.month.slice(0,3)}</div>
        </div>
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

const ACTIVITY = [
  { icon: '✓', text: 'Invoice #INV-044 marked as paid', time: '2m ago', color: 'var(--green)' },
  { icon: '✉', text: 'Reminder sent to Acme Corp', time: '18m ago', color: 'var(--accent)' },
  { icon: '✦', text: 'AI generated cash flow forecast', time: '1h ago', color: 'var(--accent2)' },
  { icon: '◈', text: 'New invoice created for Infosys Ltd', time: '3h ago', color: 'var(--yellow)' },
  { icon: '◎', text: 'New client: Tata Consultancy onboarded', time: '1d ago', color: 'var(--accent)' },
];

const AI_TIPS = [
  { title: 'Collect faster', tip: 'Send reminders on Tue–Thu mornings — 42% higher open rate.', icon: '💡' },
  { title: 'Revenue opportunity', tip: 'Your top 3 clients generate 67% of revenue. Consider annual contracts.', icon: '🎯' },
  { title: 'Risk alert', tip: '2 clients have payment delays > 30 days. Initiate collection workflow.', icon: '⚠️' },
];

async function getAIWeeklySummary(): Promise<string> {
  // This endpoint calls an LLM internally — allow 30 s for generation.
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30_000);

  try {
    const token = localStorage.getItem('token');
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000/api/v1'}/ai/weekly-summary`,
      {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      }
    );
    const data = await res.json();
    return data.summary || '';
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      return 'Summary generation timed out. Please try again in a moment.';
    }
    return '';
  } finally {
    clearTimeout(timeoutId);
  }
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [aiSummary, setAiSummary] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [activityIdx, setActivityIdx] = useState(0);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    dashboard.analytics()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));

    const t = setInterval(() => setActivityIdx(i => (i + 1) % ACTIVITY.length), 3500);
    return () => clearInterval(t);
  }, []);

  async function loadAISummary() {
    setAiLoading(true);
    const s = await getAIWeeklySummary();
    setAiSummary(s);
    setAiLoading(false);
  }

  const fmt = (n?: number) =>
    `₹${(n ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Dashboard</div>
          <div className="topbar-right">
            <div className="ai-health-pill">
              <div className="ai-health-dot" />
              <span>Business Health: 78/100</span>
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {new Date().toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}
            </span>
            <a href={invoicesApi.exportCsv()} className="btn btn-secondary btn-sm">↓ Export CSV</a>
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

          {/* AI Weekly Summary Banner */}
          <div className="ai-summary-banner">
            <div className="ai-summary-left">
              <div className="ai-summary-badge">✦ AI Weekly Summary</div>
              {aiSummary ? (
                <div className="ai-summary-text">{aiSummary}</div>
              ) : (
                <div className="ai-summary-placeholder">Get an AI-generated summary of your business performance, trends, and recommendations.</div>
              )}
            </div>
            <button className="btn btn-primary btn-sm" onClick={loadAISummary} disabled={aiLoading} style={{ flexShrink: 0 }}>
              {aiLoading ? '✦ Generating...' : '✦ Generate Summary'}
            </button>
          </div>

          {/* Stats */}
          <div className="stats-grid">
            <StatCard label="Total Revenue" value={data ? fmt(data.total_revenue) : '—'} delta="↑ This period" deltaUp={true} color="blue" icon="₹" sub="Collected" />
            <StatCard label="Pending Amount" value={data ? fmt(data.pending_amount) : '—'} color="yellow" icon="⏳" sub="Outstanding" />
            <StatCard label="Total Customers" value={data ? String(data.total_customers) : '—'} color="purple" icon="◎" sub="Active clients" />
            <StatCard label="Paid Invoices" value={data ? `${data.paid_invoices}/${data.total_invoices}` : '—'} color="green" icon="✓" sub="Collection rate" />
          </div>

          {/* AI Tips Row */}
          <div className="ai-tips-row">
            {AI_TIPS.map((tip, i) => (
              <div key={i} className="ai-tip-card">
                <span className="ai-tip-icon">{tip.icon}</span>
                <div>
                  <div className="ai-tip-title">{tip.title}</div>
                  <div className="ai-tip-text">{tip.tip}</div>
                </div>
              </div>
            ))}
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

            {/* Right column */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Revenue chart */}
              <div className="card card-glow">
                <div className="section-title" style={{ marginBottom: 16 }}>Monthly Revenue</div>
                {data?.monthly_revenue?.length ? (
                  <MiniChart data={data.monthly_revenue} />
                ) : (
                  <div style={{ height: 80, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-dim)', fontSize: 13 }}>No data yet</div>
                )}
              </div>

              {/* Overview */}
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

              {/* Activity Timeline */}
              <div className="card">
                <div style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 12 }}>Live Activity</div>
                <div className="activity-timeline">
                  {ACTIVITY.map((a, i) => (
                    <div key={i} className={`activity-item ${i === activityIdx ? 'active' : ''}`}>
                      <div className="activity-dot" style={{ background: a.color }} />
                      <div className="activity-content">
                        <div className="activity-text">{a.text}</div>
                        <div className="activity-time">{a.time}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Quick Actions */}
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
