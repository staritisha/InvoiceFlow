'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import Toast from '@/components/Toast';
import { invoices as api, customers as custApi, type Invoice, type Customer, type InvoiceItemCreate } from '@/lib/api';

type Status = 'all' | 'draft' | 'sent' | 'paid' | 'overdue' | 'cancelled';
const STATUSES: Status[] = ['all', 'draft', 'sent', 'paid', 'overdue', 'cancelled'];

const STATUS_BADGE: Record<string, string> = {
  paid: 'badge-green', sent: 'badge-blue', draft: 'badge-gray',
  overdue: 'badge-red', cancelled: 'badge-yellow',
};

const EMPTY_ITEM: InvoiceItemCreate = { description: '', quantity: 1, unit_price: 0 };

export default function InvoicesPage() {
  const router = useRouter();
  const [list, setList] = useState<Invoice[]>([]);
  const [customerList, setCustomerList] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Status>('all');
  const [modal, setModal] = useState(false);
  const [form, setForm] = useState({ customer_id: '', due_date: '', notes: '' });
  const [items, setItems] = useState<InvoiceItemCreate[]>([{ ...EMPTY_ITEM }]);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [aiMessage, setAiMessage] = useState<{ id: number; text: string } | null>(null);
  const [aiLoading, setAiLoading] = useState<number | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const [invs, custs] = await Promise.all([api.list(), custApi.list()]);
      setList(invs);
      setCustomerList(custs);
    } catch { }
    setLoading(false);
  }

  function addItem() { setItems([...items, { ...EMPTY_ITEM }]); }
  function removeItem(i: number) { setItems(items.filter((_, idx) => idx !== i)); }
  function updateItem(i: number, key: keyof InvoiceItemCreate, val: string | number) {
    setItems(items.map((it, idx) => idx === i ? { ...it, [key]: val } : it));
  }

  const total = items.reduce((sum, it) => sum + (it.quantity * it.unit_price), 0);
  const fmt = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

  async function handleCreate() {
    if (!form.customer_id || !form.due_date || items.length === 0) {
      setToast({ msg: 'Fill all required fields', type: 'error' }); return;
    }
    setSaving(true);
    try {
      await api.create({
        customer_id: Number(form.customer_id),
        due_date: form.due_date,
        notes: form.notes,
        items: items.filter(it => it.description.trim()),
      });
      setToast({ msg: 'Invoice created!', type: 'success' });
      setModal(false);
      setForm({ customer_id: '', due_date: '', notes: '' });
      setItems([{ ...EMPTY_ITEM }]);
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
    setSaving(false);
  }

  async function updateStatus(id: number, status: string) {
    try {
      await api.updatePayment(id, status);
      setToast({ msg: `Status updated to ${status}`, type: 'success' });
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  async function sendReminder(id: number) {
    try {
      await api.sendReminder(id);
      setToast({ msg: 'Reminder sent!', type: 'success' });
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('Delete this invoice?')) return;
    try {
      await api.delete(id);
      setToast({ msg: 'Invoice deleted', type: 'success' });
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  async function handleAIFollowup(invoiceId: number) {
  setAiLoading(invoiceId);
  try {
    const data = await api.aiFollowup(invoiceId, "polite");
    setAiMessage({ id: invoiceId, text: data.message });
  } catch (e: any) {
    setToast({ msg: e.message, type: "error" });
  }
  setAiLoading(null);
}

  const filtered = list.filter(inv => {
    const matchFilter = filter === 'all' || inv.status === filter;
    const matchSearch =
      inv.invoice_number.includes(search) ||
      (inv.customer?.name || '').toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Invoices</div>
          <div className="topbar-right">
            <div className="search-bar">
              <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
              </svg>
              <input className="search-input" placeholder="Search invoices..." value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <a href={api.exportCsv()} className="btn btn-secondary btn-sm">↓ CSV</a>
            <button className="btn btn-primary" onClick={() => setModal(true)}>+ New Invoice</button>
          </div>
        </header>

        <div className="page-content">
          {/* Status filter tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface)', padding: 4, borderRadius: 'var(--r-sm)', width: 'fit-content' }}>
            {STATUSES.map(s => (
              <button
                key={s}
                onClick={() => setFilter(s)}
                style={{
                  padding: '6px 14px', borderRadius: 6, border: 'none', fontSize: 12, fontWeight: 600,
                  background: filter === s ? 'var(--bg3)' : 'transparent',
                  color: filter === s ? 'var(--text)' : 'var(--text-muted)',
                  textTransform: 'capitalize', cursor: 'pointer', transition: 'all 0.15s',
                  boxShadow: filter === s ? '0 2px 6px rgba(0,0,0,0.3)' : 'none',
                }}
              >
                {s}
              </button>
            ))}
            {aiMessage && (
  <div className="card card-glow" style={{ marginTop: 20, borderColor: "rgba(124,108,252,0.3)" }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
      <div>
        <div style={{ fontSize: 11, color: "var(--accent2)", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 4 }}>
          ✦ AI Follow-up — Invoice #{list.find(i => i.id === aiMessage.id)?.invoice_number}
        </div>
        <div className="section-title">Generated Message</div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => navigator.clipboard.writeText(aiMessage.text).then(() => setToast({ msg: "Copied!", type: "success" }))}
        >
          Copy
        </button>
        <button className="close-btn" onClick={() => setAiMessage(null)}>×</button>
      </div>
    </div>
    <pre style={{
      whiteSpace: "pre-wrap", fontFamily: "var(--font-body)", fontSize: 14,
      color: "var(--text)", lineHeight: 1.7,
      background: "rgba(124,108,252,0.05)", borderRadius: "var(--r-sm)",
      padding: "16px 18px", border: "1px solid rgba(124,108,252,0.1)",
    }}>
      {aiMessage.text}
    </pre>
  </div>
)}
          </div>

          {loading ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {[0,1,2].map(i => <div key={i} className="loading-dot" />)}
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading invoices...</span>
            </div>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Invoice #</th>
                      <th>Customer</th>
                      <th>Amount</th>
                      <th>Issue Date</th>
                      <th>Due Date</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 ? (
                      <tr><td colSpan={7}>
                        <div className="empty-state">
                          <div className="empty-state-icon">◈</div>
                          <div className="empty-state-title">No invoices found</div>
                          <div className="empty-state-desc">Create your first invoice to get started</div>
                          <button className="btn btn-primary btn-sm" onClick={() => setModal(true)}>+ New Invoice</button>
                        </div>
                      </td></tr>
                    ) : filtered.map(inv => (
                      <tr key={inv.id}>
                        <td><span className="mono text-accent">#{inv.invoice_number}</span></td>
                        <td>
                          <div style={{ fontWeight: 600 }}>{inv.customer?.name || `#${inv.customer_id}`}</div>
                          {inv.customer?.company && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{inv.customer.company}</div>}
                        </td>
                        <td><span style={{ fontWeight: 700 }}>{fmt(inv.total_amount)}</span></td>
                        <td><span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{new Date(inv.issue_date).toLocaleDateString('en-IN')}</span></td>
                        <td>
                          <span style={{ fontSize: 13, color: new Date(inv.due_date) < new Date() && inv.status !== 'paid' ? 'var(--red)' : 'var(--text-muted)' }}>
                            {new Date(inv.due_date).toLocaleDateString('en-IN')}
                          </span>
                        </td>
                        <td>
                          <select
                            value={inv.status}
                            onChange={e => updateStatus(inv.id, e.target.value)}
                            className={`badge ${STATUS_BADGE[inv.status]}`}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 11, fontWeight: 600 }}
                          >
                            {['draft','sent','paid','overdue','cancelled'].map(s => (
                              <option key={s} value={s} style={{ background: 'var(--bg2)', color: 'var(--text)' }}>{s}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: 5 }}>
                            <a
                              href={api.downloadPdf(inv.id)}
                              target="_blank"
                              className="btn btn-ghost btn-sm"
                              title="Download PDF"
                            >PDF</a>
                            <button className="btn btn-ghost btn-sm" onClick={() => sendReminder(inv.id)} title="Send reminder">✉</button>
                            <button className="btn btn-danger btn-sm" onClick={() => handleDelete(inv.id)}>×</button>
                            <button
  className="btn btn-secondary btn-sm"
  onClick={() => handleAIFollowup(inv.id)}
  disabled={aiLoading === inv.id}
  title="Generate AI follow-up message"
  style={{ color: "var(--accent2)" }}
>
  {aiLoading === inv.id ? "..." : "✦ AI"}
</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Create Invoice Modal */}
      {modal && (
        <div className="modal-overlay" onClick={() => setModal(false)}>
          <div className="modal" style={{ maxWidth: 620 }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">+ New Invoice</div>
              <button className="close-btn" onClick={() => setModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <div className="grid-2">
                <div className="form-group">
                  <label className="form-label">Customer *</label>
                  <select className="form-input form-select" value={form.customer_id} onChange={e => setForm({ ...form, customer_id: e.target.value })}>
                    <option value="">Select customer...</option>
                    {customerList.map(c => <option key={c.id} value={c.id}>{c.name}{c.company ? ` — ${c.company}` : ''}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Due Date *</label>
                  <input className="form-input" type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} />
                </div>
              </div>

              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <label className="form-label" style={{ marginBottom: 0 }}>Line Items *</label>
                  <button className="btn btn-secondary btn-sm" onClick={addItem}>+ Add Line</button>
                </div>

                {/* Items header */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px 100px 30px', gap: 8, marginBottom: 6, padding: '0 4px' }}>
                  {['Description', 'Qty', 'Unit Price', ''].map(h => (
                    <div key={h} style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{h}</div>
                  ))}
                </div>

                {items.map((item, i) => (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 80px 100px 30px', gap: 8, marginBottom: 8 }}>
                    <input className="form-input" placeholder="Service description" value={item.description} onChange={e => updateItem(i, 'description', e.target.value)} style={{ padding: '8px 12px', fontSize: 13 }} />
                    <input className="form-input" type="number" min={1} value={item.quantity} onChange={e => updateItem(i, 'quantity', Number(e.target.value))} style={{ padding: '8px 12px', fontSize: 13 }} />
                    <input className="form-input" type="number" min={0} step={0.01} placeholder="0.00" value={item.unit_price || ''} onChange={e => updateItem(i, 'unit_price', Number(e.target.value))} style={{ padding: '8px 12px', fontSize: 13 }} />
                    <button onClick={() => removeItem(i)} style={{ background: 'rgba(255,90,90,0.1)', border: '1px solid rgba(255,90,90,0.2)', borderRadius: 6, color: 'var(--red)', cursor: 'pointer', fontSize: 14 }}>×</button>
                  </div>
                ))}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12, padding: '12px 16px', background: 'rgba(99,210,255,0.05)', borderRadius: 8, border: '1px solid rgba(99,210,255,0.1)' }}>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>Total Amount</div>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 800, color: 'var(--accent)' }}>{fmt(total)}</div>
                  </div>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Notes</label>
                <textarea className="form-input" rows={2} placeholder="Payment terms, thank you note..." value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} style={{ resize: 'none' }} />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={saving}>
                {saving ? 'Creating...' : 'Create Invoice'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
