import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Palette, Wand2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import { getErrorMessage, cleanErrorMessage } from '../getErrorMessage';
import { CandidateEditor } from './CandidateEditor';
import { ChatInput, type PendingUploadFile } from './ChatInput';
import { ChatMessageItem } from './ChatMessage';
import StyleManagementDrawer from './StyleManagementDrawer';
import { ThinkingBubble, type ThinkingStep } from './ThinkingBubble';

interface SendDisplayOverride {
  content: string;
  attachments?: api.InspirationAttachment[];
}

const STEP_STREAM_CHUNK = 8;
const STEP_STREAM_INTERVAL_MS = 12;
const REPLY_STREAM_CHUNK = 10;
const REPLY_STREAM_INTERVAL_MS = 10;
const AUTO_SCROLL_THRESHOLD_PX = 120;

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

function isGenericThinkingMessage(message: string): boolean {
  const normalized = message.trim();
  return normalized === '正在思考...' || normalized === '正在组织下一步...';
}

function sanitizeLiveThinkingMessage(message: string): string {
  const normalized = String(message || '').trim();
  if (!normalized) return '正在组织下一步...';
  if (isGenericThinkingMessage(normalized)) return normalized;
  if (normalized.startsWith('正在')) return normalized;
  return '正在组织下一步...';
}

function toThinkingTimestamp(createdAt?: string): number {
  const value = Date.parse(createdAt || '');
  return Number.isFinite(value) ? value : Date.now();
}

function buildThinkingStepsFromTrace(
  agent: api.InspirationAgentMeta | null | undefined,
  animated = false,
): ThinkingStep[] {
  if (!agent?.trace?.length) return [];
  const steps: ThinkingStep[] = [];
  for (const item of agent.trace) {
    if (item.node !== 'tool_node') continue;
    const message = String(item.summary || '').trim();
    if (!message) continue;
    steps.push({
      id: `trace-${item.id}`,
      type: 'tool_done',
      message,
      displayedMessage: animated ? '' : message,
      toolName: item.tool_name || undefined,
      timestamp: toThinkingTimestamp(item.created_at),
    });
  }
  return steps;
}

function mergeThinkingSteps(existing: ThinkingStep[], incoming: ThinkingStep[]): ThinkingStep[] {
  if (!incoming.length) return existing;
  if (!existing.length) return incoming;
  const signatures = new Set(existing.map((step) => step.type === 'thinking' ? `thinking|${step.message}` : `${step.type}|${step.toolName || ''}|${step.message}`));
  const merged = [...existing];
  for (const step of incoming) {
    const signature = step.type === 'thinking' ? `thinking|${step.message}` : `${step.type}|${step.toolName || ''}|${step.message}`;
    if (signatures.has(signature)) continue;
    signatures.add(signature);
    merged.push(step);
  }
  return merged.sort((a, b) => a.timestamp - b.timestamp);
}

function isSameThinkingStep(left: ThinkingStep | null | undefined, right: ThinkingStep | null | undefined): boolean {
  if (!left || !right) return false;
  if (left.type === "thinking" && right.type === "thinking") {
    return left.message === right.message;
  }
  return left.type === right.type && left.toolName === right.toolName && left.message === right.message;
}

function appendLiveThinkingStep(existing: ThinkingStep[], incoming: ThinkingStep): ThinkingStep[] {
  if (!existing.length) return [incoming];
  if (
    incoming.type === 'thinking'
    && existing.some((step) => step.type === 'thinking' && step.message === incoming.message)
  ) {
    return existing;
  }
  const latestStep = existing[existing.length - 1];
  if (
    incoming.type === 'thinking'
    && !isGenericThinkingMessage(incoming.message)
    && latestStep.type === 'thinking'
    && isGenericThinkingMessage(latestStep.message)
  ) {
    const replaced = [...existing.slice(0, -1), incoming];
    return isSameThinkingStep(replaced[replaced.length - 2], incoming) ? replaced.slice(0, -1) : replaced;
  }
  if (isSameThinkingStep(latestStep, incoming)) {
    return existing;
  }
  return [...existing, incoming];
}

function isNearBottom(container: HTMLDivElement): boolean {
  return container.scrollHeight - container.scrollTop - container.clientHeight <= AUTO_SCROLL_THRESHOLD_PX;
}

function getLatestAssistantMessage(messages: api.InspirationMessage[]): api.InspirationMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') {
      return messages[index];
    }
  }
  return null;
}

function areMessagesEquivalent(left: api.InspirationMessage[], right: api.InspirationMessage[]): boolean {
  if (left === right) return true;
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    const l = left[index];
    const r = right[index];
    if (l.id !== r.id || l.role !== r.role || l.content !== r.content || l.created_at !== r.created_at) return false;
    const lAttachments = l.attachments || [];
    const rAttachments = r.attachments || [];
    if (lAttachments.length !== rAttachments.length) return false;
    for (let attachmentIndex = 0; attachmentIndex < lAttachments.length; attachmentIndex += 1) {
      const la = lAttachments[attachmentIndex];
      const ra = rAttachments[attachmentIndex];
      if (la.id !== ra.id || la.status !== ra.status || la.name !== ra.name || la.type !== ra.type) return false;
    }
  }
  return true;
}

function finalizeThinkingSteps(steps: ThinkingStep[]): ThinkingStep[] {
  return steps.map((step) => ({
    ...step,
    displayedMessage: step.message,
  }));
}

function attachThinkingStepsToAssistantMessage(
  previous: Record<string, ThinkingStep[]>,
  messages: api.InspirationMessage[],
  steps: ThinkingStep[],
): Record<string, ThinkingStep[]> {
  const assistantMessage = getLatestAssistantMessage(messages);
  if (!assistantMessage || !steps.length) return previous;
  return {
    ...previous,
    [assistantMessage.id]: mergeThinkingSteps(previous[assistantMessage.id] || [], finalizeThinkingSteps(steps)),
  };
}

function createLiveThinkingStep(event: Extract<api.StreamEvent, { type: 'thinking' | 'tool_start' | 'tool_done' }>): ThinkingStep {
  const message = event.type === 'thinking'
    ? sanitizeLiveThinkingMessage(event.message)
    : event.message;
  return {
    id: `${Date.now()}-${Math.random()}`,
    type: event.type,
    message,
    displayedMessage: isGenericThinkingMessage(message) ? message : '',
    toolName: 'tool_name' in event ? event.tool_name : undefined,
    durationMs: 'duration_ms' in event ? event.duration_ms : undefined,
    timestamp: Date.now(),
  };
}

export default function InspirationPanel() {
  const { activeSessionId, addToast, draft, setDraft, syncLatestJob } = useAppStore();
  const [messages, setMessages] = useState<api.InspirationMessage[]>([]);
  const [agentMeta, setAgentMeta] = useState<api.InspirationAgentMeta | null>(null);
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [thinkingStepsByMessageId, setThinkingStepsByMessageId] = useState<Record<string, ThinkingStep[]>>({});
  const [pendingAssistantReply, setPendingAssistantReply] = useState('');
  const [pendingAssistantDisplayedReply, setPendingAssistantDisplayedReply] = useState('');
  const [pendingConversationResponse, setPendingConversationResponse] = useState<api.InspirationConversationResponse | null>(null);
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
  const shouldStickToBottomRef = useRef(true);
  const skipNextMessageAutoScrollRef = useRef(false);
  const isBackgroundFetchingRef = useRef(false);
  const streamAbortControllerRef = useRef<AbortController | null>(null);
  const isTurnBusy = isLoading || pendingConversationResponse !== null;

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

  const scrollChatToBottom = useCallback((force = false) => {
    const container = chatRef.current;
    if (!container) return;
    if (!force && !shouldStickToBottomRef.current) return;
    window.requestAnimationFrame(() => {
      const latestContainer = chatRef.current;
      if (!latestContainer) return;
      latestContainer.scrollTop = latestContainer.scrollHeight;
      shouldStickToBottomRef.current = true;
    });
  }, []);

  const fetchConversation = useCallback(async (sessionId: string, options?: { background?: boolean }) => {
    const background = options?.background === true;
    if (background) {
      if (isBackgroundFetchingRef.current) return;
      isBackgroundFetchingRef.current = true;
    }
    if (!background) {
      setIsLoading(true);
    }
    try {
      const response = await api.getInspirationConversation(sessionId);
      if (sessionIdRef.current !== sessionId) return;
      const nextMessages = response.messages || [];
      setMessages((previous) => {
        if (areMessagesEquivalent(previous, nextMessages)) {
          return previous;
        }
        if (background && !shouldStickToBottomRef.current) {
          skipNextMessageAutoScrollRef.current = true;
        }
        return nextMessages;
      });
      setDraft(response.draft || null);
      setAgentMeta(response.agent || null);
      setThinkingStepsByMessageId((previous) =>
        attachThinkingStepsToAssistantMessage(previous, nextMessages, buildThinkingStepsFromTrace(response.agent || null)),
      );
      if (response.draft?.active_job_id) {
        void syncLatestJob(response.draft.active_job_id);
      }
    } catch {
      if (sessionIdRef.current !== sessionId) return;
      if (!background) {
        setMessages([{ id: Date.now().toString(), role: 'system', content: '连接失败，请稍后重试。', created_at: new Date().toISOString() }]);
        setAgentMeta(null);
      }
    } finally {
      if (background) {
        isBackgroundFetchingRef.current = false;
      }
      if (!background && sessionIdRef.current === sessionId) setIsLoading(false);
    }
  }, [setDraft, syncLatestJob]);

  const shouldPollTranscriptUpdates = useMemo(() => {
    if (!activeSessionId || isTurnBusy) return false;
    return draft?.stage === 'transcribing_video';
  }, [activeSessionId, draft?.stage, isTurnBusy]);

  useEffect(() => {
    pendingFilesRef.current = pendingFiles;
  }, [pendingFiles]);

  useEffect(() => () => {
    streamAbortControllerRef.current?.abort();
    streamAbortControllerRef.current = null;
  }, []);

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
    let timer: ReturnType<typeof setTimeout>;
    if (isStreaming) {
      timer = setTimeout(() => {
        addToast('模型服务响应较慢，请稍候', 'info');
      }, 15000);
    }
    return () => clearTimeout(timer);
  }, [isStreaming, addToast]);

  useEffect(() => {
    sessionIdRef.current = activeSessionId;
    streamAbortControllerRef.current?.abort();
    streamAbortControllerRef.current = null;
    setIsLoading(false);
    setIsStreaming(false);
    setThinkingSteps([]);
    setThinkingStepsByMessageId({});
    setPendingAssistantReply('');
    setPendingAssistantDisplayedReply('');
    setPendingConversationResponse(null);
    isBackgroundFetchingRef.current = false;
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
    if (!activeSessionId || !shouldPollTranscriptUpdates) return undefined;
    const timer = window.setInterval(() => {
      void fetchConversation(activeSessionId, { background: true });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeSessionId, fetchConversation, shouldPollTranscriptUpdates]);

  useEffect(() => {
    const container = chatRef.current;
    if (!container) return;
    const updateStickiness = () => {
      shouldStickToBottomRef.current = isNearBottom(container);
    };
    updateStickiness();
    container.addEventListener('scroll', updateStickiness);
    return () => container.removeEventListener('scroll', updateStickiness);
  }, []);

  useEffect(() => {
    if (skipNextMessageAutoScrollRef.current) {
      skipNextMessageAutoScrollRef.current = false;
      return;
    }
    scrollChatToBottom();
  }, [messages, scrollChatToBottom]);

  useEffect(() => {
    const container = chatRef.current;
    if (!container || thinkingSteps.length === 0) return;
    scrollChatToBottom();
  }, [scrollChatToBottom, thinkingSteps]);

  useEffect(() => {
    if (!pendingAssistantDisplayedReply) return;
    scrollChatToBottom();
  }, [pendingAssistantDisplayedReply, scrollChatToBottom]);

  useEffect(() => {
    const currentStepIndex = thinkingSteps.findIndex(
      (step) => (step.displayedMessage ?? '') !== step.message,
    );
    if (currentStepIndex === -1) return;
    const timer = window.setInterval(() => {
      setThinkingSteps((previous) => {
        const target = previous[currentStepIndex];
        if (!target) return previous;
        const displayedMessage = target.displayedMessage ?? '';
        if (displayedMessage === target.message) return previous;
        const nextDisplayedMessage = target.message.slice(
          0,
          Math.min(target.message.length, displayedMessage.length + STEP_STREAM_CHUNK),
        );
        const next = [...previous];
        next[currentStepIndex] = { ...target, displayedMessage: nextDisplayedMessage };
        return next;
      });
    }, STEP_STREAM_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [thinkingSteps]);

  useEffect(() => {
    if (!pendingAssistantReply) return;
    if (thinkingSteps.some((step) => (step.displayedMessage ?? '') !== step.message)) return;
    if (pendingAssistantDisplayedReply === pendingAssistantReply) return;
    const timer = window.setInterval(() => {
      setPendingAssistantDisplayedReply((previous) =>
        pendingAssistantReply.slice(0, Math.min(pendingAssistantReply.length, previous.length + REPLY_STREAM_CHUNK)),
      );
    }, REPLY_STREAM_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [pendingAssistantDisplayedReply, pendingAssistantReply, thinkingSteps]);

  useEffect(() => {
    if (!pendingConversationResponse) return;
    if (thinkingSteps.some((step) => (step.displayedMessage ?? '') !== step.message)) return;
    if (pendingAssistantDisplayedReply !== pendingAssistantReply) return;
    setMessages(pendingConversationResponse.messages || []);
    setDraft(pendingConversationResponse.draft || null);
    setAgentMeta(pendingConversationResponse.agent || null);
    const traceThinkingSteps = buildThinkingStepsFromTrace(pendingConversationResponse.agent || null).filter(
      (step) => step.type === 'thinking',
    );
    setThinkingStepsByMessageId((previous) =>
      attachThinkingStepsToAssistantMessage(
        previous,
        pendingConversationResponse.messages || [],
        mergeThinkingSteps(thinkingSteps, traceThinkingSteps),
      ),
    );
    if (pendingConversationResponse.draft?.active_job_id) {
      void syncLatestJob(pendingConversationResponse.draft.active_job_id);
    }
    setThinkingSteps([]);
    setPendingAssistantReply('');
    setPendingAssistantDisplayedReply('');
    setPendingConversationResponse(null);
  }, [
    pendingAssistantDisplayedReply,
    pendingAssistantReply,
    pendingConversationResponse,
    setDraft,
    syncLatestJob,
    thinkingSteps,
  ]);

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
    if (isTurnBusy || !!draft?.locked) return;
    setIsInputDragActive(true);
  };

  const handleInputDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsInputDragActive(false);
  };

  const handleInputDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsInputDragActive(false);
    if (isTurnBusy || !!draft?.locked) return;
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
    if (!activeSessionId || isTurnBusy) return;

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
    setPendingAssistantReply('');
    setPendingAssistantDisplayedReply('');
    setPendingConversationResponse(null);
    const requestSessionId = activeSessionId;
    const streamAbortController = new AbortController();
    streamAbortControllerRef.current?.abort();
    streamAbortControllerRef.current = streamAbortController;

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
          setThinkingSteps((previous) => {
            const nextStep = createLiveThinkingStep(event);
            return appendLiveThinkingStep(previous, nextStep);
          });
          setTimeout(() => {
            scrollChatToBottom();
          }, 50);
        } else if (event.type === 'done') {
          const response = event.data;
          const latestAssistantMessage = getLatestAssistantMessage(response.messages || []);
          setThinkingSteps((previous) =>
            mergeThinkingSteps(previous, buildThinkingStepsFromTrace(response.agent || null, true)),
          );
          setPendingConversationResponse(response);
          setPendingAssistantReply(latestAssistantMessage?.content || '');
          setPendingAssistantDisplayedReply('');
        } else if (event.type === 'error') {
          addToast(cleanErrorMessage(event.message), 'error');
          fetchConversation(requestSessionId);
        }
      }, streamAbortController.signal);
    } catch (error) {
      if (sessionIdRef.current !== requestSessionId) return;
      const aborted = error instanceof DOMException
        ? error.name === 'AbortError'
        : (error as { name?: string }).name === 'AbortError';
      if (aborted) return;
      console.warn('Stream failed or transport error:', error);
      const typedError = error as { response?: { data?: { code?: string } } };
      if (typedError.response?.data?.code === 'E-1010') {
        addToast('当前模型不支持图片解析，请切换为视觉模型后重试。', 'error');
      } else {
        addToast(getErrorMessage(error), 'error');
      }
      fetchConversation(requestSessionId);
    } finally {
      revokePreviewUrls(pendingSnapshot);
      if (streamAbortControllerRef.current === streamAbortController) {
        streamAbortControllerRef.current = null;
      }
      if (sessionIdRef.current === requestSessionId) {
        setIsLoading(false);
        setIsStreaming(false);
        // 保留思考记录，便于用户回看刚刚的处理过程。
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


      {!activeSessionId ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          请先在左侧选择或创建一个会话。
        </div>
      ) : (
        <div className="panel-content" style={{ display: 'flex', flexDirection: 'column', padding: '16px 20px 0 20px' }}>
          <div className="chat-container" style={{ flex: 1, overflowY: 'auto', paddingBottom: '16px', paddingRight: '12px' }} ref={chatRef}>
            {messages.map((message, index) => {
              const persistedThinkingSteps = message.role === 'assistant'
                ? (thinkingStepsByMessageId[message.id] || [])
                : [];
              return (
                <Fragment key={`${message.id}-${index}`}>
                  {persistedThinkingSteps.length > 0 && (
                    <ThinkingBubble steps={persistedThinkingSteps} isActive={false} />
                  )}
                  <ChatMessageItem
                    message={message}
                    onPreview={openImagePreview}
                  />
                </Fragment>
              );
            })}

            {isLoading && !isStreaming && messages.length === 0 && thinkingSteps.length === 0 && !pendingAssistantDisplayedReply && (
              <div className="chat-bubble bot">
                <Loader2 size={16} className="animate-spin" /> Agent 正在准备会话内容...
              </div>
            )}

            {(isStreaming || thinkingSteps.length > 0) && (
              <ThinkingBubble steps={thinkingSteps} isActive={isStreaming} />
            )}

            {pendingAssistantDisplayedReply && (
              <ChatMessageItem
                message={{
                  id: 'pending-assistant',
                  role: 'assistant',
                  content: pendingAssistantDisplayedReply || '',
                  attachments: [],
                  created_at: new Date().toISOString(),
                }}
                onPreview={openImagePreview}
              />
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

          {activeSessionId && draft?.stage && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px', marginBottom: '12px', padding: '10px 12px', border: '1px solid var(--border-color)', borderRadius: '10px', background: 'var(--bg-glass)', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ color: 'var(--accent-color)', fontWeight: 600 }}>
                  当前 Agent 阶段：{agentMeta?.dynamic_stage_label || getProgressLabel(draft)}
                </div>
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
            </div>
          )}

          <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-color)', padding: '14px 0' }}>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
              {currentOptions.map((option) => (
                <button
                  key={`${option.action_hint || 'option'}-${option.label}`}
                  className="btn btn-secondary"
                  disabled={isTurnBusy}
                  onClick={() => handleAgentOptionClick(option)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <ChatInput
              draftLocked={!!draft?.locked}
              isLoading={isTurnBusy}
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
