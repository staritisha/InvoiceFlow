'use client';
import { useState, useRef, useEffect } from 'react';

type Message = { role: 'user' | 'ai'; text: string; timestamp?: string };
type Panel = 'chat' | 'insights' | 'actions' | 'forecast';

const QUICK_PROMPTS = [
  'Summarize my business health',
  'Which clients are at risk?',
  'Generate a payment reminder',
  'Forecast next month revenue',
  'Top 3 actions to take today',
];

const MOCK_INSIGHTS = [
  { icon: '🔥', title: 'Revenue Spike Detected', desc: 'March revenue is 34% above your 6-month average. Consider raising rates.', color: 'var(--accent)' },
  { icon: '⚠️', title: '3 Overdue Invoices', desc: 'Total ₹1,24,000 overdue — 2 clients have not paid in 45+ days.', color: 'var(--yellow)' },
  { icon: '📈', title: 'Growth Trend Positive', desc: 'Paid invoices up 18% this quarter. Strong collection rate.', color: 'var(--green)' },
  { icon: '🎯', title: 'Top Client Opportunity', desc: 'Acme Corp accounts for 40% revenue — consider upselling.', color: 'var(--accent2)' },
];

const MOCK_ACTIONS = [
  { label: 'Send overdue reminders (3)', icon: '✉️', type: 'warning' },
  { label: 'Follow up: Acme Corp - INV-042', icon: '📋', type: 'info' },
  { label: 'Schedule recurring — Infosys Ltd', icon: '↻', type: 'success' },
  { label: 'Review draft invoices (2)', icon: '◈', type: 'default' },
];

async function callAI(messages: { role: string; content: string }[]): Promise<string> {
  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1000,
        system: `You are an AI business assistant embedded in InvoiceFlow, a premium invoice & billing SaaS. 
You help business owners with invoice management, client relationships, cash flow, and revenue growth.
Keep responses concise, actionable, and professional. Use ₹ for Indian currency when relevant.
Format responses clearly with bullet points or short paragraphs. Be confident and data-driven.`,
        messages,
      }),
    });
    const data = await res.json();
    return data.content?.[0]?.text || 'I couldn\'t process that request. Please try again.';
  } catch {
    return 'Connection issue. Please check your network and try again.';
  }
}

export default function AICommandCenter({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [panel, setPanel] = useState<Panel>('chat');
  const [messages, setMessages] = useState<Message[]>([
    { role: 'ai', text: 'Hello! I\'m your AI Business Assistant. I can help you analyze invoices, predict payment risks, generate reminders, and provide revenue insights.\n\nWhat would you like to know about your business today?', timestamp: 'Just now' }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [healthScore] = useState(78);
  const [riskScore] = useState(23);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function sendMessage(text?: string) {
    const msg = text || input.trim();
    if (!msg || loading) return;
    setInput('');
    const userMsg: Message = { role: 'user', text: msg, timestamp: 'Just now' };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    const history = [...messages, userMsg].map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.text }));
    const reply = await callAI(history);
    setMessages(prev => [...prev, { role: 'ai', text: reply, timestamp: 'Just now' }]);
    setLoading(false);
  }

  if (!open) return null;

  return (
    <div className="ai-center-overlay" onClick={onClose}>
      <div className="ai-center" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="ai-center-header">
          <div className="ai-center-logo">
            <div className="ai-orb" />
            <div>
              <div className="ai-title">AI Command Center</div>
              <div className="ai-status">● Online — Analyzing your business</div>
            </div>
          </div>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        {/* Score bar */}
        <div className="ai-scores">
          <div className="ai-score-item">
            <div className="ai-score-label">Business Health</div>
            <div className="ai-score-bar">
              <div className="ai-score-fill green" style={{ width: `${healthScore}%` }} />
            </div>
            <div className="ai-score-val green">{healthScore}/100</div>
          </div>
          <div className="ai-score-divider" />
          <div className="ai-score-item">
            <div className="ai-score-label">Risk Index</div>
            <div className="ai-score-bar">
              <div className="ai-score-fill red" style={{ width: `${riskScore}%` }} />
            </div>
            <div className="ai-score-val red">{riskScore}%</div>
          </div>
        </div>

        {/* Panel tabs */}
        <div className="ai-tabs">
          {(['chat', 'insights', 'actions', 'forecast'] as Panel[]).map(p => (
            <button key={p} className={`ai-tab ${panel === p ? 'active' : ''}`} onClick={() => setPanel(p)}>
              {p === 'chat' ? '💬 Chat' : p === 'insights' ? '✦ Insights' : p === 'actions' ? '⚡ Actions' : '📊 Forecast'}
            </button>
          ))}
        </div>

        {/* Panel content */}
        <div className="ai-panel-body">

          {/* CHAT */}
          {panel === 'chat' && (
            <>
              <div className="ai-messages">
                {messages.map((m, i) => (
                  <div key={i} className={`ai-msg ${m.role}`}>
                    {m.role === 'ai' && <div className="ai-msg-avatar">✦</div>}
                    <div className="ai-msg-bubble">
                      <div className="ai-msg-text" style={{ whiteSpace: 'pre-wrap' }}>{m.text}</div>
                      {m.timestamp && <div className="ai-msg-time">{m.timestamp}</div>}
                    </div>
                  </div>
                ))}
                {loading && (
                  <div className="ai-msg ai">
                    <div className="ai-msg-avatar">✦</div>
                    <div className="ai-msg-bubble">
                      <div className="ai-typing"><span /><span /><span /></div>
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="ai-quick-prompts">
                {QUICK_PROMPTS.map(p => (
                  <button key={p} className="ai-quick-btn" onClick={() => sendMessage(p)}>{p}</button>
                ))}
              </div>

              <div className="ai-input-row">
                <input
                  className="ai-input"
                  placeholder="Ask anything about your business..."
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && sendMessage()}
                />
                <button className="ai-send" onClick={() => sendMessage()} disabled={loading || !input.trim()}>
                  {loading ? '...' : '↑'}
                </button>
              </div>
            </>
          )}

          {/* INSIGHTS */}
          {panel === 'insights' && (
            <div className="ai-insights">
              <div className="ai-insights-header">AI-generated insights based on your invoice data</div>
              {MOCK_INSIGHTS.map((ins, i) => (
                <div key={i} className="ai-insight-card" style={{ '--ins-color': ins.color } as React.CSSProperties}>
                  <div className="ai-insight-icon">{ins.icon}</div>
                  <div>
                    <div className="ai-insight-title">{ins.title}</div>
                    <div className="ai-insight-desc">{ins.desc}</div>
                  </div>
                </div>
              ))}
              <button className="btn btn-secondary btn-sm" style={{ marginTop: 8, width: '100%', justifyContent: 'center' }}
                onClick={() => { setPanel('chat'); sendMessage('Generate detailed business insights from my invoice data'); }}>
                ✦ Generate Fresh Insights
              </button>
            </div>
          )}

          {/* ACTIONS */}
          {panel === 'actions' && (
            <div className="ai-actions">
              <div className="ai-insights-header">AI-recommended actions for today</div>
              {MOCK_ACTIONS.map((a, i) => (
                <div key={i} className={`ai-action-item type-${a.type}`}>
                  <span className="ai-action-icon">{a.icon}</span>
                  <span className="ai-action-label">{a.label}</span>
                  <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }}
                    onClick={() => { setPanel('chat'); sendMessage(`Help me with: ${a.label}`); }}>
                    Do it →
                  </button>
                </div>
              ))}
              <div className="ai-action-divider">AI Generators</div>
              {[
                { label: 'Generate overdue reminder email', prompt: 'Write a professional overdue payment reminder email for a client who is 30 days late' },
                { label: 'Generate thank-you message', prompt: 'Write a professional thank-you message for a client who just paid their invoice on time' },
                { label: 'Write follow-up schedule', prompt: 'Create a 3-step follow-up schedule for an overdue invoice' },
              ].map((g, i) => (
                <button key={i} className="ai-gen-btn" onClick={() => { setPanel('chat'); sendMessage(g.prompt); }}>
                  ✦ {g.label}
                </button>
              ))}
            </div>
          )}

          {/* FORECAST */}
          {panel === 'forecast' && (
            <div className="ai-forecast">
              <div className="ai-insights-header">AI revenue predictions & cash flow</div>
              <div className="ai-forecast-chart">
                {[65, 80, 55, 90, 75, 95].map((h, i) => (
                  <div key={i} className="ai-forecast-bar-wrap">
                    <div className="ai-forecast-bar" style={{ height: `${h}%`, opacity: i >= 4 ? 0.55 : 1 }} />
                    <div className="ai-forecast-label">{['Nov','Dec','Jan','Feb','Mar','Apr*'][i]}</div>
                  </div>
                ))}
              </div>
              <div className="ai-forecast-note">* Predicted months shown with reduced opacity</div>
              {[
                { label: 'Next month forecast', value: '₹2,40,000', up: true },
                { label: 'Expected collections', value: '₹1,85,000', up: true },
                { label: 'At-risk amount', value: '₹55,000', up: false },
                { label: 'Cash flow score', value: '74/100', up: true },
              ].map((f, i) => (
                <div key={i} className="ai-forecast-row">
                  <span className="ai-forecast-label2">{f.label}</span>
                  <span className={`ai-forecast-val ${f.up ? 'up' : 'down'}`}>{f.value}</span>
                </div>
              ))}
              <button className="btn btn-secondary btn-sm" style={{ marginTop: 12, width: '100%', justifyContent: 'center' }}
                onClick={() => { setPanel('chat'); sendMessage('Provide a detailed 3-month revenue forecast and cash flow analysis for my business'); }}>
                ✦ Deep Forecast Analysis
              </button>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
