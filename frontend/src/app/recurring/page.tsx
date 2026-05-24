'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import Toast from '@/components/Toast';
import { recurring as api, customers as custApi, type RecurringBilling, type Customer, type RecurringCreate } from '@/lib/api';

const FREQ_LABELS: Record<string, string> = { weekly: '7d', monthly: '30d', quarterly: '90d', yearly: '365d' };
const FREQ_COLORS: Record<string, string> = { weekly: 'badge-green', monthly: 'badge-blue', quarterly: 'badge-purple', yearly: 'badge-yellow' };

const EMPTY: RecurringCreate = { client_id: 0, title: '', description: '', amount: 0, frequency: 'monthly', next_billing_date: '' };
export default function RecurringPage() {
  const router = useRouter();
  const [list, setList] = useState<RecurringBilling[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<false | 'create' | 'edit'>(false);
  const [editing, setEditing] = useState<RecurringBilling | null>(null);
  const [form, setForm] = useState<RecurringCreate>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const [recs, custs] = await Promise.all([api.list(), custApi.list()]);
      setList(recs);
      setCustomers(custs);
    } catch { }
    setLoading(false);
  }

  function openCreate() {
    setForm({ ...EMPTY, next_billing_date: new Date().toISOString().split('T')[0] });
    setEditing(null);
    setModal('create');
  }

  function openEdit(r: RecurringBilling) {
setForm({ client_id: r.client_id ?? 0, title: r.title ?? '', description: r.description, amount: r.amount, frequency: r.frequency, next_billing_date: r.next_billing_date.split('T')[0] });  }

  async function handleSave() {
    if (!form.client_id || !form.title || !form.description || !form.amount) {
      setToast({ msg: 'Fill all required fields', type: 'error' }); return;
    }
    setSaving(true);
    try {
      if (modal === 'create') {
        await api.create(form);
        setToast({ msg: 'Recurring billing created!', type: 'success' });
      } else if (editing) {
        await api.update(editing.id, form);
        setToast({ msg: 'Updated!', type: 'success' });
      }
      setModal(false);
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
    setSaving(false);
  }

  async function handleGenerate(id: number) {
    try {
      await api.generate(id);
      setToast({ msg: 'Invoice generated from recurring!', type: 'success' });
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('Delete this recurring billing?')) return;
    try {
      await api.delete(id);
      setToast({ msg: 'Deleted', type: 'success' });
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  const fmt = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

  function daysUntil(dateStr: string) {
    const diff = new Date(dateStr).getTime() - Date.now();
    return Math.ceil(diff / (1000 * 60 * 60 * 24));
  }

  const activeCount = list.filter(r => r.is_active).length;
  const totalMonthly = list.filter(r => r.is_active && r.frequency === 'monthly').reduce((s, r) => s + r.amount, 0);

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Recurring Billing</div>
          <div className="topbar-right">
            <button className="btn btn-primary" onClick={openCreate}>+ New Recurring</button>
          </div>
        </header>

        <div className="page-content">
          {/* Summary stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }}>
            {[
              { label: 'Active Schedules', value: String(activeCount), color: 'var(--accent)', icon: '↻' },
              { label: 'Monthly Recurring', value: fmt(totalMonthly), color: 'var(--green)', icon: '₹' },
              { label: 'Total Schedules', value: String(list.length), color: 'var(--accent2)', icon: '◈' },
            ].map(s => (
              <div key={s.label} className="card" style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '18px 22px' }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: `${s.color}18`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, color: s.color }}>{s.icon}</div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 4 }}>{s.label}</div>
                  <div style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 800, color: s.color }}>{s.value}</div>
                </div>
              </div>
            ))}
          </div>

          {loading ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {[0,1,2].map(i => <div key={i} className="loading-dot" />)}
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading...</span>
            </div>
          ) : list.length === 0 ? (
            <div className="card">
              <div className="empty-state">
                <div className="empty-state-icon">↻</div>
                <div className="empty-state-title">No recurring billing yet</div>
                <div className="empty-state-desc">Set up automatic billing schedules for repeat customers</div>
                <button className="btn btn-primary btn-sm" onClick={openCreate}>+ New Recurring</button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
              {list.map(r => {
                const days = daysUntil(r.next_billing_date);
                const urgency = days <= 0 ? 'var(--red)' : days <= 7 ? 'var(--yellow)' : 'var(--text-muted)';
                return (
                  <div key={r.id} className="card" style={{ position: 'relative', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: r.is_active ? 'linear-gradient(90deg, var(--accent), var(--accent2))' : 'var(--border)' }} />

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 3 }}>{r.description}</div>
                        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>{r.customer?.name || `Customer #${r.customer_id}`}</div>
                      </div>
                      <span className={`badge ${FREQ_COLORS[r.frequency]}`}>{r.frequency}</span>
                    </div>

                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 26, fontWeight: 800, color: 'var(--accent)', marginBottom: 14 }}>
                      {fmt(r.amount)}
                      <span style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-body)', fontWeight: 400 }}>/{FREQ_LABELS[r.frequency]}</span>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Next billing</div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: urgency }}>
                        {days <= 0 ? 'Overdue!' : days === 0 ? 'Today' : `in ${days}d`}
                        <span style={{ fontWeight: 400, color: 'var(--text-muted)', marginLeft: 6 }}>
                          {new Date(r.next_billing_date).toLocaleDateString('en-IN')}
                        </span>
                      </div>
                    </div>

                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, 100 - (days / 30) * 100))}%` }} />
                    </div>

                    <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                      <button className="btn btn-primary btn-sm" style={{ flex: 1, justifyContent: 'center' }} onClick={() => handleGenerate(r.id)}>
                        ↻ Generate Invoice
                      </button>
                      <button className="btn btn-secondary btn-sm" onClick={() => openEdit(r)}>Edit</button>
                      <button className="btn btn-danger btn-sm" onClick={() => handleDelete(r.id)}>×</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>

      {modal && (
        <div className="modal-overlay" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">{modal === 'create' ? '+ New Recurring' : 'Edit Recurring'}</div>
              <button className="close-btn" onClick={() => setModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">Customer *</label>
                <select
  className="form-input form-select"
  value={form.client_id || ''}
  onChange={e => setForm({ ...form, client_id: Number(e.target.value) })}
>
  <option value="">Select customer</option>

  {customers.map(c => (
    <option key={c.id} value={c.id}>
      {c.name}
    </option>
  ))}
</select>
              </div>
              <div className="form-group">
                <label className="form-label">Description *</label>
                <input className="form-input" placeholder="Monthly retainer, SaaS subscription..." value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
              </div>
              <div className="grid-2">
                <div className="form-group">
                  <input className="form-input" placeholder="e.g. Monthly Retainer" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} />
                  <label className="form-label">Amount (₹) *</label>
                  <input className="form-input" type="number" min={0} step={0.01} value={form.amount || ''} onChange={e => setForm({ ...form, amount: Number(e.target.value) })} />
                </div>
                <div className="form-group">
                  <label className="form-label">Frequency *</label>
                  <select className="form-input form-select" value={form.frequency} onChange={e => setForm({ ...form, frequency: e.target.value as any })}>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                    <option value="yearly">Yearly</option>
                  </select>
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Next Billing Date *</label>
                <input className="form-input" type="date" value={form.next_billing_date} onChange={e => setForm({ ...form, next_billing_date: e.target.value })} />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving...' : modal === 'create' ? 'Create Schedule' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
