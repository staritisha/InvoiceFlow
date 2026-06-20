'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function LandingPage() {
  const router = useRouter();

  useEffect(() => {
    // If already logged in, skip straight to dashboard
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
    if (token) router.push('/dashboard');
  }, [router]);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Instrument+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap');

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        html { scroll-behavior: smooth; }

        :root {
          --bg:            #0f172a;
          --bg2:           #1e293b;
          --surface:       rgba(30,41,59,0.6);
          --border:        rgba(148,163,184,0.12);
          --border-accent: rgba(99,102,241,0.35);
          --text:          #f8fafc;
          --text-muted:    #94a3b8;
          --text-dim:      #475569;
          --accent:        #6366f1;
          --accent2:       #8b5cf6;
          --green:         #10b981;
          --yellow:        #f59e0b;
          --red:           #ef4444;
          --glow:          rgba(99,102,241,0.18);
          --font-display:  'Syne', sans-serif;
          --font-body:     'Instrument Sans', sans-serif;
          --font-mono:     'DM Mono', monospace;
          --r:     16px;
          --r-sm:  10px;
          --r-lg:  24px;
        }

        body {
          background: var(--bg);
          color: var(--text);
          font-family: var(--font-body);
          font-size: 15px;
          line-height: 1.6;
          min-height: 100vh;
          overflow-x: hidden;
        }

        /* Background atmosphere */
        body::before {
          content: '';
          position: fixed;
          inset: 0;
          background:
            radial-gradient(ellipse 70% 50% at 10% -10%, rgba(99,102,241,0.14) 0%, transparent 55%),
            radial-gradient(ellipse 60% 40% at 90% 110%, rgba(139,92,246,0.10) 0%, transparent 55%),
            radial-gradient(ellipse 50% 40% at 50% 50%, rgba(99,102,241,0.04) 0%, transparent 70%);
          pointer-events: none;
          z-index: 0;
        }

        body::after {
          content: '';
          position: fixed;
          inset: 0;
          opacity: 0.025;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
          pointer-events: none;
          z-index: 0;
        }

        a { color: inherit; text-decoration: none; }
        button { cursor: pointer; font-family: inherit; }

        /* ── NAV ── */
        .nav {
          position: fixed;
          top: 0; left: 0; right: 0;
          z-index: 100;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 48px;
          height: 64px;
          background: rgba(15,23,42,0.8);
          backdrop-filter: blur(20px);
          border-bottom: 1px solid var(--border);
        }

        .nav-logo {
          display: flex;
          align-items: center;
          gap: 10px;
          font-family: var(--font-display);
          font-size: 18px;
          font-weight: 700;
          letter-spacing: -0.02em;
        }

        .nav-logo-mark {
          width: 32px;
          height: 32px;
          background: linear-gradient(135deg, #6366f1, #8b5cf6);
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 14px;
          font-weight: 800;
          color: #fff;
          font-family: var(--font-display);
          flex-shrink: 0;
        }

        .nav-links {
          display: flex;
          align-items: center;
          gap: 32px;
          font-size: 14px;
          color: var(--text-muted);
        }

        .nav-links a:hover { color: var(--text); }

        .nav-actions {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        /* ── BUTTONS ── */
        .btn {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 9px 20px;
          border-radius: var(--r-sm);
          font-size: 14px;
          font-weight: 500;
          font-family: var(--font-body);
          border: none;
          transition: all 0.18s ease;
          text-decoration: none;
          cursor: pointer;
          white-space: nowrap;
        }

        .btn-ghost {
          background: transparent;
          color: var(--text-muted);
          border: 1px solid transparent;
        }
        .btn-ghost:hover {
          color: var(--text);
          background: rgba(148,163,184,0.08);
          border-color: var(--border);
        }

        .btn-secondary {
          background: rgba(99,102,241,0.1);
          color: var(--accent);
          border: 1px solid rgba(99,102,241,0.25);
        }
        .btn-secondary:hover {
          background: rgba(99,102,241,0.18);
          border-color: rgba(99,102,241,0.45);
        }

        .btn-primary {
          background: linear-gradient(135deg, #6366f1, #8b5cf6);
          color: #fff;
          border: 1px solid transparent;
          box-shadow: 0 0 0 0 rgba(99,102,241,0);
          transition: all 0.2s ease;
        }
        .btn-primary:hover {
          box-shadow: 0 0 24px rgba(99,102,241,0.4);
          transform: translateY(-1px);
        }

        .btn-lg { padding: 13px 28px; font-size: 15px; border-radius: 12px; }

        /* ── HERO ── */
        .hero {
          position: relative;
          z-index: 1;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 120px 24px 80px;
        }

        .hero-badge {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 6px 14px;
          border-radius: 999px;
          border: 1px solid rgba(99,102,241,0.3);
          background: rgba(99,102,241,0.08);
          font-size: 12px;
          font-family: var(--font-mono);
          color: var(--accent);
          letter-spacing: 0.05em;
          text-transform: uppercase;
          margin-bottom: 32px;
        }

        .hero-badge-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent);
          animation: pulse 2s ease-in-out infinite;
        }

        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(0.8); }
        }

        .hero-title {
          font-family: var(--font-display);
          font-size: clamp(42px, 7vw, 80px);
          font-weight: 800;
          line-height: 1.05;
          letter-spacing: -0.03em;
          max-width: 900px;
          margin-bottom: 24px;
        }

        .hero-title .grad {
          background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a78bfa 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }

        .hero-sub {
          font-size: 18px;
          color: var(--text-muted);
          max-width: 560px;
          line-height: 1.7;
          margin-bottom: 40px;
        }

        .hero-ctas {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          justify-content: center;
          margin-bottom: 64px;
        }

        /* ── TICKER / SOCIAL PROOF ── */
        .ticker-wrap {
          display: flex;
          align-items: center;
          gap: 32px;
          font-size: 13px;
          color: var(--text-dim);
          font-family: var(--font-mono);
          flex-wrap: wrap;
          justify-content: center;
        }

        .ticker-item {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .ticker-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background: var(--green);
        }

        /* ── DASHBOARD PREVIEW ── */
        .preview-wrap {
          position: relative;
          z-index: 1;
          max-width: 960px;
          margin: 0 auto;
          padding: 0 24px 100px;
        }

        .preview-glow {
          position: absolute;
          top: -80px; left: 50%; transform: translateX(-50%);
          width: 600px;
          height: 300px;
          background: radial-gradient(ellipse, rgba(99,102,241,0.18) 0%, transparent 70%);
          pointer-events: none;
          z-index: 0;
        }

        .preview-frame {
          position: relative;
          z-index: 1;
          border-radius: 20px;
          border: 1px solid rgba(99,102,241,0.2);
          background: rgba(15,23,42,0.9);
          overflow: hidden;
          box-shadow:
            0 0 0 1px rgba(99,102,241,0.1),
            0 40px 120px rgba(0,0,0,0.6),
            0 0 80px rgba(99,102,241,0.12);
        }

        /* mock chrome bar */
        .preview-chrome {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px 16px;
          background: rgba(15,23,42,0.95);
          border-bottom: 1px solid var(--border);
        }

        .chrome-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
        }

        .chrome-url {
          flex: 1;
          height: 24px;
          background: rgba(30,41,59,0.8);
          border-radius: 6px;
          margin: 0 12px;
          display: flex;
          align-items: center;
          padding: 0 10px;
          font-size: 11px;
          color: var(--text-dim);
          font-family: var(--font-mono);
        }

        /* fake dashboard inside frame */
        .mock-dash {
          display: flex;
          height: 380px;
          background: #0f172a;
        }

        .mock-sidebar {
          width: 160px;
          flex-shrink: 0;
          background: rgba(15,23,42,0.95);
          border-right: 1px solid var(--border);
          padding: 20px 16px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .mock-logo {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 0 4px;
          margin-bottom: 20px;
        }

        .mock-logo-mark {
          width: 22px;
          height: 22px;
          border-radius: 5px;
          background: linear-gradient(135deg, #6366f1, #8b5cf6);
          flex-shrink: 0;
        }

        .mock-logo-text {
          width: 60px;
          height: 10px;
          border-radius: 3px;
          background: rgba(248,250,252,0.15);
        }

        .mock-nav-item {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 7px 8px;
          border-radius: 8px;
          background: transparent;
        }

        .mock-nav-item.active {
          background: rgba(99,102,241,0.15);
          border: 1px solid rgba(99,102,241,0.2);
        }

        .mock-nav-icon {
          width: 14px;
          height: 14px;
          border-radius: 3px;
          background: rgba(148,163,184,0.25);
          flex-shrink: 0;
        }

        .mock-nav-item.active .mock-nav-icon {
          background: rgba(99,102,241,0.6);
        }

        .mock-nav-label {
          height: 8px;
          border-radius: 2px;
          background: rgba(148,163,184,0.15);
          flex: 1;
        }

        .mock-nav-item.active .mock-nav-label {
          background: rgba(99,102,241,0.4);
        }

        .mock-main {
          flex: 1;
          padding: 20px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          overflow: hidden;
        }

        .mock-topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        .mock-title {
          width: 80px;
          height: 14px;
          border-radius: 4px;
          background: rgba(248,250,252,0.2);
        }

        .mock-chip {
          height: 22px;
          width: 120px;
          border-radius: 999px;
          background: rgba(99,102,241,0.15);
          border: 1px solid rgba(99,102,241,0.2);
        }

        .mock-stats {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 10px;
        }

        .mock-stat {
          background: rgba(30,41,59,0.6);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .mock-stat-label {
          height: 7px;
          border-radius: 2px;
          width: 60%;
          background: rgba(148,163,184,0.2);
        }

        .mock-stat-value {
          height: 16px;
          border-radius: 3px;
          width: 80%;
          background: rgba(248,250,252,0.15);
        }

        .mock-stat:nth-child(1) .mock-stat-value { background: rgba(99,102,241,0.4); }
        .mock-stat:nth-child(2) .mock-stat-value { background: rgba(245,158,11,0.4); }
        .mock-stat:nth-child(3) .mock-stat-value { background: rgba(139,92,246,0.4); }
        .mock-stat:nth-child(4) .mock-stat-value { background: rgba(16,185,129,0.4); }

        .mock-row {
          display: grid;
          grid-template-columns: 1fr 120px;
          gap: 10px;
          flex: 1;
        }

        .mock-card {
          background: rgba(30,41,59,0.45);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 12px;
          overflow: hidden;
        }

        .mock-table-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 0;
          border-bottom: 1px solid rgba(148,163,184,0.06);
        }

        .mock-table-cell {
          height: 8px;
          border-radius: 2px;
          background: rgba(148,163,184,0.15);
        }

        .mock-badge {
          height: 16px;
          width: 36px;
          border-radius: 4px;
          flex-shrink: 0;
        }

        .badge-green { background: rgba(16,185,129,0.25); }
        .badge-blue  { background: rgba(99,102,241,0.25); }
        .badge-yellow{ background: rgba(245,158,11,0.25); }
        .badge-red   { background: rgba(239,68,68,0.25); }

        .mock-chart {
          display: flex;
          align-items: flex-end;
          gap: 6px;
          height: 80px;
          padding-top: 10px;
        }

        .mock-bar {
          flex: 1;
          border-radius: 4px 4px 0 0;
          background: rgba(99,102,241,0.3);
          transition: height 0.3s ease;
        }

        .mock-bar:nth-child(3) { background: rgba(99,102,241,0.5); }
        .mock-bar:nth-child(5) { background: rgba(99,102,241,0.65); }
        .mock-bar:nth-child(6) { background: linear-gradient(180deg, #6366f1, #8b5cf6); }

        /* ── FEATURES ── */
        .section {
          position: relative;
          z-index: 1;
          max-width: 1100px;
          margin: 0 auto;
          padding: 0 24px 100px;
        }

        .section-eyebrow {
          font-size: 11px;
          font-family: var(--font-mono);
          color: var(--accent);
          letter-spacing: 0.1em;
          text-transform: uppercase;
          margin-bottom: 16px;
        }

        .section-heading {
          font-family: var(--font-display);
          font-size: clamp(28px, 4vw, 42px);
          font-weight: 700;
          letter-spacing: -0.02em;
          margin-bottom: 16px;
          line-height: 1.15;
        }

        .section-sub {
          font-size: 16px;
          color: var(--text-muted);
          max-width: 560px;
          line-height: 1.7;
          margin-bottom: 52px;
        }

        .features-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 20px;
        }

        @media (max-width: 768px) {
          .features-grid { grid-template-columns: 1fr; }
          .nav-links { display: none; }
          .hero-title { font-size: 38px; }
          .mock-stats { grid-template-columns: repeat(2, 1fr); }
        }

        .feat-card {
          background: rgba(30,41,59,0.45);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 28px;
          transition: border-color 0.2s ease, box-shadow 0.2s ease;
          position: relative;
          overflow: hidden;
        }

        .feat-card::before {
          content: '';
          position: absolute;
          inset: 0;
          border-radius: 16px;
          opacity: 0;
          background: linear-gradient(135deg, rgba(99,102,241,0.06), rgba(139,92,246,0.04));
          transition: opacity 0.2s ease;
        }

        .feat-card:hover { border-color: rgba(99,102,241,0.3); box-shadow: 0 0 40px rgba(99,102,241,0.1); }
        .feat-card:hover::before { opacity: 1; }

        .feat-icon {
          width: 44px;
          height: 44px;
          border-radius: 12px;
          background: rgba(99,102,241,0.12);
          border: 1px solid rgba(99,102,241,0.2);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 20px;
          margin-bottom: 20px;
        }

        .feat-title {
          font-family: var(--font-display);
          font-size: 17px;
          font-weight: 600;
          margin-bottom: 10px;
          letter-spacing: -0.01em;
        }

        .feat-desc {
          font-size: 14px;
          color: var(--text-muted);
          line-height: 1.65;
        }

        /* ── AI SECTION ── */
        .ai-section {
          position: relative;
          z-index: 1;
          max-width: 1100px;
          margin: 0 auto;
          padding: 0 24px 100px;
        }

        .ai-panel {
          background: rgba(30,41,59,0.5);
          border: 1px solid rgba(99,102,241,0.2);
          border-radius: 24px;
          padding: 52px;
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 60px;
          align-items: center;
          overflow: hidden;
          position: relative;
        }

        .ai-panel::before {
          content: '';
          position: absolute;
          top: -100px; right: -100px;
          width: 400px;
          height: 400px;
          border-radius: 50%;
          background: radial-gradient(circle, rgba(139,92,246,0.15) 0%, transparent 70%);
          pointer-events: none;
        }

        @media (max-width: 768px) {
          .ai-panel { grid-template-columns: 1fr; padding: 32px; gap: 32px; }
        }

        .ai-label {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 11px;
          font-family: var(--font-mono);
          color: var(--accent2);
          letter-spacing: 0.1em;
          text-transform: uppercase;
          margin-bottom: 20px;
        }

        .ai-message-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .ai-message {
          background: rgba(15,23,42,0.8);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 16px;
          font-size: 13px;
          line-height: 1.6;
          font-family: var(--font-body);
          position: relative;
        }

        .ai-message.highlight {
          border-color: rgba(99,102,241,0.3);
          background: rgba(99,102,241,0.06);
        }

        .ai-message-badge {
          font-size: 10px;
          font-family: var(--font-mono);
          color: var(--accent);
          letter-spacing: 0.08em;
          text-transform: uppercase;
          margin-bottom: 8px;
          opacity: 0.8;
        }

        .ai-message-text {
          color: var(--text-muted);
        }

        .ai-message.highlight .ai-message-text {
          color: var(--text);
        }

        /* ── STACK SECTION ── */
        .stack-grid {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
        }

        .stack-chip {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 999px;
          background: rgba(30,41,59,0.6);
          border: 1px solid var(--border);
          font-size: 13px;
          font-family: var(--font-mono);
          color: var(--text-muted);
          transition: border-color 0.15s ease, color 0.15s ease;
        }

        .stack-chip:hover {
          border-color: rgba(99,102,241,0.35);
          color: var(--text);
        }

        .stack-chip-dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: var(--accent);
          opacity: 0.6;
        }

        /* ── CTA SECTION ── */
        .cta-section {
          position: relative;
          z-index: 1;
          max-width: 700px;
          margin: 0 auto;
          padding: 0 24px 120px;
          text-align: center;
        }

        .cta-card {
          background: rgba(30,41,59,0.5);
          border: 1px solid rgba(99,102,241,0.2);
          border-radius: 28px;
          padding: 64px 48px;
          position: relative;
          overflow: hidden;
        }

        .cta-card::before {
          content: '';
          position: absolute;
          inset: 0;
          background: radial-gradient(ellipse 60% 50% at 50% 0%, rgba(99,102,241,0.12) 0%, transparent 70%);
          pointer-events: none;
        }

        .cta-title {
          font-family: var(--font-display);
          font-size: clamp(28px, 4vw, 40px);
          font-weight: 700;
          letter-spacing: -0.02em;
          margin-bottom: 16px;
          position: relative;
        }

        .cta-sub {
          font-size: 16px;
          color: var(--text-muted);
          margin-bottom: 36px;
          line-height: 1.65;
          position: relative;
        }

        .cta-actions {
          display: flex;
          gap: 12px;
          justify-content: center;
          flex-wrap: wrap;
          position: relative;
        }

        /* ── FOOTER ── */
        .footer {
          position: relative;
          z-index: 1;
          border-top: 1px solid var(--border);
          padding: 32px 48px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 13px;
          color: var(--text-dim);
          flex-wrap: wrap;
          gap: 16px;
        }

        .footer-logo {
          display: flex;
          align-items: center;
          gap: 8px;
          font-family: var(--font-display);
          font-size: 14px;
          font-weight: 600;
          color: var(--text-muted);
        }

        .footer-logo-mark {
          width: 22px;
          height: 22px;
          background: linear-gradient(135deg, #6366f1, #8b5cf6);
          border-radius: 5px;
        }

        .divider {
          position: relative;
          z-index: 1;
          height: 1px;
          background: linear-gradient(90deg, transparent, rgba(99,102,241,0.2), transparent);
          margin: 0 48px 0;
        }
      `}</style>

      {/* NAV */}
      <nav className="nav">
        <div className="nav-logo">
          <div className="nav-logo-mark">IF</div>
          InvoiceFlow
        </div>
        <div className="nav-links">
          <a href="#features">Features</a>
          <a href="#ai">AI Layer</a>
          <a href="#stack">Stack</a>
        </div>
        <div className="nav-actions">
          <a href="/login" className="btn btn-ghost">Sign in</a>
          <a href="/login" className="btn btn-primary">Get started →</a>
        </div>
      </nav>

      {/* HERO */}
      <section className="hero">
        <div className="hero-badge">
          <div className="hero-badge-dot" />
          Production-ready · Deployed on Render + Vercel
        </div>
        <h1 className="hero-title">
          Invoice management,<br />
          <span className="grad">supercharged with AI</span>
        </h1>
        <p className="hero-sub">
          Create, send, and track invoices end-to-end. Let AI write your follow-ups, summarise your week, and flag collection risks before they become problems.
        </p>
        <div className="hero-ctas">
          <a href="/login" className="btn btn-primary btn-lg">Open the app →</a>
          <a href="https://github.com/staritisha/InvoiceFlow" target="_blank" rel="noreferrer" className="btn btn-secondary btn-lg">View on GitHub</a>
        </div>
        <div className="ticker-wrap">
          <div className="ticker-item">
            <div className="ticker-dot" />
            FastAPI + Next.js 14
          </div>
          <div className="ticker-item">
            <div className="ticker-dot" style={{ background: 'var(--accent)' }} />
            GPT-4o-mini &amp; Claude Haiku
          </div>
          <div className="ticker-item">
            <div className="ticker-dot" style={{ background: 'var(--yellow)' }} />
            PostgreSQL · JWT auth
          </div>
          <div className="ticker-item">
            <div className="ticker-dot" style={{ background: 'var(--accent2)' }} />
            PDF generation · CSV export
          </div>
        </div>
      </section>

      {/* DASHBOARD PREVIEW */}
      <div className="preview-wrap">
        <div className="preview-glow" />
        <div className="preview-frame">
          {/* Chrome bar */}
          <div className="preview-chrome">
            <div className="chrome-dot" style={{ background: '#ff5f56' }} />
            <div className="chrome-dot" style={{ background: '#ffbd2e' }} />
            <div className="chrome-dot" style={{ background: '#27c93f' }} />
            <div className="chrome-url">invoice-flow-flame-nu.vercel.app</div>
          </div>
          {/* Mock dashboard */}
          <div className="mock-dash">
            {/* Sidebar */}
            <div className="mock-sidebar">
              <div className="mock-logo">
                <div className="mock-logo-mark" />
                <div className="mock-logo-text" />
              </div>
              {[true, false, false, false, false].map((active, i) => (
                <div key={i} className={`mock-nav-item ${active ? 'active' : ''}`}>
                  <div className="mock-nav-icon" />
                  <div className="mock-nav-label" style={{ width: `${[70, 55, 65, 80, 60][i]}%` }} />
                </div>
              ))}
            </div>
            {/* Main content */}
            <div className="mock-main">
              <div className="mock-topbar">
                <div className="mock-title" />
                <div className="mock-chip" />
              </div>
              <div className="mock-stats">
                {[0,1,2,3].map(i => (
                  <div key={i} className="mock-stat">
                    <div className="mock-stat-label" />
                    <div className="mock-stat-value" />
                  </div>
                ))}
              </div>
              <div className="mock-row">
                <div className="mock-card">
                  {[['badge-green',65], ['badge-blue',50], ['badge-yellow',75], ['badge-red',40], ['badge-green',55]].map(([cls, w], i) => (
                    <div key={i} className="mock-table-row">
                      <div className="mock-table-cell" style={{ width: '60px' }} />
                      <div className="mock-table-cell" style={{ flex: 1 }} />
                      <div className="mock-table-cell" style={{ width: `${w}px` }} />
                      <div className={`mock-badge ${cls}`} />
                    </div>
                  ))}
                </div>
                <div className="mock-card" style={{ display: 'flex', flexDirection: 'column' }}>
                  <div className="mock-chart">
                    {[35, 55, 45, 70, 50, 90, 65].map((h, i) => (
                      <div key={i} className="mock-bar" style={{ height: `${h}%` }} />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* FEATURES */}
      <section className="section" id="features">
        <div className="section-eyebrow">What's inside</div>
        <h2 className="section-heading">Everything billing needs.<br />Nothing it doesn't.</h2>
        <p className="section-sub">A full invoice lifecycle platform — from creation to collection — with the AI layer built in, not bolted on.</p>
        <div className="features-grid">
          {[
            { icon: '◈', title: 'Invoice Lifecycle', desc: 'Draft → sent → paid → overdue. Full status tracking with branded PDF download and CSV export for your accountant.' },
            { icon: '◎', title: 'Client Management', desc: 'Manage your customers, track payment history, and see outstanding balances at a glance.' },
            { icon: '↻', title: 'Recurring Billing', desc: 'Set up weekly, monthly, quarterly, or yearly schedules. APScheduler handles the rest automatically.' },
            { icon: '✦', title: 'AI Follow-ups', desc: 'Generate tone-aware reminder emails — polite, firm, or urgent — in one click. Powered by GPT-4o-mini or Claude Haiku.' },
            { icon: '▤', title: 'Analytics Dashboard', desc: 'KPIs, monthly revenue chart, overdue rate, and a weekly AI business summary with actionable recommendations.' },
            { icon: '⬡', title: 'Production Engineering', desc: 'JWT auth, bcrypt hashing, per-IP rate limiting, security headers, request tracing, and GitHub Actions CI.' },
          ].map((f, i) => (
            <div key={i} className="feat-card">
              <div className="feat-icon">{f.icon}</div>
              <div className="feat-title">{f.title}</div>
              <p className="feat-desc">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="divider" />

      {/* AI SECTION */}
      <section className="ai-section" id="ai" style={{ paddingTop: '80px' }}>
        <div className="ai-panel">
          <div>
            <div className="ai-label">✦ AI Layer</div>
            <h2 className="section-heading" style={{ marginBottom: 16 }}>Your billing assistant that actually knows your business</h2>
            <p style={{ fontSize: 15, color: 'var(--text-muted)', lineHeight: 1.7, marginBottom: 28 }}>
              Not just generic AI — InvoiceFlow's AI reads your real invoice data to write follow-ups calibrated to each client, and delivers weekly summaries that tell you what's actually going on with your cash flow.
            </p>
            <div className="stack-grid" style={{ marginBottom: 0 }}>
              {['AI Follow-up Emails', 'Weekly Business Summary', 'Collection Risk Alerts', 'Revenue Forecasting'].map(t => (
                <div key={t} className="stack-chip" style={{ fontSize: 12 }}>
                  <div className="stack-chip-dot" style={{ background: 'var(--accent2)' }} />
                  {t}
                </div>
              ))}
            </div>
          </div>
          <div className="ai-message-list">
            <div className="ai-message">
              <div className="ai-message-badge">✦ AI Follow-up · Firm tone</div>
              <div className="ai-message-text">Hi Rohan, this is a reminder that Invoice #INV-038 for ₹42,000 is now 14 days overdue. Please arrange payment by Friday to avoid a late fee. Reply to this email to discuss.</div>
            </div>
            <div className="ai-message highlight">
              <div className="ai-message-badge">✦ Weekly Summary · Jun 16–22</div>
              <div className="ai-message-text">Revenue is up 18% vs last month. 3 invoices are overdue &gt;30 days — initiate your collection workflow now. Your top client (Tata Consultancy) accounts for 34% of billings. Consider an annual contract.</div>
            </div>
          </div>
        </div>
      </section>

      {/* STACK */}
      <section className="section" id="stack">
        <div className="section-eyebrow">Tech stack</div>
        <h2 className="section-heading">Built with the right tools</h2>
        <p className="section-sub">Full-stack TypeScript + Python, deployed to Vercel and Render with PostgreSQL in production.</p>
        <div className="stack-grid">
          {[
            'Next.js 14', 'TypeScript', 'FastAPI', 'Python 3.12',
            'PostgreSQL', 'SQLAlchemy 2.0', 'JWT + bcrypt', 'ReportLab',
            'APScheduler', 'OpenAI GPT-4o-mini', 'Claude Haiku', 'GitHub Actions',
            'Vercel', 'Render', 'Docker', 'pytest',
          ].map(t => (
            <div key={t} className="stack-chip">
              <div className="stack-chip-dot" />
              {t}
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="cta-section">
        <div className="cta-card">
          <h2 className="cta-title">Ready to see it in action?</h2>
          <p className="cta-sub">Sign in to the live app or explore the source on GitHub.</p>
          <div className="cta-actions">
            <a href="/login" className="btn btn-primary btn-lg">Open the app →</a>
            <a href="https://github.com/staritisha/InvoiceFlow" target="_blank" rel="noreferrer" className="btn btn-ghost btn-lg">GitHub</a>
          </div>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="footer">
        <div className="footer-logo">
          <div className="footer-logo-mark" />
          InvoiceFlow
        </div>
        <div>Built with FastAPI · Next.js · PostgreSQL · OpenAI · Anthropic</div>
        <div>Deployed on Render + Vercel</div>
      </footer>
    </>
  );
}