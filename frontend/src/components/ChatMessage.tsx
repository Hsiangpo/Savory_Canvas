import { Bot, User } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import * as api from '../api';
import { ChatAttachmentList } from './ChatAttachment';

export function ChatMessageItem(
  {
    message,
    index,
    currentSelection,
    isLoading,
    isLocked,
    lastOptionMsgIndex,
    shouldRenderInlineOptions,
    onPreview,
    onOptionClick,
    onSubmitSelection,
  }: {
    message: api.InspirationMessage;
    index: number;
    currentSelection: string[];
    isLoading: boolean;
    isLocked: boolean;
    lastOptionMsgIndex: number;
    shouldRenderInlineOptions: (message: api.InspirationMessage) => boolean;
    onPreview: (url: string, name: string) => void;
    onOptionClick: (event: React.MouseEvent, option: string, max: number, isLatestOption: boolean) => void;
    onSubmitSelection: () => void;
  },
) {
  return (
    <div className={`chat-bubble ${message.role === 'user' ? 'user' : 'bot'}`}>
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

      <ChatAttachmentList attachments={message.attachments || []} isUser={message.role === 'user'} onPreview={onPreview} />

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
                  disabled={isLoading || isLocked || index !== lastOptionMsgIndex}
                  onClick={(event) => onOptionClick(event, option, message.options?.max || 1, index === lastOptionMsgIndex)}
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
                  onSubmitSelection();
                }}
              >
                确认提交
              </button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
