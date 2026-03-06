import { Bot, User } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import * as api from '../api';
import { ChatAttachmentList } from './ChatAttachment';

export function ChatMessageItem(
  {
    message,
    onPreview,
  }: {
    message: api.InspirationMessage;
    onPreview: (url: string, name: string) => void;
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
    </div>
  );
}
