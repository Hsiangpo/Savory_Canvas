import { useState, useRef, useEffect } from 'react';
import { Bot, User, Wand2, Loader2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';

interface Message {
  id: string;
  sender: 'bot' | 'user';
  text: string;
  options?: api.StyleOptionBlock;
  selectedOptions?: string[];
  fallbackUsed?: boolean;
}

export default function StyleChatPanel() {
  const { activeSessionId, addToast } = useAppStore();
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [inputText, setInputText] = useState('');
  const [currentStage, setCurrentStage] = useState('painting_style');
  const [isFinished, setIsFinished] = useState(false);
  const [currentSelection, setCurrentSelection] = useState<string[]>([]);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Initial fetch when session changes
    if (activeSessionId) {
      setMessages([]);
      setIsFinished(false);
      setCurrentStage('painting_style');
      startChatSession(activeSessionId);
    } else {
      setMessages([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId]);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages, isLoading]);

  const startChatSession = async (sessionId: string) => {
    setIsLoading(true);
    try {
      const res = await api.styleChat({
        session_id: sessionId,
        stage: 'painting_style',
        user_reply: '',
        selected_items: []
      });
      addBotResponse(res);
    } catch {
      setMessages([{ id: Date.now().toString(), sender: 'bot', text: '你好！我是 Savory Assistant，请问您想配置什么样式的图文生成？(若无需配置可直接忽略此窗口并点击生成)' }]);
    } finally {
      setIsLoading(false);
    }
  };

  const addBotResponse = (res: api.StyleChatResponse) => {
    setCurrentSelection([]);
    setMessages(prev => [
      ...prev,
      {
        id: Date.now().toString(),
        sender: 'bot',
        text: res.reply,
        options: res.options || undefined,
        fallbackUsed: res.fallback_used
      }
    ]);
    if (res.is_finished) {
      setIsFinished(true);
    } else {
      setCurrentStage(res.next_stage || res.stage);
    }
  };

  const handleOptionClick = (e: React.MouseEvent, opt: string, max: number) => {
    e.stopPropagation();
    if (max <= 1) {
      handleSend([opt]);
    } else {
      setCurrentSelection(prev => {
        if (prev.includes(opt)) {
          return prev.filter(item => item !== opt);
        }
        if (prev.length >= max) {
          addToast(`最多只能选择 ${max} 项`, 'info');
          return prev;
        }
        return [...prev, opt];
      });
    }
  };

  const handleSend = async (customSelection?: string[]) => {
    if (!activeSessionId || isFinished || isLoading) return;
    
    const userText = customSelection ? customSelection.join(', ') : inputText;
    if (!userText.trim() && !customSelection) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      sender: 'user',
      text: userText,
      selectedOptions: customSelection
    };

    setMessages(prev => [...prev, userMessage]);
    setInputText('');
    setIsLoading(true);

    try {
      const res = await api.styleChat({
        session_id: activeSessionId,
        stage: currentStage,
        user_reply: userText,
        selected_items: customSelection || []
      });
      addBotResponse(res);
    } catch {
      setMessages(prev => [...prev, { id: 'err', sender: 'bot', text: '请求失败，请稍后重试' }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="panel-header">
        <h2 className="panel-title">
          <Wand2 size={20} color="var(--accent-color)" /> 风格配置对话
        </h2>
      </div>
      
      {!activeSessionId ? (
         <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
           请先在左侧选择或创建一个会话
         </div>
      ): (
        <div className="panel-content" style={{ display: 'flex', flexDirection: 'column', padding: '16px 20px 0 20px', gap: '0' }}>
          <div className="chat-container" style={{ flex: 1, overflowY: 'auto', paddingBottom: '20px', paddingRight: '12px' }} ref={chatRef}>
            {messages.map((msg, i) => (
              <div key={msg.id + i} className={`chat-bubble ${msg.sender}`}>
                {msg.sender === 'bot' ? (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px', color: 'var(--text-secondary)' }}>
                      <Bot size={16} /> Savory Assistant
                      {msg.fallbackUsed && <span style={{ fontSize: '0.75rem', color: '#fbbf24', background: 'rgba(245, 158, 11, 0.1)', padding: '2px 6px', borderRadius: '4px' }}>已启用默认选项</span>}
                    </div>
                    {msg.text}
                    {msg.options && msg.options.items?.length > 0 && (
                      <div style={{ marginTop: '16px' }}>
                        <div style={{ fontWeight: 500, marginBottom: '8px' }}>{msg.options.title} {msg.options.max > 1 ? `(多选，最多 ${msg.options.max} 项)` : '(单选)'}</div>
                        <div className="chat-options">
                          {msg.options.items.map(opt => {
                            const isSelected = i === messages.length - 1 && currentSelection.includes(opt);
                            return (
                              <button 
                                key={opt}
                                className={`chat-option ${isSelected ? 'selected' : ''}`}
                                disabled={isFinished || isLoading || (i !== messages.length - 1)}
                                onClick={(e) => handleOptionClick(e, opt, msg.options!.max)}
                              >
                                {opt}
                              </button>
                            );
                          })}
                        </div>
                        {msg.options.max > 1 && i === messages.length - 1 && !isFinished && (
                          <div style={{ marginTop: '12px' }}>
                            <button 
                              className="btn btn-primary" 
                              style={{ padding: '6px 16px', fontSize: '0.85rem' }}
                              disabled={currentSelection.length === 0 || isLoading}
                              onClick={() => handleSend(currentSelection)}
                            >
                              确认提交
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '8px', marginBottom: '8px', opacity: 0.9 }}>
                      我 <User size={16} />
                    </div>
                    {msg.text}
                  </>
                )}
              </div>
            ))}
            
            {isLoading && (
              <div className="chat-bubble bot">
                <Loader2 size={16} className="animate-spin" /> 正在思考中...
              </div>
            )}
          </div>

          <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-color)', padding: '16px 0' }}>
            <div className="input-group">
              <div style={{ display: 'flex', gap: '8px' }}>
                <input 
                  type="text" 
                  className="input" 
                  placeholder={isFinished ? "本次对话已完成" : "输入您的要求，例如：看起来能引起食欲..."} 
                  value={inputText}
                  disabled={isFinished || isLoading}
                  onClick={e => e.stopPropagation()}
                  onChange={e => setInputText(e.target.value)}
                  onKeyDown={e => {
                    e.stopPropagation();
                    if (e.key === 'Enter') handleSend();
                  }}
                />
                <button 
                  className="btn btn-primary" 
                  disabled={!inputText.trim() || isFinished || isLoading}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSend();
                  }}
                >
                  发送
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
