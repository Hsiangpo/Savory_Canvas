import { useEffect, useState } from 'react';
import './ThinkingBubble.css';

export interface ThinkingStep {
  id: string;
  type: 'thinking' | 'tool_start' | 'tool_done';
  message: string;
  displayedMessage?: string;
  toolName?: string;
  durationMs?: number;
  timestamp: number;
}

interface ThinkingBubbleProps {
  steps: ThinkingStep[];
  isActive: boolean;
}

const TOOL_NAME_LABELS: Record<string, string> = {
  suggest_painting_style: '风格判断',
  extract_assets: '素材提取',
  recommend_city_content_combos: '内容推荐',
  generate_style_prompt: '提示词整理',
  allocate_assets_to_images: '分图规划',
  save_style: '保存风格',
  generate_images: '启动出图',
  reset_progress: '回退进度',
  generate_copy: '生成文案',
};

function resolveStepDurationMs(
  step: ThinkingStep,
  index: number,
  steps: ThinkingStep[],
  isActive: boolean,
  nowTimestamp: number,
): number | undefined {
  if (step.durationMs !== undefined) {
    return step.durationMs;
  }
  if (step.type !== 'thinking') {
    return undefined;
  }
  const nextStep = steps[index + 1];
  if (nextStep) {
    return Math.max(0, nextStep.timestamp - step.timestamp);
  }
  if (!isActive) {
    return undefined;
  }
  return Math.max(0, nowTimestamp - step.timestamp);
}

function formatDuration(durationMs: number | undefined): string | null {
  if (durationMs === undefined || durationMs < 100) {
    return null;
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

function resolveGenericThinkingLabel(
  steps: ThinkingStep[],
  index: number,
  isActive: boolean,
  durationLabel: string | null,
): string {
  const previousToolStep = [...steps.slice(0, index)].reverse().find((step) => step.toolName);
  const toolName = previousToolStep?.toolName || '';
  const longWait = isActive && durationLabel !== null && Number.parseFloat(durationLabel) >= 12;
  if (toolName === 'extract_assets') return longWait ? '正在等待提示词整理结果' : '正在整理提示词';
  if (toolName === 'generate_style_prompt') return longWait ? '正在等待分图方案返回' : '正在准备分图方案';
  if (toolName === 'allocate_assets_to_images') return longWait ? '正在等待生成任务创建' : '正在准备生成任务';
  if (toolName === 'generate_images') return longWait ? '正在同步生成进度' : '正在准备生成反馈';
  if (toolName === 'recommend_city_content_combos') return longWait ? '正在整理推荐内容' : '正在准备推荐内容';
  if (toolName === 'suggest_painting_style') return longWait ? '正在整理风格方向' : '正在整理风格方向';
  return longWait ? '模型响应较慢，请稍候' : '正在整理下一步';
}

function normalizeThinkingMessage(
  message: string,
  steps: ThinkingStep[],
  index: number,
  isActive: boolean,
  durationLabel: string | null,
): string {
  const normalized = sanitizeThinkingMessage(message).replace(/\.{3}$/, '').trim();
  if (normalized === '正在思考') {
    return '思考中';
  }
  if (normalized === '正在组织下一步') {
    return resolveGenericThinkingLabel(steps, index, isActive, durationLabel);
  }
  const lowered = normalized.toLowerCase();
  if (lowered.includes('contemplating response') || lowered.includes('empty transcript')) {
    return '正在整理转写内容';
  }
  if (/^[a-z0-9 ,.!?:;'"()_-]+$/i.test(normalized) && normalized.length > 0) {
    return resolveGenericThinkingLabel(steps, index, isActive, durationLabel);
  }
  return normalized;
}

function sanitizeThinkingMessage(message: string): string {
  return message.replace(/^\*+|\*+$/g, '').replace(/\*\*/g, '').trim();
}

function normalizeToolMessage(step: ThinkingStep): string {
  if (step.toolName === 'extract_assets') {
    return step.type === 'tool_start' ? '正在提取素材' : '素材提取完成';
  }
  if (step.toolName === 'generate_style_prompt') {
    return step.type === 'tool_start' ? '正在整理提示词' : '提示词已就绪';
  }
  if (step.toolName === 'allocate_assets_to_images') {
    return step.type === 'tool_start' ? '正在规划分图' : '分图规划完成';
  }
  if (step.toolName === 'recommend_city_content_combos') {
    return step.type === 'tool_start' ? '正在整理推荐内容' : '推荐内容已就绪';
  }
  if (step.toolName === 'suggest_painting_style') {
    return step.type === 'tool_start' ? '正在判断风格方向' : '风格方向已确定';
  }
  if (step.toolName === 'save_style') {
    return step.type === 'tool_start' ? '正在保存风格' : '风格已保存';
  }
  if (step.toolName === 'generate_images') {
    return step.type === 'tool_start' ? '正在启动生成' : '生成任务已创建';
  }
  if (step.toolName === 'generate_copy') {
    return step.type === 'tool_start' ? '正在生成文案' : '文案已生成';
  }
  return sanitizeThinkingMessage(step.message).replace(/\.{3}$/, '').trim();
}

function buildInlineMetaLabel(isRunning: boolean, durationLabel: string | null): string | null {
  if (isRunning && durationLabel) {
    return `· ${durationLabel}`;
  }
  if (isRunning) {
    return '· 进行中';
  }
  if (durationLabel) {
    return `· ${durationLabel}`;
  }
  return null;
}

export function ThinkingBubble({ steps, isActive }: ThinkingBubbleProps) {
  const [nowTimestamp, setNowTimestamp] = useState(() => Date.now());

  useEffect(() => {
    if (!isActive) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      setNowTimestamp(Date.now());
    }, 200);
    return () => window.clearInterval(timer);
  }, [isActive]);

  if (steps.length === 0 && !isActive) {
    return null;
  }

  return (
    <div className="thinking-bubble">
      <div className="thinking-content">
        {steps.map((step, index) => {
          const isLast = index === steps.length - 1;
          const isRunning = isLast && isActive && step.type !== 'tool_done';
          const toolLabel = step.toolName ? (TOOL_NAME_LABELS[step.toolName] || '工具') : '';
          const displayedMessage = step.displayedMessage ?? step.message;
          const durationLabel = formatDuration(resolveStepDurationMs(step, index, steps, isActive, nowTimestamp));
          const normalizedMessage = step.type === 'thinking'
            ? normalizeThinkingMessage(displayedMessage, steps, index, isActive, durationLabel)
            : normalizeToolMessage({ ...step, message: displayedMessage });
          const inlineMetaLabel = step.type === 'thinking'
            ? buildInlineMetaLabel(isRunning, durationLabel)
            : durationLabel;

          return (
            <div key={step.id} className={`thinking-step thinking-step-${step.type} fade-in`}>
              {toolLabel && (
                <div className="thinking-tool-label">
                  {step.type === 'tool_done' ? '工具完成' : '工具调用'} · {toolLabel}
                </div>
              )}
              <div className={`thinking-message-container ${step.type === 'thinking' ? 'thinking-message-container-inline' : ''}`}>
                <span className={`thinking-message ${isRunning && step.type === 'thinking' ? 'text-pulse-soft' : ''}`}>
                  {normalizedMessage}
                </span>
                {inlineMetaLabel && (
                  <span className={`thinking-duration ${step.type === 'thinking' ? 'thinking-duration-inline' : ''}`}>
                    {inlineMetaLabel}
                  </span>
                )}
              </div>
            </div>
          );
        })}

        {steps.length === 0 && isActive && (
          <div className="thinking-step thinking-step-thinking fade-in">
            <div className="thinking-message-container thinking-message-container-inline">
              <span className="thinking-message text-pulse-soft">思考中</span>
              <span className="thinking-duration thinking-duration-inline">· 进行中</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
