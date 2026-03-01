import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, User, Wand2, Loader2, Image as ImageIcon, Film, X, Paperclip, Palette } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAppStore } from '../store';
import * as api from '../api';
import StyleManagementDrawer from './StyleManagementDrawer';

interface PendingUploadFile {
  id: string;
  file: File;
  previewUrl?: string;
}

interface SendDisplayOverride {
  content: string;
  attachments?: api.InspirationAttachment[];
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
  if (!input) return '请继续调整每张图重点内容';
  const foods = (input.foods || []).join('、') || '无';
  const scenes = (input.scenes || []).join('、') || '无';
  const keywords = (input.keywords || []).join('、') || '无';
  return `请按以下重点内容继续调整并确认：美食：${foods}；景点：${scenes}；关键词：${keywords}。`;
}

function isBottomActionOption(option: string): boolean {
  const text = option.trim();
  return (
    text.includes('继续优化') ||
    text.includes('确定使用') ||
    text.includes('确认提示词') ||
    text.includes('确认分图并锁定') ||
    text.includes('继续调整分图') ||
    text.includes('保存风格') ||
    text.includes('暂不保存')
  );
}

function isPromptActionOption(option: string): boolean {
  const text = option.trim();
  return text.includes('继续优化') || text.includes('确定使用') || text.includes('确认提示词');
}

function shouldRenderInlineOptions(message: api.InspirationMessage): boolean {
  const items = message.options?.items || [];
  if (!items.length) return false;
  return !items.every((item) => isBottomActionOption(item));
}

function ChatImageAttachmentCard(
  { attachment, isUser, onPreview }: { attachment: api.InspirationAttachment; isUser: boolean; onPreview: (url: string, name: string) => void },
) {
  const [errorUrl, setErrorUrl] = useState<string | null>(null);
  const loadFailed = !!attachment.preview_url && errorUrl === attachment.preview_url;

  return (
    <div
      style={{
        width: '160px',
        background: isUser ? 'rgba(255,255,255,0.16)' : 'var(--bg-glass)',
        border: '1px solid var(--border-color)',
        borderRadius: '8px',
        padding: '6px',
      }}
    >
      {attachment.preview_url && !loadFailed ? (
        <img
          src={attachment.preview_url}
          alt={attachment.name || '图片'}
          style={{ width: '100%', height: '96px', objectFit: 'cover', borderRadius: '6px', cursor: 'zoom-in' }}
          onClick={() => onPreview(attachment.preview_url!, attachment.name || '图片')}
          onError={() => setErrorUrl(attachment.preview_url || null)}
        />
      ) : (
        <div
          style={{
            width: '100%',
            height: '96px',
            borderRadius: '6px',
            border: '1px dashed var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-muted)',
            fontSize: '0.78rem',
            gap: '6px',
          }}
        >
          <ImageIcon size={14} />
          预览失败
        </div>
      )}
      <div style={{ marginTop: '6px', fontSize: '0.76rem', opacity: 0.95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {attachment.name || '图片'}
      </div>
      <div style={{ marginTop: '2px', fontSize: '0.7rem', opacity: 0.82 }}>
        {attachment.status === 'failed' ? '预览失败' : ''}
      </div>
    </div>
  );
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
  const [isInputDragActive, setIsInputDragActive] = useState(false);
  const [previewModal, setPreviewModal] = useState<{ url: string; name: string } | null>(null);
  const [previewLoadFailed, setPreviewLoadFailed] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputTextRef = useRef<HTMLTextAreaElement>(null);
  const sessionIdRef = useRef<string | null>(activeSessionId);
  const pendingFilesRef = useRef<PendingUploadFile[]>([]);

  const revokePreviewUrls = useCallback((items: PendingUploadFile[]) => {
    items.forEach((item) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
  }, []);

  const openImagePreview = useCallback((url: string, name?: string) => {
    setPreviewLoadFailed(false);
    setPreviewModal({ url, name: name || '图片预览' });
  }, []);

  const closeImagePreview = useCallback(() => {
    setPreviewModal(null);
    setPreviewLoadFailed(false);
  }, []);

  const fetchConversation = useCallback(async (sessionId: string) => {
    setIsLoading(true);
    try {
      const response = await api.getInspirationConversation(sessionId);
      if (sessionIdRef.current !== sessionId) return;
      setMessages(response.messages || []);
      setDraft(response.draft || null);
    } catch {
      if (sessionIdRef.current !== sessionId) return;
      setMessages([{ id: Date.now().toString(), role: 'system', content: '连接失败，请稍后重试。', created_at: new Date().toISOString() }]);
    } finally {
      if (sessionIdRef.current === sessionId) setIsLoading(false);
    }
  }, [setDraft]);

  useEffect(() => {
    pendingFilesRef.current = pendingFiles;
  }, [pendingFiles]);

  useEffect(() => {
    return () => {
      revokePreviewUrls(pendingFilesRef.current);
    };
  }, [revokePreviewUrls]);

  useEffect(() => {
    if (!previewModal) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeImagePreview();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [closeImagePreview, previewModal]);

  useEffect(() => {
    sessionIdRef.current = activeSessionId;
    setIsLoading(false);
    setMessages([]);
    setDraft(null);
    setCurrentSelection([]);
    setPendingFiles((previous) => {
      revokePreviewUrls(previous);
      return [];
    });
    setInputText('');
    setEditableCandidates(null);
    closeImagePreview();
    if (activeSessionId) {
      fetchConversation(activeSessionId);
    }
  }, [activeSessionId, closeImagePreview, fetchConversation, revokePreviewUrls, setDraft]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages, isLoading, draft]);

  useEffect(() => {
    const element = inputTextRef.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [inputText]);

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
      .map((message, index) => (shouldRenderInlineOptions(message) ? index : -1))
      .reduce((max, current) => Math.max(max, current), -1);
  }, [messages]);

  const lastSaveDecisionMsgIndex = useMemo(() => {
    return messages
      .map((message, index) => (message.options?.items?.some((item) => item.includes('保存风格')) ? index : -1))
      .reduce((max, current) => Math.max(max, current), -1);
  }, [messages]);

  const isWaitingForSaveDecision = useMemo(() => {
    if (!draft?.locked || lastSaveDecisionMsgIndex < 0) return false;
    const hasNewAssistantAfterOptions = messages
      .slice(lastSaveDecisionMsgIndex + 1)
      .some((message) => message.role === 'assistant');
    return !hasNewAssistantAfterOptions;
  }, [draft?.locked, lastSaveDecisionMsgIndex, messages]);

  const promptActionState = useMemo(() => {
    if (draft?.stage !== 'prompt_revision' || draft.locked) {
      return { visible: false, allowConfirm: false };
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role !== 'assistant' && message.role !== 'system') continue;
      const items = message.options?.items || [];
      const hasPromptActions = items.some((item) => isPromptActionOption(item));
      if (!hasPromptActions) {
        return { visible: false, allowConfirm: false };
      }
      return {
        visible: true,
        allowConfirm: items.some((item) => item.includes('确定使用') || item.includes('确认提示词')),
      };
    }
    return { visible: false, allowConfirm: false };
  }, [draft?.locked, draft?.stage, messages]);

  const handleOptionClick = (event: React.MouseEvent, option: string, max: number, isLatestOption: boolean) => {
    event.stopPropagation();
    if (isLoading || !isLatestOption || draft?.locked) return;

    let actionValue: string | undefined;
    if (option.includes('确定使用') || option.includes('确认提示词')) actionValue = 'confirm_prompt';
    if (option.includes('确认资产')) actionValue = 'confirm_assets';
    if (option.includes('确认分图')) actionValue = 'confirm_allocation_plan';
    if (option.includes('继续调整资产')) actionValue = 'revise_assets';
    if (option.includes('继续调整分图')) actionValue = 'revise_allocation_plan';
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
    appendPendingFiles(Array.from(event.target.files));
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const appendPendingFiles = (files: File[]) => {
    if (!files.length) return;
    const acceptedFiles = files.filter((file) => file.type.startsWith('image/') || file.type.startsWith('video/'));
    if (!acceptedFiles.length) {
      addToast('仅支持拖拽图片或视频文件。', 'error');
      return;
    }
    const incomingFiles: PendingUploadFile[] = acceptedFiles.map((file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      file,
      previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
    }));
    setPendingFiles((previous) => [...previous, ...incomingFiles]);
  };

  const handleInputDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (isLoading || !!draft?.locked) return;
    setIsInputDragActive(true);
  };

  const handleInputDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsInputDragActive(false);
  };

  const handleInputDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsInputDragActive(false);
    if (isLoading || !!draft?.locked) return;
    appendPendingFiles(Array.from(event.dataTransfer.files || []));
  };

  const removePendingFile = (pendingId: string) => {
    setPendingFiles((previous) => {
      const target = previous.find((item) => item.id === pendingId);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return previous.filter((item) => item.id !== pendingId);
    });
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
    displayOverride?: SendDisplayOverride,
  ) => {
    if (!activeSessionId || isLoading) return;
    if (draft?.locked && actionStr !== 'save_style' && actionStr !== 'skip_save') return;

    const text = (overrideText ?? inputText).trim();
    const selection = customSelection || currentSelection;
    const pendingSnapshot = pendingFiles;
    const hasText = !!text;
    const hasSelection = selection.length > 0;
    const hasFiles = pendingSnapshot.length > 0;
    if (!hasText && !hasSelection && !hasFiles && !actionStr) return;

    const imageUploads = pendingSnapshot.filter((item) => item.file.type.startsWith('image/'));
    const videoUploads = pendingSnapshot.filter((item) => item.file.type.startsWith('video/'));

    if (hasText || hasSelection || hasFiles) {
      const userMessage: api.InspirationMessage = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content: displayOverride?.content || (hasSelection ? selection.join('、') : text || '已上传附件'),
        attachments: displayOverride?.attachments || pendingSnapshot.map((item) => ({
          id: `temp-att-${item.id}`,
          type: item.file.type.startsWith('video/') ? 'video' : 'image',
          name: item.file.name,
          status: 'processing',
          preview_url: item.previewUrl,
        })),
        created_at: new Date().toISOString(),
      };
      setMessages((previous) => [...previous, userMessage]);
    }

    setInputText('');
    setPendingFiles([]);
    setCurrentSelection([]);
    setIsLoading(true);
    const requestSessionId = activeSessionId;

    try {
      const response = await api.postInspirationMessage({
        session_id: activeSessionId,
        text,
        selected_items: selection,
        action: actionStr,
        images: imageUploads.length ? imageUploads.map((item) => item.file) : undefined,
        videos: videoUploads.length ? videoUploads.map((item) => item.file) : undefined,
      });
      if (sessionIdRef.current !== requestSessionId) return;
      setMessages(response.messages || []);
      setDraft(response.draft || null);
    } catch (error) {
      if (sessionIdRef.current !== requestSessionId) return;
      const typedError = error as { response?: { data?: { code?: string; message?: string } } };
      if (typedError.response?.data?.code === 'E-1010') {
        addToast('当前模型不支持图片解析，请切换为视觉模型后重试。', 'error');
      } else {
        addToast(typedError.response?.data?.message || '请求失败，请稍后重试。', 'error');
      }
      fetchConversation(requestSessionId);
    } finally {
      revokePreviewUrls(pendingSnapshot);
      if (sessionIdRef.current === requestSessionId) setIsLoading(false);
    }
  };

  const handleReviseAssets = () => {
    const revisionText = buildCandidateRevisionText(editableCandidates);
    handleSend('revise_allocation_plan', ['继续调整分图'], revisionText);
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
          <div style={{ color: draft.stage === 'asset_confirming' ? 'var(--accent-color)' : draft.stage === 'locked' ? 'var(--text-secondary)' : 'var(--text-muted)' }}>3. 分图确认</div>
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
                    </div>
                    <div className="chat-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px', marginBottom: '8px', opacity: 0.9 }}>
                      我 <User size={16} />
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{message.content}</div>
                  </>
                )}

                {message.attachments && message.attachments.length > 0 && (
                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap', justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start' }}>
                    {message.attachments.map((attachment) => {
                      if (attachment.type === 'image') {
                        return (
                          <ChatImageAttachmentCard
                            key={attachment.id}
                            attachment={attachment}
                            isUser={message.role === 'user'}
                            onPreview={openImagePreview}
                          />
                        );
                      }
                      return (
                        <div key={attachment.id} style={{ padding: '4px 8px', background: message.role === 'user' ? 'rgba(255,255,255,0.2)' : 'var(--bg-glass)', borderRadius: '4px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          {attachment.type === 'video' && <Film size={14} />}
                          {attachment.type === 'text' && <Paperclip size={14} />}
                          {attachment.type === 'transcript' && <Paperclip size={14} />}
                          {attachment.name || attachment.id}
                          {attachment.status === 'processing' && ' (正在思考中...)'}
                          {attachment.status === 'failed' && ' (失败)'}
                        </div>
                      );
                    })}
                  </div>
                )}

                {shouldRenderInlineOptions(message) ? (
                  <div style={{ marginTop: '14px' }}>
                    <div style={{ fontWeight: 500, marginBottom: '8px' }}>
                      {message.options?.title} {message.options?.max && message.options.max > 1 ? `(多选，最多 ${message.options.max} 项)` : '(单选)'}
                    </div>
                    <div className="chat-options">
                      {message.options?.items?.map((option) => {
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
                    {message.options?.max && message.options.max > 1 && index === lastOptionMsgIndex && (
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
                <Loader2 size={16} className="animate-spin" /> 正在思考中...
              </div>
            )}
          </div>

          {draft?.stage === 'asset_confirming' && editableCandidates && (
            <div style={{ marginBottom: '12px', padding: '12px', border: '1px solid var(--border-color)', borderRadius: '8px', background: 'var(--bg-glass)' }}>
              <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>每张图重点内容确认（可点击标签移除误提取项）</div>
              {(draft?.allocation_plan || []).length > 0 && (
                <div style={{ display: 'grid', gap: '8px', marginBottom: '10px' }}>
                  {(draft?.allocation_plan || []).map((item) => (
                    <div
                      key={`allocation-${item.slot_index}-${item.focus_title}`}
                      style={{
                        padding: '8px',
                        borderRadius: '8px',
                        border: '1px solid var(--border-color)',
                        background: 'rgba(255,255,255,0.03)',
                      }}
                    >
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        第{item.slot_index}张：{item.focus_title}
                      </div>
                      <div style={{ marginTop: '4px', fontSize: '0.82rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                        {item.focus_description}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {renderCandidateGroup('美食', 'foods')}
              {renderCandidateGroup('景点/场景', 'scenes')}
              {renderCandidateGroup('关键词', 'keywords')}
              <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                <button className="btn btn-primary" disabled={isLoading} onClick={() => handleSend('confirm_allocation_plan', ['确认分图并锁定'])}>
                  <Wand2 size={16} /> 确认分图并锁定
                </button>
                <button className="btn btn-secondary" disabled={isLoading} onClick={handleReviseAssets}>
                  继续调整分图
                </button>
              </div>
            </div>
          )}

          <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-color)', padding: '14px 0' }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
              {promptActionState.visible && promptActionState.allowConfirm && (
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
                  <div
                    key={item.id}
                    style={{
                      width: '200px',
                      padding: '6px 8px',
                      background: 'var(--bg-glass)',
                      borderRadius: '8px',
                      border: '1px solid var(--border-color)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px',
                    }}
                  >
                    {item.file.type.startsWith('image/') && item.previewUrl ? (
                      <button
                        type="button"
                        onClick={() => openImagePreview(item.previewUrl!, item.file.name)}
                        style={{
                          width: '100%',
                          height: '84px',
                          padding: 0,
                          border: 'none',
                          borderRadius: '6px',
                          overflow: 'hidden',
                          background: 'transparent',
                          cursor: 'zoom-in',
                        }}
                        title="点击查看大图"
                      >
                        <img
                          src={item.previewUrl}
                          alt={item.file.name}
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      </button>
                    ) : (
                      <div
                        style={{
                          width: '100%',
                          height: '48px',
                          borderRadius: '6px',
                          border: '1px dashed var(--border-color)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          color: 'var(--text-muted)',
                        }}
                      >
                        {item.file.type.startsWith('video/') ? <Film size={16} /> : <ImageIcon size={16} />}
                      </div>
                    )}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {item.file.type.startsWith('video/') ? <Film size={14} /> : <ImageIcon size={14} />}
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.82rem' }}>{item.file.name}</span>
                      <button onClick={() => removePendingFile(item.id)} disabled={isLoading || !!draft?.locked} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0, display: 'flex' }}>
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div
              className={`input-group upload-dropzone ${isInputDragActive ? 'upload-dropzone-active' : ''}`}
              onDragOver={handleInputDragOver}
              onDragLeave={handleInputDragLeave}
              onDrop={handleInputDrop}
              style={{ borderRadius: '8px', padding: '8px' }}
            >
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple accept="image/*,video/*" onChange={handleFileChange} />
                <button className="btn btn-secondary" style={{ padding: '8px' }} title="添加附件" disabled={isLoading || !!draft?.locked} onClick={() => fileInputRef.current?.click()}>
                  <Paperclip size={18} />
                </button>
                <textarea
                  ref={inputTextRef}
                  className="input"
                  placeholder={draft?.locked ? '方案已锁定，可直接在右侧生成。' : '输入描述，支持同时上传文本、图片、视频（Enter发送，Shift/Ctrl+Enter换行）。'}
                  value={inputText}
                  disabled={isLoading || !!draft?.locked}
                  onChange={(event) => setInputText(event.target.value)}
                  rows={1}
                  style={{
                    minHeight: '42px',
                    maxHeight: '180px',
                    resize: 'none',
                    overflowY: 'auto',
                    lineHeight: 1.5,
                  }}
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter'
                      && !event.shiftKey
                      && !event.ctrlKey
                      && !event.metaKey
                      && !event.altKey
                    ) {
                      event.preventDefault();
                      handleSend();
                    }
                  }}
                />
                <button className="btn btn-primary" disabled={(!inputText.trim() && pendingFiles.length === 0) || isLoading || !!draft?.locked} onClick={() => handleSend()}>
                  发送
                </button>
              </div>
              <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', paddingLeft: '4px' }}>
                可直接拖拽图片或视频到输入区上传。
              </div>
            </div>
          </div>
        </div>
      )}

      {previewModal && (
        <div className="modal-overlay" style={{ zIndex: 120 }} onClick={closeImagePreview}>
          <div
            className="modal-content"
            style={{ maxWidth: 'min(92vw, 1100px)', maxHeight: '92vh', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}
            onClick={(event) => event.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
              <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {previewModal.name}
              </div>
              <button className="btn btn-secondary" style={{ padding: '4px 10px' }} onClick={closeImagePreview}>
                关闭
              </button>
            </div>
            <div style={{ flex: 1, minHeight: '260px', maxHeight: 'calc(92vh - 90px)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.28)', borderRadius: '8px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
              {previewLoadFailed ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.88rem' }}>图片加载失败，请重试。</div>
              ) : (
                <img
                  src={previewModal.url}
                  alt={previewModal.name}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                  onError={() => setPreviewLoadFailed(true)}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {isStyleDrawerOpen && (
        <StyleManagementDrawer
          onClose={() => setIsStyleDrawerOpen(false)}
          onApply={(style) => {
            setIsStyleDrawerOpen(false);
            const styleLines = [
              `已选择风格：${style.name}`,
              `绘画风格：${style.style_payload.painting_style || '-'}`,
              `色彩情绪：${style.style_payload.color_mood || '-'}`,
            ];
            if ((style.style_payload.extra_keywords || []).length > 0) {
              styleLines.push(`风格细节关键词：${style.style_payload.extra_keywords.join('、')}`);
            }
            const styleAttachments: api.InspirationAttachment[] = style.sample_image_preview_url
              ? [{
                  id: `style-sample-${style.id}`,
                  asset_id: style.style_payload.sample_image_asset_id || undefined,
                  type: 'image',
                  name: '风格样例图',
                  preview_url: style.sample_image_preview_url,
                  status: 'ready',
                }]
              : [];
            handleSend('use_style_profile', [style.id], undefined, {
              content: styleLines.join('\n'),
              attachments: styleAttachments,
            });
          }}
        />
      )}
    </div>
  );
}
