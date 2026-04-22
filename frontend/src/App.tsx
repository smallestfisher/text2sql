import React, { useState, useEffect, useRef } from 'react';
import { 
  Send, Database, Layout, Shield, Plus, 
  MessageCircle, LogOut, ChevronRight, 
  Terminal, Search, Info, Activity,
  Table, Code, Eye, Sparkles, Command
} from 'lucide-react';
import { api } from './api';
import { 
  UserContext, ChatSession, ChatMessage, 
  ViewMode, ChatResponse, QueryPlan, ExecutionResponse 
} from './types';

export default function App() {
  const [token, setToken] = useState<string | null>(localStorage.getItem('token'));
  const [user, setUser] = useState<UserContext | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('workspace');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [loginData, setLoginData] = useState({ username: '', password: '' });
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (token) {
      api.me(token).then(setUser).catch(() => setToken(null));
      loadSessions();
    }
  }, [token]);

  useEffect(() => {
    if (activeSession && token) {
      api.getSessionHistory(token, activeSession).then(res => setMessages(res.messages));
    }
  }, [activeSession, token]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const loadSessions = async () => {
    if (!token) return;
    const res = await api.listSessions(token);
    setSessions(res.sessions);
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await api.login(loginData);
      setToken(res.access_token);
      localStorage.setItem('token', res.access_token);
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleNewSession = async () => {
    if (!token) return;
    const res = await api.createSession(token);
    await loadSessions();
    setActiveSession(res.session.id);
    setMessages([]);
    setViewMode('workspace');
  };

  const handleSend = async () => {
    if (!input.trim() || !token || loading) return;
    setLoading(true);
    
    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      session_id: activeSession || '',
      role: 'user',
      content: input,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');

    try {
      const res = await api.chatQuery(token, input, activeSession || undefined);
      if (!activeSession) {
        setActiveSession(res.next_session_state.session_id);
        loadSessions();
      }
      
      const assistantMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        session_id: activeSession || res.next_session_state.session_id,
        role: 'assistant',
        content: res.answer?.summary || 'I have analyzed your request:',
        created_at: new Date().toISOString(),
        query_plan: res.query_plan,
        sql: res.sql,
        execution: res.execution,
        answer_payload: res.answer,
      };
      setMessages(prev => [...prev, assistantMsg]);
      setActiveMessageId(assistantMsg.id);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setLoading(false);
    }
  };

  const activeMessage = messages.find(m => m.id === activeMessageId);

  if (!token) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="msg-bubble assistant" style={{ width: 380, padding: 32, borderRadius: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 32 }}>
            <div className="avatar" style={{ width: 48, height: 48 }}><Sparkles color="white" style={{ margin: 12 }} /></div>
            <h1>Text2SQL <span style={{ color: 'var(--accent-color)' }}>AI</span></h1>
          </div>
          <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="input-capsule" style={{ background: 'rgba(255,255,255,0.05)', padding: '4px 16px' }}>
              <input 
                type="text" 
                placeholder="Username" 
                style={{ background: 'transparent', border: 'none', color: 'white', height: 44, outline: 'none', width: '100%' }}
                value={loginData.username}
                onChange={e => setLoginData({...loginData, username: e.target.value})}
              />
            </div>
            <div className="input-capsule" style={{ background: 'rgba(255,255,255,0.05)', padding: '4px 16px' }}>
              <input 
                type="password" 
                placeholder="Password" 
                style={{ background: 'transparent', border: 'none', color: 'white', height: 44, outline: 'none', width: '100%' }}
                value={loginData.password}
                onChange={e => setLoginData({...loginData, password: e.target.value})}
              />
            </div>
            <button type="submit" className="icon-btn primary" style={{ width: '100%', borderRadius: 22, height: 44, fontSize: 15, fontWeight: 600 }}>
              Sign In
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div style={{ padding: '24px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
            <Sparkles size={20} color="var(--accent-color)" />
            <span style={{ fontWeight: 800, fontSize: 18 }}>Lobe <span style={{ opacity: 0.5 }}>SQL</span></span>
          </div>
          <button className="icon-btn primary" style={{ width: '100%', borderRadius: 12, gap: 8 }} onClick={handleNewSession}>
            <Plus size={18} /> <span>New Chat</span>
          </button>
        </div>
        
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div className="caption" style={{ padding: '0 24px 12px' }}>Recent</div>
          {sessions.map(s => (
            <div 
              key={s.id} 
              className={`session-card ${activeSession === s.id ? 'active' : ''}`}
              onClick={() => { setActiveSession(s.id); setViewMode('workspace'); }}
            >
              <MessageCircle size={18} />
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 14 }}>
                {s.title || 'Untitled Session'}
              </span>
            </div>
          ))}
        </div>

        <div style={{ padding: 12, borderTop: '1px solid var(--glass-border)' }}>
          <div className="session-card" onClick={() => setViewMode('semantic')}>
            <Database size={18} /> <span>Semantics</span>
          </div>
          <div className="session-card" onClick={() => setViewMode('admin')}>
            <Shield size={18} /> <span>Admin</span>
          </div>
          <div className="session-card" onClick={() => { localStorage.removeItem('token'); setToken(null); }}>
            <LogOut size={18} /> <span>Logout</span>
          </div>
        </div>
      </aside>

      <main className="main-stage">
        <header className="top-nav">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(124, 58, 237, 0.2)', display: 'flex', alignItems: 'center', justifyItems: 'center' }}>
              <Activity size={18} color="var(--accent-color)" style={{ margin: 'auto' }} />
            </div>
            <h1>{viewMode === 'workspace' ? 'Data Workspace' : viewMode === 'admin' ? 'System Console' : 'Semantic Layer'}</h1>
          </div>
          <div className="input-capsule" style={{ padding: '4px 12px', background: 'rgba(255,255,255,0.03)', fontSize: 13, border: 'none' }}>
            <Command size={14} color="var(--text-muted)" />
            <span style={{ color: 'var(--text-muted)' }}>{user?.username} • {user?.roles[0]}</span>
          </div>
        </header>

        <div style={{ flex: 1, display: 'flex', gap: 12, minHeight: 0 }}>
          <div className="chat-container">
            <div className="message-list">
              {messages.length === 0 && (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', opacity: 0.3 }}>
                  <Sparkles size={64} style={{ marginBottom: 24 }} />
                  <p style={{ fontSize: 18, fontWeight: 500 }}>How can I help you explore your data today?</p>
                </div>
              )}
              {messages.map(m => (
                <div key={m.id} className={`msg-row ${m.role}`}>
                  <div className="avatar">{m.role === 'assistant' ? <Sparkles size={16} color="white" style={{ margin: 10 }} /> : null}</div>
                  <div className="msg-bubble">
                    <div style={{ fontSize: 15 }}>{m.content}</div>
                    {m.role === 'assistant' && (m.sql || m.query_plan) && (
                      <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="icon-btn" style={{ background: 'rgba(255,255,255,0.05)', borderRadius: 8, width: 'auto', padding: '0 12px', fontSize: 12, gap: 6 }} onClick={() => setActiveMessageId(m.id)}>
                          <Eye size={14} /> Inspector
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>

            <div className="input-dock">
              <div className="input-capsule">
                <textarea 
                  placeholder="Ask a question about your database..." 
                  rows={1}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                />
                <button className="icon-btn primary" onClick={handleSend} disabled={loading || !input.trim()}>
                  <Send size={18} />
                </button>
              </div>
            </div>
          </div>

          {activeMessageId && activeMessage && (
            <aside className="side-drawer">
              <div style={{ padding: 20, borderBottom: '1px solid var(--glass-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Search size={18} color="var(--accent-color)" />
                  <span style={{ fontWeight: 700 }}>AI Inspector</span>
                </div>
                <button className="icon-btn" onClick={() => setActiveMessageId(null)}>×</button>
              </div>
              
              <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
                {activeMessage.query_plan && (
                  <section style={{ marginBottom: 32 }}>
                    <div className="caption" style={{ marginBottom: 12 }}>Logical Plan</div>
                    <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 12, padding: 16, fontSize: 13, border: '1px solid var(--glass-border)' }}>
                      <div style={{ marginBottom: 8 }}><strong>Target:</strong> {activeMessage.query_plan.subject_domain}</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {activeMessage.query_plan.metrics.map(m => <span key={m} style={{ background: 'rgba(124,58,237,0.1)', color: 'var(--accent-color)', padding: '2px 8px', borderRadius: 4 }}>{m}</span>)}
                      </div>
                    </div>
                  </section>
                )}

                {activeMessage.sql && (
                  <section style={{ marginBottom: 32 }}>
                    <div className="caption" style={{ marginBottom: 12 }}>Generated SQL</div>
                    <pre><code style={{ fontSize: 12, color: '#a78bfa' }}>{activeMessage.sql}</code></pre>
                  </section>
                )}

                {activeMessage.execution && activeMessage.execution.rows.length > 0 && (
                  <section>
                    <div className="caption" style={{ marginBottom: 12 }}>Data Result</div>
                    <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 12, overflow: 'hidden', border: '1px solid var(--glass-border)' }}>
                      <div style={{ overflowX: 'auto' }}>
                        <table className="result-table">
                          <thead>
                            <tr>
                              {activeMessage.execution.columns.map(c => <th key={c}>{c}</th>)}
                            </tr>
                          </thead>
                          <tbody>
                            {activeMessage.execution.rows.slice(0, 10).map((row, i) => (
                              <tr key={i}>
                                {activeMessage.execution!.columns.map(c => <td key={c}>{String(row[c])}</td>)}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {activeMessage.execution.row_count > 10 && (
                        <div style={{ padding: 12, textAlign: 'center', fontSize: 12, color: 'var(--text-muted)', borderTop: '1px solid var(--glass-border)' }}>
                          + {activeMessage.execution.row_count - 10} more rows
                        </div>
                      )}
                    </div>
                  </section>
                )}
              </div>
            </aside>
          )}
        </div>
      </main>
    </div>
  );
}
