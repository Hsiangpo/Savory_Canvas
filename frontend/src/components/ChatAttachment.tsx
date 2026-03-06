import { Film, Image as ImageIcon, Paperclip } from 'lucide-react';
import { useState } from 'react';
import * as api from '../api';

export function ChatImageAttachmentCard(
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

export function ChatAttachmentList(
  { attachments, isUser, onPreview }: { attachments: api.InspirationAttachment[]; isUser: boolean; onPreview: (url: string, name: string) => void },
) {
  if (!attachments.length) return null;
  return (
    <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      {attachments.map((attachment) => {
        if (attachment.type === 'image') {
          return (
            <ChatImageAttachmentCard
              key={attachment.id}
              attachment={attachment}
              isUser={isUser}
              onPreview={onPreview}
            />
          );
        }
        return (
          <div key={attachment.id} style={{ padding: '4px 8px', background: isUser ? 'rgba(255,255,255,0.2)' : 'var(--bg-glass)', borderRadius: '4px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
            {attachment.type === 'video' && <Film size={14} />}
            {(attachment.type === 'text' || attachment.type === 'transcript') && <Paperclip size={14} />}
            {attachment.name || attachment.id}
            {attachment.status === 'processing' && ' (正在思考中...)'}
            {attachment.status === 'failed' && ' (失败)'}
          </div>
        );
      })}
    </div>
  );
}
