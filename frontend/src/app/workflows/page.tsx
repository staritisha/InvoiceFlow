'use client';
import { useState } from 'react';
import Sidebar from '@/components/Sidebar';

type Workflow = { id: number; name: string; trigger: string; actions: string[]; active: boolean; runs: number };

const DEFAULT_WORKFLOWS: Workflow[] = [
  { id: 1, name: 'Overdue Reminder Sequence', trigger: 'Invoice overdue by 1 day', actions: ['Send email reminder', 'Log activity', 'Flag for follow-up'], active: true, runs: 14 },
  { id: 2, name: 'Payment Thank You', trigger: 'Invoice marked as paid', actions: ['Send thank-you email', 'Update client score', 'Suggest next invoice'], active: true, runs: 28 },
  { id: 3, name: 'Weekly Business Report', trigger: 'Every Monday 9 AM', actions: ['Generate AI summary', 'Send to email', 'Update dashboard'], active: false, runs: 6 },
  { id: 4, name: 'Late Payment Escalation', trigger: 'Invoice overdue by 15 days', actions: ['Send urgent reminder', 'AI risk flag client', 'Notify admin'], active: false, runs: 3 },
];

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>(DEFAULT_WORKFLOWS);
  const [showBuilder, setShowBuilder] = useState(false);

  function toggle(id: number) {
    setWorkflows(ws => ws.map(w => w.id === id ? { ...w, active: !w.active } : w));
  }

  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">Workflow Automation</div>
          <div className="topbar-right">
            <button className="btn btn-primary" onClick={() => setShowBuilder(true)}>+ New Workflow</button>
          </div>
        </header>

        <div className="page-content">
          <div className="ai-summary-banner" style={{ marginBottom: 24 }}>
            <div className="ai-summary-left">
              <div className="ai-summary-badge">⚡ Workflow Engine</div>
              <div className="ai-summary-text">Automate repetitive tasks with AI-powered workflows. Trigger actions based on invoice events, dates, or client behavior. 2 workflows currently active.</div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {workflows.map(wf => (
              <div key={wf.id} className="card" style={{ borderLeft: `3px solid ${wf.active ? 'var(--green)' : 'var(--border)'}` }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
                  <div>
                    <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{wf.name}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)', background: 'var(--surface)', padding: '3px 8px', borderRadius: 4 }}>
                        ⚡ {wf.trigger}
                      </span>
                      <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>{wf.runs} runs</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span className={`badge ${wf.active ? 'badge-green' : 'badge-gray'}`}>{wf.active ? 'Active' : 'Paused'}</span>
                    <button
                      onClick={() => toggle(wf.id)}
                      style={{
                        width: 40, height: 22, borderRadius: 11, border: 'none', cursor: 'pointer',
                        background: wf.active ? 'var(--green)' : 'rgba(255,255,255,0.1)',
                        position: 'relative', transition: 'background 0.2s',
                      }}
                    >
                      <div style={{
                        width: 16, height: 16, borderRadius: 8, background: '#fff',
                        position: 'absolute', top: 3, left: wf.active ? 21 : 3, transition: 'left 0.2s',
                      }} />
                    </button>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {wf.actions.map((a, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ padding: '4px 10px', background: 'rgba(99,210,255,0.06)', border: '1px solid rgba(99,210,255,0.12)', borderRadius: 20, fontSize: 11, color: 'var(--accent)' }}>{a}</span>
                      {i < wf.actions.length - 1 && <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>→</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {showBuilder && (
            <div className="modal-overlay" onClick={() => setShowBuilder(false)}>
              <div className="modal" onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                  <div className="modal-title">⚡ New Workflow</div>
                  <button className="close-btn" onClick={() => setShowBuilder(false)}>×</button>
                </div>
                <div className="modal-body">
                  <div className="form-group">
                    <label className="form-label">Workflow Name</label>
                    <input className="form-input" placeholder="e.g. Overdue 30-day escalation" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Trigger</label>
                    <select className="form-input form-select">
                      <option>Invoice overdue by 1 day</option>
                      <option>Invoice overdue by 7 days</option>
                      <option>Invoice marked as paid</option>
                      <option>New invoice created</option>
                      <option>Every Monday 9AM</option>
                      <option>Client risk score changes</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Actions</label>
                    {['Send reminder email', 'Generate AI message', 'Flag client'].map((a, i) => (
                      <label key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 13, color: 'var(--text-muted)', cursor: 'pointer' }}>
                        <input type="checkbox" defaultChecked={i === 0} style={{ accentColor: 'var(--accent)' }} />
                        {a}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="modal-footer">
                  <button className="btn btn-secondary" onClick={() => setShowBuilder(false)}>Cancel</button>
                  <button className="btn btn-primary" onClick={() => setShowBuilder(false)}>Create Workflow</button>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
