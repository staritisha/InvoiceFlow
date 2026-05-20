'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import AICommandCenter from './AICommandCenter';

const navItems = [
  { href: '/',           label: 'Dashboard',  icon: '▦' },
  { href: '/customers',  label: 'Customers',  icon: '◎' },
  { href: '/invoices',   label: 'Invoices',   icon: '◈' },
  { href: '/recurring',  label: 'Recurring',  icon: '↻' },
  { href: '/analytics',  label: 'Analytics',  icon: '📊' },
  { href: '/workflows',  label: 'Workflows',  icon: '⚡' },
];

export default function Sidebar() {
  const pathname  = usePathname();
  const router    = useRouter();
  const [user,       setUser]       = useState<{ username?: string } | null>(null);
  const [aiOpen,     setAiOpen]     = useState(false);
  const [cmdPalette, setCmdPalette] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token && pathname !== '/login') router.push('/login');
    setUser({ username: 'Admin' });

    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setCmdPalette(v => !v); }
      if (e.key === 'Escape') { setAiOpen(false); setCmdPalette(false); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function logout() { localStorage.removeItem('token'); router.push('/login'); }

  return (
    <>
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="logo-mark">IF</div>
          <div className="logo-text">Invoice<span>Flow</span></div>
        </div>

        {/* AI Status Badge */}
        <div style={{ margin: '0 12px 12px', display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', background: 'rgba(99,102,241,0.07)', border: '1px solid rgba(99,102,241,0.18)', borderRadius: 20, width: 'fit-content' }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', boxShadow: '0 0 8px var(--green)', animation: 'pulseDot 2s infinite' }} />
          <span style={{ fontSize: 10, fontWeight: 600, color: '#a5b4fc', letterSpacing: '0.06em' }}>AI ASSISTANT ONLINE</span>
        </div>

        <button className="ai-sidebar-btn" onClick={() => setAiOpen(true)}>
          <div className="ai-sidebar-orb" />
          <div>
            <div className="ai-sidebar-label">AI Command Center</div>
            <div className="ai-sidebar-sub">Ask anything • Get insights</div>
          </div>
          <span className="ai-sidebar-arrow">✦</span>
        </button>

        <nav className="sidebar-nav">
          <div className="nav-section-label">Main</div>
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-item ${pathname === item.href ? 'active' : ''}`}
            >
              <span style={{ fontSize: 16 }}>{item.icon}</span>
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="sidebar-cmd-hint" onClick={() => setCmdPalette(v => !v)}>
          <span>⌘K</span>
          <span>Command palette</span>
        </div>

        <div className="sidebar-footer">
          <div className="user-card" style={{ cursor: 'pointer' }} onClick={logout}>
            <div className="user-avatar">{user?.username?.[0]?.toUpperCase() || 'A'}</div>
            <div>
              <div className="user-name">{user?.username || 'Admin'}</div>
              <div className="user-role">Sign out →</div>
            </div>
          </div>
        </div>
      </aside>

      <AICommandCenter open={aiOpen} onClose={() => setAiOpen(false)} />

      {cmdPalette && (
        <div className="modal-overlay" onClick={() => setCmdPalette(false)}>
          <div
            style={{ background: 'rgba(15,23,42,0.97)', backdropFilter: 'blur(24px)', border: '1px solid var(--border-accent)', borderRadius: 'var(--r-lg)', width: '100%', maxWidth: 520, padding: 0, overflow: 'hidden', boxShadow: '0 32px 80px rgba(0,0,0,0.7), 0 0 0 1px rgba(99,102,241,0.08)' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
              <input
                autoFocus
                style={{ flex: 1, background: 'none', border: 'none', outline: 'none', fontSize: 15, color: 'var(--text)', fontFamily: 'var(--font-body)' }}
                placeholder="Search pages, actions, AI commands..."
              />
              <span style={{ fontSize: 11, color: 'var(--text-dim)', background: 'var(--surface)', padding: '3px 6px', borderRadius: 4 }}>ESC</span>
            </div>
            {[
              { label: 'Open AI Command Center', icon: '✦', action: () => { setCmdPalette(false); setAiOpen(true); } },
              { label: 'New Invoice',            icon: '◈', action: () => { setCmdPalette(false); router.push('/invoices'); } },
              { label: 'Add Customer',           icon: '◎', action: () => { setCmdPalette(false); router.push('/customers'); } },
              { label: 'View Analytics',         icon: '📊', action: () => { setCmdPalette(false); router.push('/analytics'); } },
              { label: 'Dashboard',              icon: '▦', action: () => { setCmdPalette(false); router.push('/'); } },
            ].map((item, i) => (
              <div key={i} className="cmd-item" onClick={item.action}>
                <span style={{ width: 28, textAlign: 'center', fontSize: 14 }}>{item.icon}</span>
                <span style={{ fontSize: 14, color: 'var(--text)' }}>{item.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
