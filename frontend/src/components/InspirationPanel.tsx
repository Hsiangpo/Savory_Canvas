import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Palette, Wand2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import { getErrorMessage } from '../getErrorMessage';
import { CandidateEditor } from './CandidateEditor';
import { ChatInput, type PendingUploadFile } from './ChatInput';
import { ChatMessageItem } from './ChatMessage';
import StyleManagementDrawer from './StyleManagementDrawer';

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

function isSaveDecisionOption(option: string): boolean {
  const text = option.trim();
  return text.includes('保存风格') || text.includes('暂不保存');
}

function isAllocationOption(option: string): boolean {
  const text = option.trim();
  return text.includes('确认分图') || text.includes('继续调整分图');
}

function resolveActionValue(option: string): string | undefined {
  const text = option.trim();
  if (text.includes('确定使用') || text.includes('确认提示词')) return 'confirm_prompt';
  if (text.includes('确认资产')) return 'confirm_assets';
  if (text.includes('确认分图')) return 'confirm_allocation_plan';
  if (text.includes('继续调整资产')) return 'revise_assets';
  if (text.includes('继续调整分图')) return 'revise_allocation_plan';
  if (text.includes('保存风格')) return 'save_style';
  if (text.includes('暂不保存')) return 'skip_save';
  return undefined;
}

function getStageLabel(stage: api.InspirationDraft['stage'] | undefined): string {
  if (stage === 'prompt_revision') return '张数与提示词确认';
  if (stage === 'asset_confirming') return '分图确认';
  if (stage === 'locked') return '锁定生成';
  return '风格确认';
}

function shouldRenderInlineOptions(message: api.InspirationMessage): boolean {
  const items = message.options?.items || [];
  if (!items.length) return false;
  return !items.every((item) => isBottomActionOption(item));
}

export default function InspirationPanel() {
  const { activeSessionId, addToast, draft, setDraft } = useAppStore();
  const [messages, setMessages] = useState<api.InspirationMessage[]>([]);
  const [agentMeta, setAgentMeta] = useState<api.InspirationAgentMeta | null>(null);
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
    } catch {
      if (sessionIdRef.current !== sessionId) return;
      setMessages([{ id: Date.now().toString(), role: 'system', content: '连接失败，请稍后重试。', created_at: new Date().toISOString() }]);
      setAgentMeta(null);
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
    setAgentMeta(null);
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
    if (agentMeta?.mode === 'langgraph') {
      const items = draft?.options?.items || [];
      return items.some((item) => isSaveDecisionOption(item));
    }
    if (!draft?.locked || lastSaveDecisionMsgIndex < 0) return false;
    const hasNewAssistantAfterOptions = messages
      .slice(lastSaveDecisionMsgIndex + 1)
      .some((message) => message.role === 'assistant');
    return !hasNewAssistantAfterOptions;
  }, [agentMeta?.mode, draft?.locked, draft?.options?.items, lastSaveDecisionMsgIndex, messages]);

  const promptActionState = useMemo(() => {
    if (agentMeta?.mode === 'langgraph') {
      const items = draft?.options?.items || [];
      return {
        visible: items.some((item) => isPromptActionOption(item)),
        allowConfirm: items.some((item) => item.includes('确定使用') || item.includes('确认提示词')),
      };
    }
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
  }, [agentMeta?.mode, draft?.locked, draft?.options?.items, draft?.stage, messages]);

  const dynamicBottomOptionBlock = useMemo(() => {
    if (agentMeta?.mode !== 'langgraph') return null;
    const optionBlock = draft?.options;
    if (!optionBlock?.items?.length) return null;
    const bottomItems = optionBlock.items.filter((item) => isBottomActionOption(item));
    if (!bottomItems.length) return null;
    return { ...optionBlock, items: bottomItems };
  }, [agentMeta?.mode, draft?.options]);

  const handleOptionClick = (event: React.MouseEvent, option: string, max: number, isLatestOption: boolean) => {
    event.stopPropagation();
    if (isLoading || !isLatestOption || draft?.locked) return;

    const actionValue = resolveActionValue(option);

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
      setAgentMeta(response.agent || null);
    } catch (error) {
      if (sessionIdRef.current !== requestSessionId) return;
      const typedError = error as { response?: { data?: { code?: string } } };
      if (typedError.response?.data?.code === 'E-1010') {
        addToast('当前模型不支持图片解析，请切换为视觉模型后重试。', 'error');
      } else {
        addToast(getErrorMessage(error), 'error');
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

  const renderDynamicActionButtons = (optionBlock: api.StyleOptionBlock, location: 'footer' | 'allocation') => {
    const items = optionBlock.items || [];
    if (!items.length) return null;
    if (optionBlock.max > 1) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div className="chat-options">
            {items.map((option) => {
              const isSelected = currentSelection.includes(option);
              return (
                <button
                  key={`${location}-${option}`}
                  className={`chat-option ${isSelected ? 'selected' : ''}`}
                  disabled={isLoading || !!draft?.locked}
                  onClick={(event) => handleOptionClick(event, option, optionBlock.max, true)}
                >
                  {option}
                </button>
              );
            })}
          </div>
          <button
            className="btn btn-primary"
            disabled={currentSelection.length === 0 || isLoading}
            onClick={() => handleSend(undefined, currentSelection)}
          >
            确认提交
          </button>
        </div>
      );
    }
    return (
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        {items.map((option) => {
          const primary = option.includes('确认') || option.includes('保存') || option.includes('生成');
          const actionValue = option.includes('继续调整分图')
            ? () => handleReviseAssets()
            : () => handleSend(resolveActionValue(option), [option]);
          return (
            <button
              key={`${location}-${option}`}
              className={primary ? 'btn btn-primary' : 'btn btn-secondary'}
              disabled={isLoading || !!draft?.locked}
              onClick={actionValue}
            >
              {option}
            </button>
          );
        })}
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px 20px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-glass)', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
          {agentMeta?.mode === 'langgraph' ? (
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ color: 'var(--accent-color)', fontWeight: 600 }}>
                当前 Agent 阶段：{agentMeta.dynamic_stage_label || getStageLabel(draft.stage)}
              </div>
              {agentMeta.dynamic_stage && (
                <div style={{ color: 'var(--text-secondary)' }}>
                  ({agentMeta.dynamic_stage})
                </div>
              )}
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '10px' }}>
              <div style={{ color: draft.stage === 'style_collecting' ? 'var(--accent-color)' : 'var(--text-secondary)' }}>1. 风格确认</div>
              <div>&gt;</div>
              <div style={{ color: draft.stage === 'prompt_revision' ? 'var(--accent-color)' : ['asset_confirming', 'locked'].includes(draft.stage) ? 'var(--text-secondary)' : 'var(--text-muted)' }}>2. 张数与提示词确认</div>
              <div>&gt;</div>
              <div style={{ color: draft.stage === 'asset_confirming' ? 'var(--accent-color)' : draft.stage === 'locked' ? 'var(--text-secondary)' : 'var(--text-muted)' }}>3. 分图确认</div>
              <div>&gt;</div>
              <div style={{ color: draft.stage === 'locked' ? 'var(--accent-color)' : 'var(--text-muted)' }}>4. 锁定生成</div>
            </div>
          )}
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
                index={index}
                currentSelection={currentSelection}
                isLoading={isLoading}
                isLocked={!!draft?.locked}
                lastOptionMsgIndex={lastOptionMsgIndex}
                shouldRenderInlineOptions={shouldRenderInlineOptions}
                onPreview={openImagePreview}
                onOptionClick={handleOptionClick}
                onSubmitSelection={() => handleSend(undefined, currentSelection)}
              />
            ))}

            {isLoading && (
              <div className="chat-bubble bot">
                <Loader2 size={16} className="animate-spin" /> {agentMeta?.mode === 'langgraph' ? 'Agent 正在思考...' : '正在思考中...'}
              </div>
            )}
          </div>

          {draft?.stage === 'asset_confirming' && editableCandidates && (
            <CandidateEditor
              editableCandidates={editableCandidates}
              allocationPlan={draft?.allocation_plan || []}
              isLoading={isLoading}
              onRemoveCandidateItem={removeCandidateItem}
              actionArea={
                agentMeta?.mode === 'langgraph' && dynamicBottomOptionBlock?.items.some((item) => isAllocationOption(item))
                  ? renderDynamicActionButtons(
                      { ...dynamicBottomOptionBlock, items: dynamicBottomOptionBlock.items.filter((item) => isAllocationOption(item)) },
                      'allocation',
                    )
                  : (
                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                      <button className="btn btn-primary" disabled={isLoading} onClick={() => handleSend('confirm_allocation_plan', ['确认分图并锁定'])}>
                        <Wand2 size={16} /> 确认分图并锁定
                      </button>
                      <button className="btn btn-secondary" disabled={isLoading} onClick={handleReviseAssets}>
                        继续调整分图
                      </button>
                    </div>
                  )
              }
            />
          )}

          <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-color)', padding: '14px 0' }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
              {agentMeta?.mode === 'langgraph' && dynamicBottomOptionBlock
                ? renderDynamicActionButtons(
                    {
                      ...dynamicBottomOptionBlock,
                      items: dynamicBottomOptionBlock.items.filter((item) => !isAllocationOption(item)),
                    },
                    'footer',
                  )
                : null}
              {agentMeta?.mode !== 'langgraph' && promptActionState.visible && promptActionState.allowConfirm && (
                <button className="btn btn-primary" onClick={() => handleSend('confirm_prompt', ['确定使用'])} disabled={isLoading}>
                  <Wand2 size={16} /> 确认提示词
                </button>
              )}
              {agentMeta?.mode !== 'langgraph' && isWaitingForSaveDecision && (
                <>
                  <button className="btn btn-primary" onClick={() => handleSend('save_style', ['保存风格'])} disabled={isLoading}>保存风格</button>
                  <button className="btn btn-secondary" onClick={() => handleSend('skip_save', ['暂不保存'])} disabled={isLoading}>暂不保存</button>
                </>
              )}
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
