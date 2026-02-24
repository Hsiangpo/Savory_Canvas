import { MessageSquarePlus, MessageCircle, MoreVertical } from 'lucide-react';
import { useAppStore } from '../store';
import { useState, useEffect } from 'react';
import * as api from '../api';

export default function SessionPanel() {
  const { sessionList, activeSessionId, setActiveSessionId, fetchSessions, createSession, renameSession, removeSession, addToast } = useAppStore();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newTitle, setNewTitle] = useState('未命名会话');

  const [dropdownOpen, setDropdownOpen] = useState<string | null>(null);
  const [renameSessionObj, setRenameSessionObj] = useState<api.Session | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [deleteSessionObj, setDeleteSessionObj] = useState<api.Session | null>(null);

  const openRename = (s: api.Session) => {
    setRenameSessionObj(s);
    setRenameTitle(s.title);
  };
  const handleRenameConfirm = async () => {
    if (renameSessionObj && renameTitle.trim()) {
      const success = await renameSession(renameSessionObj.id, renameTitle.trim());
      if (success) {
        setRenameSessionObj(null);
      }
    }
  };

  const openDelete = (s: api.Session) => {
    setDeleteSessionObj(s);
  };
  const handleDeleteConfirm = () => {
    if (deleteSessionObj) {
      removeSession(deleteSessionObj.id);
      setDeleteSessionObj(null);
    }
  };

  useEffect(() => {
    fetchSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCreateConfirm = () => {
    if (newTitle.trim()) {
      createSession(newTitle, 'food');
      setShowCreateModal(false);
      setNewTitle('未命名会话');
      addToast('会话已创建', 'success');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="panel-header">
        <h2 className="panel-title">会话列表</h2>
        <button className="btn btn-icon" onClick={() => setShowCreateModal(true)}>
          <MessageSquarePlus size={20} />
        </button>
      </div>
      
      <div className="panel-content" style={{ gap: '8px' }}>
        <button className="btn btn-primary" style={{ width: '100%', marginBottom: '16px' }} onClick={() => setShowCreateModal(true)}>
          + 新建会话
        </button>
        
        {sessionList.map(s => (
          <div 
            key={s.id}
            className={`session-item ${activeSessionId === s.id ? 'active' : ''}`}
            onClick={() => setActiveSessionId(s.id)}
          >
            <MessageCircle className="session-icon" />
            <div style={{ flex: 1, overflow: 'hidden' }}>
              <div style={{ fontWeight: 500, whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>{s.title}</div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '2px' }}>{new Date(s.created_at).toLocaleDateString()}</div>
            </div>
            <div style={{ position: 'relative' }}>
              <button className="btn btn-icon" style={{ padding: '4px' }} onClick={(e) => { e.stopPropagation(); setDropdownOpen(s.id === dropdownOpen ? null : s.id); }}>
                <MoreVertical size={16} />
              </button>
              {dropdownOpen === s.id && (
                <div style={{ position: 'absolute', right: 0, top: '100%', background: 'var(--panel-bg)', border: '1px solid var(--border-color)', borderRadius: '4px', zIndex: 10, padding: '4px', display: 'flex', flexDirection: 'column', minWidth: '100px', boxShadow: '0 4px 6px rgba(0,0,0,0.1)' }}>
                  <button className="btn btn-ghost" style={{ textAlign: 'left', padding: '6px 12px', fontSize: '0.85rem' }} onClick={(e) => { e.stopPropagation(); setDropdownOpen(null); openRename(s); }}>重命名</button>
                  <button className="btn btn-ghost" style={{ textAlign: 'left', padding: '6px 12px', fontSize: '0.85rem', color: 'var(--error)' }} onClick={(e) => { e.stopPropagation(); setDropdownOpen(null); openDelete(s); }}>删除</button>
                </div>
              )}
            </div>
          </div>
        ))}
        {sessionList.length === 0 && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', textAlign: 'center', marginTop: '16px' }}>
            暂无会话，立即创建吧~
          </div>
        )}
      </div>

      {showCreateModal && (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ padding: '24px' }}>
            <h3 style={{ marginBottom: '16px' }}>新建会话</h3>
            <div className="input-group">
              <label className="input-label">会话标题</label>
              <input 
                autoFocus
                className="input" 
                value={newTitle} 
                onChange={(e) => setNewTitle(e.target.value)} 
                placeholder="例如: 奶油草莓蛋糕设计" 
              />
            </div>
            <div style={{ marginTop: '24px', display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setShowCreateModal(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleCreateConfirm} disabled={!newTitle.trim()}>
                确认创建
              </button>
            </div>
          </div>
        </div>
      )}

      {renameSessionObj && (
        <div className="modal-overlay" onClick={() => setRenameSessionObj(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ padding: '24px' }}>
            <h3 style={{ marginBottom: '16px' }}>重命名会话</h3>
            <div className="input-group">
              <label className="input-label">新标题</label>
              <input 
                autoFocus
                className="input" 
                value={renameTitle} 
                onChange={(e) => setRenameTitle(e.target.value)} 
                placeholder="输入新标题" 
              />
            </div>
            <div style={{ marginTop: '24px', display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setRenameSessionObj(null)}>取消</button>
              <button className="btn btn-primary" onClick={handleRenameConfirm} disabled={!renameTitle.trim()}>
                确认保存
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteSessionObj && (
        <div className="modal-overlay" onClick={() => setDeleteSessionObj(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '400px', padding: '24px', textAlign: 'center' }}>
            <h3 style={{ marginBottom: '8px' }}>确定删除会话？</h3>
            <p style={{ color: 'var(--text-muted)', marginBottom: '24px', fontSize: '0.9rem' }}>
              删除会话将清理相关所有素材与导出任务，不可恢复，确定要继续吗？
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
              <button className="btn btn-secondary" onClick={() => setDeleteSessionObj(null)}>取消操作</button>
              <button className="btn btn-primary" style={{ background: 'var(--error)' }} onClick={handleDeleteConfirm}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
