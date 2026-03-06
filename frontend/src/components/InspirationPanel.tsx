import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Palette, Wand2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import { CandidateEditor } from './CandidateEditor';
import { ChatInput, type PendingUploadFile } from './ChatInput';
import { ChatMessageItem } from './ChatMessage';
import StyleManagementDrawer from './StyleManagementDrawer';
import { ThinkingBubble, type ThinkingStep } from './ThinkingBubble';

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

function getProgressLabel(draft: api.InspirationDraft | null): string {
  if (!draft) return '创作进行中';
  return draft.progress_label || draft.stage || '创作进行中';
}

function shouldUseCandidateRevision(actionHint: string | null | undefined): boolean {
  if (!actionHint) return false;
  const normalized = actionHint.trim().toLowerCase();
  return normalized.includes('revise') || normalized.includes('adjust');
}

export default function InspirationPanel() {
  const { activeSessionId, addToast, draft, setDraft, syncLatestJob } = useAppStore();
  const [messages, setMessages] = useState<api.InspirationMessage[]>([]);
  const [agentMeta, setAgentMeta] = useState<api.InspirationAgentMeta | null>(null);
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [inputText, setInputText] = useState('');
  const [pendingFiles, setPendingFiles] = useState<PendingUploadFile[]>([]);
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
  const lastMessageCountRef = useRef(0);

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
      setAgentMeta(response.agent || null);
      if (response.draft?.active_job_id) {
        void syncLatestJob(response.draft.active_job_id);
      }
    } catch {
      if (sessionIdRef.current !== sessionId) return;
      setMessages([{ id: Date.now().toString(), role: 'system', content: '连接失败，请稍后重试。', created_at: new Date().toISOString() }]);
      setAgentMeta(null);
    } finally {
      if (sessionIdRef.current === sessionId) setIsLoading(false);
    }
  }, [setDraft, syncLatestJob]);

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
    setIsStreaming(false);
    setThinkingSteps([]);
    setMessages([]);
    setDraft(null);
    setAgentMeta(null);
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
    const container = chatRef.current;
    if (!container) return;
    const previousCount = lastMessageCountRef.current;
    const nextCount = messages.length;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const shouldStickToBottom = previousCount === 0 || distanceFromBottom < 80;
    lastMessageCountRef.current = nextCount;
    if (nextCount > previousCount && shouldStickToBottom) {
      // 修复点：仅在新增消息且用户接近底部时自动滚动，避免阅读历史内容时被强制打断。
      container.scrollTop = container.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    const container = chatRef.current;
    if (!container || thinkingSteps.length === 0) return;
    container.scrollTop = container.scrollHeight;
  }, [thinkingSteps]);

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

  const currentOptions = useMemo(() => draft?.options?.items || [], [draft?.options]);

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

    const text = (overrideText ?? inputText).trim();
    const selection = customSelection || [];
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
    setIsLoading(true);
    setIsStreaming(true);
    setThinkingSteps([]);
    const requestSessionId = activeSessionId;

    try {
      const requestPayload = {
        session_id: activeSessionId,
        text,
        selected_items: selection,
        action: actionStr,
        images: imageUploads.length ? imageUploads.map((item) => item.file) : undefined,
        videos: videoUploads.length ? videoUploads.map((item) => item.file) : undefined,
      };

      await api.postInspirationMessageStream(requestPayload, (event) => {
        if (sessionIdRef.current !== requestSessionId) return;
        
        if (event.type === 'thinking' || event.type === 'tool_start' || event.type === 'tool_done') {
          setThinkingSteps((prev) => [
            ...prev,
            {
              id: `${Date.now()}-${Math.random()}`,
              type: event.type as 'thinking' | 'tool_start' | 'tool_done',
              message: event.message,
              toolName: 'tool_name' in event ? event.tool_name : undefined,
              durationMs: 'duration_ms' in event ? event.duration_ms : undefined,
              timestamp: Date.now(),
            }
          ]);
          setTimeout(() => {
            if (chatRef.current) {
              chatRef.current.scrollTop = chatRef.current.scrollHeight;
            }
          }, 50);
        } else if (event.type === 'done') {
          const response = event.data;
          setMessages(response.messages || []);
          setDraft(response.draft || null);
          setAgentMeta(response.agent || null);
          if (response.draft?.active_job_id) {
            void syncLatestJob(response.draft.active_job_id);
          }
        } else if (event.type === 'error') {
          addToast(`Agent 错误: ${event.message}`, 'error');
          fetchConversation(requestSessionId);
        }
      });
    } catch (error) {
      if (sessionIdRef.current !== requestSessionId) return;
      console.warn('Stream failed or transport error:', error);
      const typedError = error as { response?: { data?: { code?: string } } };
      if (typedError.response?.data?.code === 'E-1010') {
        addToast('当前模型不支持图片解析，请切换为视觉模型后重试。', 'error');
      } else {
        addToast('流式连接异常，已同步最新会话状态', 'error');
      }
      fetchConversation(requestSessionId);
    } finally {
      revokePreviewUrls(pendingSnapshot);
      if (sessionIdRef.current === requestSessionId) {
        setIsLoading(false);
        setIsStreaming(false);
        setThinkingSteps([]);
      }
    }
  };

  const handleAgentOptionClick = (option: api.AgentOption) => {
    const overrideText = shouldUseCandidateRevision(option.action_hint) ? buildCandidateRevisionText(editableCandidates) : undefined;
    handleSend(option.action_hint || undefined, [option.label], overrideText);
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px 20px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-glass)', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ color: 'var(--accent-color)', fontWeight: 600 }}>
              当前 Agent 阶段：{agentMeta?.dynamic_stage_label || getProgressLabel(draft)}
            </div>
            {agentMeta?.dynamic_stage && (
              <div style={{ color: 'var(--text-secondary)' }}>
                ({agentMeta.dynamic_stage})
              </div>
            )}
          </div>
          {typeof draft.progress === 'number' ? (
            <div style={{ display: 'grid', gap: '6px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                <span>{draft.progress_label || '创作进行中'}</span>
                <span>{draft.progress}%</span>
              </div>
              <div style={{ height: '8px', borderRadius: '999px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${Math.max(0, Math.min(100, draft.progress))}%`,
                    height: '100%',
                    borderRadius: '999px',
                    background: 'linear-gradient(90deg, #f59e0b 0%, #ef4444 100%)',
                    transition: 'width 220ms ease',
                  }}
                />
              </div>
            </div>
          ) : null}
          {agentMeta?.trace?.length ? (
            <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', display: 'grid', gap: '6px' }}>
              <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>Agent 工具链</div>
              {agentMeta.trace.map((item) => (
                <div
                  key={item.id}
                  style={{
                    padding: '8px 10px',
                    borderRadius: '8px',
                    border: '1px solid var(--border-color)',
                    background: 'rgba(255,255,255,0.03)',
                  }}
                >
                  <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                    {item.node}
                    {item.tool_name ? ` · ${item.tool_name}` : ''}
                  </div>
                  <div>{item.summary || item.decision || '无附加说明'}</div>
                </div>
              ))}
            </div>
          ) : null}
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
              <ChatMessageItem
                key={`${message.id}-${index}`}
                message={message}
                onPreview={openImagePreview}
              />
            ))}

            {isLoading && !isStreaming && (
              <div className="chat-bubble bot">
                <Loader2 size={16} className="animate-spin" /> Agent 正在思考...
              </div>
            )}

            {isStreaming && (
              <ThinkingBubble steps={thinkingSteps} isActive={true} />
            )}
          </div>

          {editableCandidates && (
            (draft?.allocation_plan?.length || 0) > 0
            || (editableCandidates.foods || []).length > 0
            || (editableCandidates.scenes || []).length > 0
            || (editableCandidates.keywords || []).length > 0
          ) && (
            <CandidateEditor
              editableCandidates={editableCandidates}
              allocationPlan={draft?.allocation_plan || []}
              isLoading={isLoading}
              onRemoveCandidateItem={removeCandidateItem}
              actionArea={null}
            />
          )}

          <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-color)', padding: '14px 0' }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
              {currentOptions.map((option) => (
                <button
                  key={`${option.action_hint || 'option'}-${option.label}`}
                  className="btn btn-secondary"
                  disabled={isLoading}
                  onClick={() => handleAgentOptionClick(option)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <ChatInput
              draftLocked={!!draft?.locked}
              isLoading={isLoading}
              inputText={inputText}
              pendingFiles={pendingFiles}
              isInputDragActive={isInputDragActive}
              fileInputRef={fileInputRef}
              inputTextRef={inputTextRef}
              onInputChange={setInputText}
              onFileChange={handleFileChange}
              onInputDragOver={handleInputDragOver}
              onInputDragLeave={handleInputDragLeave}
              onInputDrop={handleInputDrop}
              onRemovePendingFile={removePendingFile}
              onPreviewImage={openImagePreview}
              onSend={() => handleSend()}
            />
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
