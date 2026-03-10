import { Film, Image as ImageIcon, Paperclip, X } from 'lucide-react';

export interface PendingUploadFile {
  id: string;
  file: File;
  previewUrl?: string;
}

export function ChatInput(
  {
    draftLocked,
    isLoading,
    inputText,
    pendingFiles,
    isInputDragActive,
    fileInputRef,
    inputTextRef,
    onInputChange,
    onFileChange,
    onInputDragOver,
    onInputDragLeave,
    onInputDrop,
    onRemovePendingFile,
    onPreviewImage,
    onSend,
  }: {
    draftLocked: boolean;
    isLoading: boolean;
    inputText: string;
    pendingFiles: PendingUploadFile[];
    isInputDragActive: boolean;
    fileInputRef: React.RefObject<HTMLInputElement | null>;
    inputTextRef: React.RefObject<HTMLTextAreaElement | null>;
    onInputChange: (value: string) => void;
    onFileChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
    onInputDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
    onInputDragLeave: (event: React.DragEvent<HTMLDivElement>) => void;
    onInputDrop: (event: React.DragEvent<HTMLDivElement>) => void;
    onRemovePendingFile: (pendingId: string) => void;
    onPreviewImage: (url: string, name: string) => void;
    onSend: () => void;
  },
) {
  const textareaId = 'chat-input-textarea';

  return (
    <>
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
                  onClick={() => onPreviewImage(item.previewUrl!, item.file.name)}
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
                <button onClick={() => onRemovePendingFile(item.id)} disabled={isLoading || draftLocked} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: 0, display: 'flex' }}>
                  <X size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div
        className={`input-group upload-dropzone ${isInputDragActive ? 'upload-dropzone-active' : ''}`}
        onDragOver={onInputDragOver}
        onDragLeave={onInputDragLeave}
        onDrop={onInputDrop}
        style={{ borderRadius: '8px', padding: '8px' }}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input type="file" ref={fileInputRef} hidden name="chat-attachment-input" aria-label="附件上传" style={{ display: 'none' }} multiple accept="image/*,video/*" onChange={onFileChange} />
          <button className="btn btn-secondary" style={{ padding: '8px' }} title="添加附件" disabled={isLoading || draftLocked} onClick={() => fileInputRef.current?.click()}>
            <Paperclip size={18} />
          </button>
          <label
            htmlFor={textareaId}
            style={{
              position: 'absolute',
              width: '1px',
              height: '1px',
              padding: 0,
              margin: '-1px',
              overflow: 'hidden',
              clip: 'rect(0, 0, 0, 0)',
              whiteSpace: 'nowrap',
              border: 0,
            }}
          >
            对话输入
          </label>
          <textarea
            id={textareaId}
            ref={inputTextRef}
            className="input"
            name="chat-input"
            aria-label="对话输入"
            placeholder={draftLocked ? '方案已锁定，可继续让 Agent 保存风格、开始生成或解释当前方案。' : '输入描述，可直接拖拽视频或图片到这里上传（Enter发送，Shift/Ctrl+Enter换行）。'}
            value={inputText}
            disabled={isLoading}
            onChange={(event) => onInputChange(event.target.value)}
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
                onSend();
              }
            }}
          />
          <button className="btn btn-primary" disabled={(!inputText.trim() && pendingFiles.length === 0) || isLoading} onClick={onSend}>
            发送
          </button>
        </div>
      </div>
    </>
  );
}
