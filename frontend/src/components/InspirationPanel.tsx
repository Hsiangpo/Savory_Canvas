import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, User, Wand2, Loader2, Image as ImageIcon, Film, X, Paperclip, Palette } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import StyleManagementDrawer from './StyleManagementDrawer';

type ImageUsageType = 'style_reference' | 'content_asset';

interface PendingUploadFile {
  id: string;
  file: File;
  usageType?: ImageUsageType;
}

function normalizeCandidates(input?: api.InspirationAssetCandidates | null): api.InspirationAssetCandidates | null {
  if (!input) return null;
  const normalizeList = (values?: string[]) =>
    Array.from(new Set((values || []).map((item) => item.trim()).filter(Boolean)));
  return {
    foods: normalizeList(input.foods),
    scenes: normalizeList(input.scenes),
    keywords: normalizeList(input.keywords),
    source_asset_ids: input.source_asset_ids || [],
    confidence: input.confidence,
  };
}

function buildCandidateRevisionText(input: api.InspirationAssetCandidates | null): string {
  if (!input) return '请继续调整资产清单';
  const foods = (input.foods || []).join('、') || '无';
  const scenes = (input.scenes || []).join('、') || '无';
  const keywords = (input.keywords || []).join('、') || '无';
  return `请按以下资产清单继续调整并确认：美食：${foods}；场景：${scenes}；关键词：${keywords}。`;
}

function usageLabel(usageType?: ImageUsageType): string {
  return usageType === 'style_reference' ? '风格参考图' : '内容素材图';
}

export default function InspirationPanel() {
  const { activeSessionId, addToast, draft, setDraft } = useAppStore();
  const [messages, setMessages] = useState<api.InspirationMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [inputText, setInputText] = useState('');
  const [pendingFiles, setPendingFiles] = useState<PendingUploadFile[]>([]);
  const [currentSelection, setCurrentSelection] = useState<string[]>([]);
  const [isStyleDrawerOpen, setIsStyleDrawerOpen] = useState(false);
  const [editableCandidates, setEditableCandidates] = useState<api.InspirationAssetCandidates | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchConversation = useCallback(async (sessionId: string) => {
    setIsLoading(true);
    try {
      const response = await api.getInspirationConversation(sessionId);
      setMessages(response.messages || []);
      setDraft(response.draft || null);
    } catch {
      setMessages([{ id: Date.now().toString(), role: 'system', content: '连接失败，请稍后重试。', created_at: new Date().toISOString() }]);
    } finally {
      setIsLoading(false);
    }
  }, [setDraft]);

  useEffect(() => {
    if (activeSessionId) {
      setMessages([]);
      setDraft(null);
      setCurrentSelection([]);
      setPendingFiles([]);
      setInputText('');
      setEditableCandidates(null);
      fetchConversation(activeSessionId);
      return;
    }
    setMessages([]);
    setDraft(null);
    setEditableCandidates(null);
  }, [activeSessionId, fetchConversation, setDraft]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages, isLoading, draft]);

  const latestCandidateBlock = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].asset_candidates) return messages[i].asset_candidates;
    }
    return null;
  }, [messages]);

  useEffect(() => {
    if (draft?.stage === 'asset_confirming' && latestCandidateBlock) {
      setEditableCandidates(normalizeCandidates(latestCandidateBlock));
      return;
    }
    setEditableCandidates(null);
  }, [draft?.stage, latestCandidateBlock]);

  const lastOptionMsgIndex = useMemo(() => {
    return messages
      .map((message, index) => (message.options?.items?.length ? index : -1))
      .reduce((max, current) => Math.max(max, current), -1);
  }, [messages]);

  const isWaitingForSaveDecision = useMemo(() => {
    if (!draft?.locked || lastOptionMsgIndex < 0) return false;
    const lastOptionMessage = messages[lastOptionMsgIndex];
    const hasSaveOption = !!lastOptionMessage?.options?.items?.some((item) => item.includes('保存风格'));
    const hasNewAssistantAfterOptions = messages.slice(lastOptionMsgIndex + 1).some((message) => message.role === 'assistant');
    return hasSaveOption && !hasNewAssistantAfterOptions;
  }, [draft?.locked, lastOptionMsgIndex, messages]);

  const handleOptionClick = (event: React.MouseEvent, option: string, max: number, isLatestOption: boolean) => {
    event.stopPropagation();
    if (isLoading || !isLatestOption || draft?.locked) return;

    let actionValue: string | undefined;
    if (option.includes('确定使用') || option.includes('确认提示词')) actionValue = 'confirm_prompt';
    if (option.includes('确认资产')) actionValue = 'confirm_assets';
    if (option.includes('继续调整资产')) actionValue = 'revise_assets';
    if (option.includes('保存风格')) actionValue = 'save_style';
    if (option.includes('暂不保存')) actionValue = 'skip_save';

    if (max <= 1) {
      handleSend(actionValue, [option]);
      return;
    }
    setCurrentSelection((previous) => {
      if (previous.includes(option)) return previous.filter((item) => item !== option);
      if (previous.length >= max) {
        addToast(`最多只能选择 ${max} 项。`, 'info');
        return previous;
      }
      return [...previous, option];
    });
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!event.target.files) return;
    const incomingFiles: PendingUploadFile[] = Array.from(event.target.files).map((file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      file,
      usageType: file.type.startsWith('image/') ? ('content_asset' as ImageUsageType) : undefined,
    }));
    setPendingFiles((previous) => [...previous, ...incomingFiles]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removePendingFile = (pendingId: string) => {
    setPendingFiles((previous) => previous.filter((item) => item.id !== pendingId));
  };

  const updateImageUsage = (pendingId: string, usageType: ImageUsageType) => {
    setPendingFiles((previous) =>
      previous.map((item) => {
        if (item.id !== pendingId || !item.file.type.startsWith('image/')) return item;
        return { ...item, usageType };
      }),
    );
  };

  const removeCandidateItem = (field: 'foods' | 'scenes' | 'keywords', value: string) => {
    setEditableCandidates((previous) => {
      if (!previous) return previous;
      const source = previous[field] || [];
      return { ...previous, [field]: source.filter((item) => item !== value) };
    });
  };

  const handleSend = async (
    actionStr?: string,
    customSelection?: string[],
    overrideText?: string,
  ) => {
    if (!activeSessionId || isLoading) return;
    if (draft?.locked && actionStr !== 'save_style' && actionStr !== 'skip_save') return;

    const text = (overrideText ?? inputText).trim();
    const selection = customSelection || currentSelection;
    const hasText = !!text;
    const hasSelection = selection.length > 0;
    const hasFiles = pendingFiles.length > 0;
    if (!hasText && !hasSelection && !hasFiles && !actionStr) return;

    const imageUploads = pendingFiles.filter((item) => item.file.type.startsWith('image/'));
    const videoUploads = pendingFiles.filter((item) => item.file.type.startsWith('video/'));
    const imageUsages: ImageUsageType[] = imageUploads.map((item) => item.usageType || 'content_asset');

    if (hasText || hasSelection || hasFiles) {
      const userMessage: api.InspirationMessage = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content: hasSelection ? selection.join('、') : text || '已上传附件',
        attachments: pendingFiles.map((item) => ({
          id: `temp-att-${item.id}`,
          type: item.file.type.startsWith('video/') ? 'video' : 'image',
          name: item.file.name,
          status: 'processing',
          usage_type: item.usageType,
        })),
        created_at: new Date().toISOString(),
      };
      setMessages((previous) => [...previous, userMessage]);
    }

    setInputText('');
    setPendingFiles([]);
    setCurrentSelection([]);
    setIsLoading(true);

    try {
      const response = await api.postInspirationMessage({
        session_id: activeSessionId,
        text,
        selected_items: selection,
        action: actionStr,
        image_usages: imageUploads.length ? imageUsages : undefined,
        images: imageUploads.length ? imageUploads.map((item) => item.file) : undefined,
        videos: videoUploads.length ? videoUploads.map((item) => item.file) : undefined,
      });
      setMessages(response.messages || []);
      setDraft(response.draft || null);
    } catch (error) {
      const typedError = error as { response?: { data?: { code?: string; message?: string } } };
      if (typedError.response?.data?.code === 'E-1010') {
        addToast('当前模型不支持图片解析，请切换为视觉模型后重试。', 'error');
      } else {
        addToast(typedError.response?.data?.message || '请求失败，请稍后重试。', 'error');
      }
      fetchConversation(activeSessionId);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReviseAssets = () => {
    const revisionText = buildCandidateRevisionText(editableCandidates);
    handleSend('revise_assets', ['继续调整资产'], revisionText);
  };

  const renderCandidateGroup = (title: string, field: 'foods' | 'scenes' | 'keywords') => {
    const items = editableCandidates?.[field] || [];
    if (!items.length) return null;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{title}</div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {items.map((item) => (
            <button
              key={`${field}-${item}`}
              type="button"
              disabled={isLoading}
              onClick={() => removeCandidateItem(field, item)}
              className="btn btn-secondary"
              style={{ padding: '2px 8px', fontSize: '0.78rem' }}
              title="点击移除"
            >
              {item} <X size={12} />
            </button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative', overflow: 'hidden' }}>
      <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 className="panel-title" style={{ margin: 0 }}>
          <Wand2 size={20} color="var(--accent-color)" /> 灵感对话
        </h2>
        {activeSessionId && (
          <button className="btn btn-secondary" style={{ padding: '6px 12px' }} onClick={() => setIsStyleDrawerOpen(true)}>
            <Palette size={16} /> 风格管理
          </button>
        )}
      </div>

      {activeSessionId && draft?.stage && (
        <div style={{ display: 'flex', gap: '10px', padding: '10px 20px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-glass)', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
          <div style={{ color: draft.stage === 'style_collecting' ? 'var(--accent-color)' : 'var(--text-secondary)' }}>1. 风格确认</div>
          <div>&gt;</div>
          <div style={{ color: draft.stage === 'prompt_revision' ? 'var(--accent-color)' : ['asset_confirming', 'locked'].includes(draft.stage) ? 'var(--text-secondary)' : 'var(--text-muted)' }}>2. 张数与提示词确认</div>
          <div>&gt;</div>
          <div style={{ color: draft.stage === 'asset_confirming' ? 'var(--accent-color)' : draft.stage === 'locked' ? 'var(--text-secondary)' : 'var(--text-muted)' }}>3. 资产确认</div>
          <div>&gt;</div>
          <div style={{ color: draft.stage === 'locked' ? 'var(--accent-color)' : 'var(--text-muted)' }}>4. 锁定生成</div>
        </div>
      )}

      {!activeSessionId ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          请先在左侧选择或创建一个会话。
        </div>
      ) : (
        <div className="panel-content" style={{ display: 'flex', flexDirection: 'column', padding: '16px 20px 0 20px' }}>
          <div className="chat-container" style={{ flex: 1, overflowY: 'auto', paddingBottom: '16px', paddingRight: '12px' }} ref={chatRef}>
            {messages.map((message, index) => (
              <div key={`${message.id}-${index}`} className={`chat-bubble ${message.role === 'user' ? 'user' : 'bot'}`}>
                {message.role === 'assistant' || message.role === 'system' ? (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px', color: 'var(--text-secondary)' }}>
                      <Bot size={16} /> Savory Assistant
                      {message.fallback_used && <span style={{ fontSize: '0.75rem', color: '#fbbf24', background: 'rgba(245, 158, 11, 0.1)', padding: '2px 6px', borderRadius: '4px' }}>已启用默认候选方案</span>}
                    </div>
                    <div>{message.content}</div>
                  </>
                ) : (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px', marginBottom: '8px', opacity: 0.9 }}>
                      我 <User size={16} />
                    </div>
                    <div>{message.content}</div>
                  </>
                )}

                {message.attachments && message.attachments.length > 0 && (
                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap', justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start' }}>
                    {message.attachments.map((attachment) => (
                      <div key={attachment.id} style={{ padding: '4px 8px', background: message.role === 'user' ? 'rgba(255,255,255,0.2)' : 'var(--bg-glass)', borderRadius: '4px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {attachment.type === 'image' && <ImageIcon size={14} />}
                        {attachment.type === 'video' && <Film size={14} />}
                        {attachment.type === 'text' && <Paperclip size={14} />}
                        {attachment.type === 'transcript' && <Paperclip size={14} />}
                        {attachment.name || attachment.id}
                        {attachment.type === 'image' && <span style={{ fontSize: '0.72rem', opacity: 0.85 }}>[{usageLabel(attachment.usage_type)}]</span>}
                        {attachment.status === 'processing' && ' (处理中...)'}
                        {attachment.status === 'failed' && ' (失败)'}
                      </div>
                    ))}
                  </div>
                )}

                {message.options?.items?.length ? (
                  <div style={{ marginTop: '14px' }}>
                    <div style={{ fontWeight: 500, marginBottom: '8px' }}>
                      {message.options.title} {message.options.max > 1 ? `(多选，最多 ${message.options.max} 项)` : '(单选)'}
                    </div>
                    <div className="chat-options">
                      {message.options.items.map((option) => {
                        const isSelected = index === lastOptionMsgIndex && currentSelection.includes(option);
                        return (
                          <button
                            key={option}
                            className={`chat-option ${isSelected ? 'selected' : ''}`}
                            disabled={isLoading || !!draft?.locked || index !== lastOptionMsgIndex}
                            onClick={(event) => handleOptionClick(event, option, message.options?.max || 1, index === lastOptionMsgIndex)}
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                    {message.options.max > 1 && index === lastOptionMsgIndex && (
                      <div style={{ marginTop: '10px' }}>
                        <button
                          className="btn btn-primary"
                          style={{ padding: '6px 16px', fontSize: '0.85rem' }}
                          disabled={currentSelection.length === 0 || isLoading}
                          onClick={(event) => {
                            event.stopPropagation();
                            handleSend(undefined, currentSelection);
                          }}
                        >
                          确认提交
                        </button>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            ))}

            {isLoading && (
              <div className="chat-bubble bot">
                <Loader2 size={16} className="animate-spin" /> 正在处理中...
              </div>
            )}
          </div>

          {draft?.stage === 'asset_confirming' && editableCandidates && (
            <div style={{ marginBottom: '12px', padding: '12px', border: '1px solid var(--border-color)', borderRadius: '8px', background: 'var(--bg-glass)' }}>
              <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>资产确认（可点击标签移除误提取项）</div>
              {renderCandidateGroup('美食', 'foods')}
              {renderCandidateGroup('景点/场景', 'scenes')}
              {renderCandidateGroup('关键词', 'keywords')}
              <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                <button className="btn btn-primary" disabled={isLoading} onClick={() => handleSend('confirm_assets', ['确认资产并锁定'])}>
                  <Wand2 size={16} /> 确认资产并锁定
                </button>
                <button className="btn btn-secondary" disabled={isLoading} onClick={handleReviseAssets}>
                  继续调整资产
                </button>
              </div>
            </div>
          )}

          <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-color)', padding: '14px 0' }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
              {draft?.stage === 'prompt_revision' && !draft.locked && (
                <button className="btn btn-primary" onClick={() => handleSend('confirm_prompt', ['确定使用'])} disabled={isLoading}>
                  <Wand2 size={16} /> 确认提示词
                </button>
              )}
              {isWaitingForSaveDecision && (
                <>
                  <button className="btn btn-primary" onClick={() => handleSend('save_style', ['保存风格'])} disabled={isLoading}>保存风格</button>
                  <button className="btn btn-secondary" onClick={() => handleSend('skip_save', ['暂不保存'])} disabled={isLoading}>暂不保存</button>
                </>
              )}
            </div>

            {pendingFiles.length > 0 && (
              <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
                {pendingFiles.map((item) => (
                  <div key={item.id} style={{ padding: '6px 8px', background: 'var(--bg-glass)', borderRadius: '6px', border: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {item.file.type.startsWith('video/') ? <Film size={14} /> : <ImageIcon size={14} />}
                    <span style={{ maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.82rem' }}>{item.file.name}</span>
                    {item.file.type.startsWith('image/') && (
                      <div style={{ display: 'flex', gap: '4px' }}>
                        <button className="btn btn-secondary" style={{ padding: '2px 6px', fontSize: '0.72rem' }} disabled={isLoading || draft?.locked} onClick={() => updateImageUsage(item.id, 'style_reference')}>
                          风格参考图
                        </button>
                        <button className="btn btn-secondary" style={{ padding: '2px 6px', fontSize: '0.72rem' }} disabled={isLoading || draft?.locked} onClick={() => updateImageUsage(item.id, 'content_asset')}>
                          内容素材图
                        </button>
                      </div>
                    )}
                    {item.file.type.startsWith('image/') && <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{usageLabel(item.usageType)}</span>}
                    <button onClick={() => removePendingFile(item.id)} disabled={isLoading || !!draft?.locked} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0, display: 'flex' }}>
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="input-group">
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple accept="image/*,video/*" onChange={handleFileChange} />
                <button className="btn btn-secondary" style={{ padding: '8px' }} title="添加附件" disabled={isLoading || !!draft?.locked} onClick={() => fileInputRef.current?.click()}>
                  <Paperclip size={18} />
                </button>
                <input
                  type="text"
                  className="input"
                  placeholder={draft?.locked ? '方案已锁定，可直接在右侧生成。' : '输入描述，支持同时上传文本、图片、视频。'}
                  value={inputText}
                  disabled={isLoading || !!draft?.locked}
                  onChange={(event) => setInputText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') handleSend();
                  }}
                />
                <button className="btn btn-primary" disabled={(!inputText.trim() && pendingFiles.length === 0) || isLoading || !!draft?.locked} onClick={() => handleSend()}>
                  发送
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {isStyleDrawerOpen && (
        <StyleManagementDrawer
          onClose={() => setIsStyleDrawerOpen(false)}
          onApply={(styleId) => {
            setIsStyleDrawerOpen(false);
            handleSend('use_style_profile', [styleId]);
          }}
        />
      )}
    </div>
  );
}
