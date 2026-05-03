'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: '▦' },
  { href: '/customers', label: 'Customers', icon: '◎' },
  { href: '/invoices', label: 'Invoices', icon: '◈' },
  { href: '/recurring', label: 'Recurring', icon: '↻' },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<{ username?: string } | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token && pathname !== '/login') router.push('/login');
    // Optionally fetch /users/me for user info
    setUser({ username: 'Admin' });
  }, []);

  function logout() {
    localStorage.removeItem('token');
    router.push('/login');
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="logo-mark">IF</div>
        <div className="logo-text">Invoice<span>Flow</span></div>
      </div>

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

      <div className="sidebar-footer">
        <div className="user-card" style={{ cursor: 'pointer' }} onClick={logout}>
          <div className="user-avatar">
            {user?.username?.[0]?.toUpperCase() || 'A'}
          </div>
          <div>
            <div className="user-name">{user?.username || 'Admin'}</div>
            <div className="user-role">Sign out →</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
