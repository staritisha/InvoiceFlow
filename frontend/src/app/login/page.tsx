'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { auth } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [nameOrEmail, setNameOrEmail] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      if (mode === 'login') {
        const data = await auth.login(nameOrEmail, password);
        localStorage.setItem('token', data.access_token);
        router.push('/');
      } else {
        await auth.register({
          full_name: nameOrEmail,
          email,
          password,
        });

        const data = await auth.login(email, password);
        localStorage.setItem('token', data.access_token);
        router.push('/');
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 0,
          opacity: 0.04,
          backgroundImage:
            'linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}
      />

      <div className="login-card" style={{ position: 'relative', zIndex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 32 }}>
          <div className="logo-mark">IF</div>
          <div className="logo-text">
            Invoice<span>Flow</span>
          </div>
        </div>

        <div className="login-title">
          {mode === 'login' ? 'Welcome back' : 'Create account'}
        </div>

        <div className="login-subtitle">
          {mode === 'login'
            ? 'Sign in to your billing dashboard'
            : 'Start managing your invoices today'}
        </div>

        <div
          style={{
            display: 'flex',
            background: 'rgba(255,255,255,0.04)',
            borderRadius: 10,
            padding: 4,
            marginBottom: 28,
          }}
        >
          {(['login', 'register'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError('');
              }}
              style={{
                flex: 1,
                padding: '8px 0',
                borderRadius: 7,
                border: 'none',
                fontSize: 13,
                fontWeight: 600,
                background: mode === m ? 'var(--bg3)' : 'transparent',
                color: mode === m ? 'var(--text)' : 'var(--text-muted)',
                transition: 'all 0.2s',
                boxShadow: mode === m ? '0 2px 8px rgba(0,0,0,0.3)' : 'none',
              }}
            >
              {m === 'login' ? 'Sign In' : 'Register'}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">
              {mode === 'login' ? 'Email' : 'Full Name'}
            </label>
            <input
              className="form-input"
              type={mode === 'login' ? 'email' : 'text'}
              placeholder={mode === 'login' ? 'ritisha@example.com' : 'Ritisha Jadhao'}
              value={nameOrEmail}
              onChange={(e) => setNameOrEmail(e.target.value)}
              required
              autoFocus
            />
          </div>

          {mode === 'register' && (
            <div className="form-group">
              <label className="form-label">Email</label>
              <input
                className="form-input"
                type="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Password</label>
            <input
              className="form-input"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              
            />
            <input
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
  required
/>
          </div>

          {error && (
            <div
              style={{
                background: 'rgba(255,90,90,0.08)',
                border: '1px solid rgba(255,90,90,0.2)',
                borderRadius: 8,
                padding: '10px 14px',
                color: 'var(--red)',
                fontSize: 13,
                marginBottom: 16,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <span>⚠</span> {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-lg w-full"
            style={{ justifyContent: 'center', width: '100%' }}
            disabled={loading}
          >
            {loading ? (
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                <span className="loading-dot" />
                <span className="loading-dot" />
                <span className="loading-dot" />
              </span>
            ) : mode === 'login' ? (
              'Sign In →'
            ) : (
              'Create Account →'
            )}
          </button>
        </form>

        <div className="login-footer">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login');
              setError('');
            }}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--accent)',
              fontWeight: 600,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            {mode === 'login' ? 'Register' : 'Sign In'}
          </button>
        </div>
      </div>
    </div>
  );
}