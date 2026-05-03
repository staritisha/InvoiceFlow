'use client';
import { useEffect } from 'react';

interface ToastProps {
  message: string;
  type?: 'success' | 'error' | 'info';
  onClose: () => void;
  duration?: number;
}

export default function Toast({ message, type = 'success', onClose, duration = 3000 }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onClose, duration);
    return () => clearTimeout(t);
  }, []);

  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  const colors = { success: 'var(--green)', error: 'var(--red)', info: 'var(--accent)' };

  return (
    <div className={`toast ${type}`}>
      <span style={{ color: colors[type], fontWeight: 700, fontSize: 15 }}>{icons[type]}</span>
      <span>{message}</span>
      <button
        onClick={onClose}
        style={{ background: 'none', border: 'none', color: 'var(--text-muted)', marginLeft: 8, fontSize: 16, lineHeight: 1 }}
      >×</button>
    </div>
  );
}
