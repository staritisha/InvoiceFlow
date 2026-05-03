'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Sidebar from '@/components/Sidebar';
import Toast from '@/components/Toast';
import { customers as api, type Customer, type CustomerCreate } from '@/lib/api';

const EMPTY_FORM: CustomerCreate = { name: '', email: '', phone: '', address: '', company: '' };

export default function CustomersPage() {
  const router = useRouter();
  const [list, setList] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState<false | 'create' | 'edit'>(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [form, setForm] = useState<CustomerCreate>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success'|'error' } | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    load();
  }, []);

  async function load() {
    setLoading(true);
    try { setList(await api.list()); } catch { }
    setLoading(false);
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setEditing(null);
    setModal('create');
  }

  function openEdit(c: Customer) {
    setForm({ name: c.name, email: c.email, phone: c.phone || '', address: c.address || '', company: c.company || '' });
    setEditing(c);
    setModal('edit');
  }

  async function handleSave() {
    setSaving(true);
    try {
      if (modal === 'create') {
        await api.create(form);
        setToast({ msg: 'Customer created!', type: 'success' });
      } else if (editing) {
        await api.update(editing.id, form);
        setToast({ msg: 'Customer updated!', type: 'success' });
      }
      setModal(false);
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
    setSaving(false);
  }

  async function handleDelete(id: number) {
    if (!confirm('Delete this customer?')) return;
    try {
      await api.delete(id);
      setToast({ msg: 'Customer deleted', type: 'success' });
      await load();
    } catch (e: any) {
      setToast({ msg: e.message, type: 'error' });
    }
  }

  const filtered = list.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase()) ||
    c.email.toLowerCase().includes(search.toLowerCase()) ||
    (c.company || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Customers</div>
          <div className="topbar-right">
            <div className="search-bar">
              <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
              </svg>
              <input
                className="search-input"
                placeholder="Search customers..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <button className="btn btn-primary" onClick={openCreate}>
              + Add Customer
            </button>
          </div>
        </header>

        <div className="page-content">
          {loading ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {[0,1,2].map(i => <div key={i} className="loading-dot" />)}
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading customers...</span>
            </div>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div className="section-title">All Customers</div>
                  <div className="section-subtitle">{filtered.length} of {list.length} customers</div>
                </div>
              </div>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Company</th>
                      <th>Email</th>
                      <th>Phone</th>
                      <th>Added</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 ? (
                      <tr>
                        <td colSpan={6}>
                          <div className="empty-state">
                            <div className="empty-state-icon">◎</div>
                            <div className="empty-state-title">No customers yet</div>
                            <div className="empty-state-desc">Add your first customer to get started</div>
                            <button className="btn btn-primary btn-sm" onClick={openCreate}>+ Add Customer</button>
                          </div>
                        </td>
                      </tr>
                    ) : filtered.map(c => (
                      <tr key={c.id}>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <div style={{
                              width: 32, height: 32, borderRadius: 8,
                              background: `hsl(${(c.name.charCodeAt(0) * 17) % 360}, 40%, 30%)`,
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                              fontWeight: 700, fontSize: 13, color: '#fff', flexShrink: 0,
                            }}>{c.name[0].toUpperCase()}</div>
                            <span style={{ fontWeight: 600 }}>{c.name}</span>
                          </div>
                        </td>
                        <td><span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{c.company || '—'}</span></td>
                        <td><span className="mono" style={{ fontSize: 12 }}>{c.email}</span></td>
                        <td><span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{c.phone || '—'}</span></td>
                        <td><span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{new Date(c.created_at).toLocaleDateString('en-IN')}</span></td>
                        <td>
                          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                            <button className="btn btn-ghost btn-sm" onClick={() => openEdit(c)}>Edit</button>
                            <button className="btn btn-danger btn-sm" onClick={() => handleDelete(c.id)}>Delete</button>
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

      {/* Modal */}
      {modal && (
        <div className="modal-overlay" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">{modal === 'create' ? '+ New Customer' : 'Edit Customer'}</div>
              <button className="close-btn" onClick={() => setModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <div className="grid-2">
                <div className="form-group">
                  <label className="form-label">Name *</label>
                  <input className="form-input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Full name" required />
                </div>
                <div className="form-group">
                  <label className="form-label">Company</label>
                  <input className="form-input" value={form.company} onChange={e => setForm({ ...form, company: e.target.value })} placeholder="Company name" />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Email *</label>
                <input className="form-input" type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} placeholder="customer@email.com" required />
              </div>
              <div className="grid-2">
                <div className="form-group">
                  <label className="form-label">Phone</label>
                  <input className="form-input" value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} placeholder="+91 98765 43210" />
                </div>
                <div className="form-group">
                  <label className="form-label">Address</label>
                  <input className="form-input" value={form.address} onChange={e => setForm({ ...form, address: e.target.value })} placeholder="City, State" />
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving...' : modal === 'create' ? 'Create Customer' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
