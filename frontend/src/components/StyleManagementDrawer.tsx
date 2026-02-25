import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Plus, Edit2, Trash2, CheckCircle2, Upload } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import type { StyleProfile, StylePayload } from '../api';

interface Props {
  onClose: () => void;
  onApply: (style: StyleProfile) => void;
}

interface LocalSampleImage {
  id: string;
  file: File;
  preview_url: string;
}

function normalizeSearchText(style: StyleProfile): string {
  const payload = style.style_payload || ({} as StylePayload);
  const keywords = Array.isArray(payload.extra_keywords) ? payload.extra_keywords.join(' ') : '';
  return [
    style.name,
    payload.painting_style,
    payload.color_mood,
    payload.prompt_example,
    payload.style_prompt,
    keywords,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function formatFileDisplayName(file: File, index: number): string {
  const baseName = file.name.trim();
  if (!baseName) return `图片 ${index + 1}`;
  if (baseName.length <= 30) return `图片 ${index + 1} · ${baseName}`;
  return `图片 ${index + 1} · ${baseName.slice(0, 27)}...`;
}

export default function StyleManagementDrawer({ onClose, onApply }: Props) {
  const { styleProfileList, fetchStyles, createStyle, updateStyle, deleteStyle, activeSessionId, addToast } = useAppStore();
  const [editingStyle, setEditingStyle] = useState<Partial<StyleProfile> | null>(null);
  const [isFormVisible, setIsFormVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [selectedSampleImageAssetId, setSelectedSampleImageAssetId] = useState('');
  const [draftSampleImages, setDraftSampleImages] = useState<LocalSampleImage[]>([]);
  const [selectedDraftSampleId, setSelectedDraftSampleId] = useState('');
  const [formRenderKey, setFormRenderKey] = useState(0);
  const [isSampleDragActive, setIsSampleDragActive] = useState(false);
  const [styleToDelete, setStyleToDelete] = useState<string | null>(null);
  const draftSamplesRef = useRef<LocalSampleImage[]>([]);
  const uploadImageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchStyles();
  }, [fetchStyles]);

  useEffect(() => {
    draftSamplesRef.current = draftSampleImages;
  }, [draftSampleImages]);

  useEffect(() => () => {
    draftSamplesRef.current.forEach((item) => URL.revokeObjectURL(item.preview_url));
  }, []);

  const filteredStyles = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    if (!keyword) return styleProfileList;
    return styleProfileList.filter((style) => normalizeSearchText(style).includes(keyword));
  }, [searchText, styleProfileList]);

  const groupedStyles = useMemo(() => {
    const globalStyles = filteredStyles.filter((style) => style.is_builtin || !style.session_id);
    const sessionStyles = filteredStyles.filter((style) => style.session_id && style.session_id === activeSessionId);
    const otherStyles = filteredStyles.filter((style) => style.session_id && style.session_id !== activeSessionId);
    return { globalStyles, sessionStyles, otherStyles };
  }, [activeSessionId, filteredStyles]);

  const selectedDraftSample = useMemo(
    () => draftSampleImages.find((item) => item.id === selectedDraftSampleId) ?? null,
    [draftSampleImages, selectedDraftSampleId],
  );

  const clearDraftSampleImages = useCallback(() => {
    setDraftSampleImages((current) => {
      current.forEach((item) => URL.revokeObjectURL(item.preview_url));
      return [];
    });
    setSelectedDraftSampleId('');
  }, []);

  const handleEdit = (style: StyleProfile) => {
    clearDraftSampleImages();
    setEditingStyle(style);
    setSelectedSampleImageAssetId(style.style_payload?.sample_image_asset_id || '');
    setIsFormVisible(true);
    setFormRenderKey((value) => value + 1);
  };

  const handleAddNew = () => {
    clearDraftSampleImages();
    setEditingStyle(null);
    setSelectedSampleImageAssetId('');
    setIsFormVisible(true);
    setFormRenderKey((value) => value + 1);
  };

  const handleDelete = (id: string) => {
    setStyleToDelete(id);
  };

  const confirmDelete = async () => {
    if (!styleToDelete) return;
    setLoading(true);
    await deleteStyle(styleToDelete);
    setLoading(false);
    setStyleToDelete(null);
  };


  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const name = (form.elements.namedItem('name') as HTMLInputElement).value.trim();
    const paintingStyle = (form.elements.namedItem('painting_style') as HTMLInputElement).value.trim();
    const colorMood = (form.elements.namedItem('color_mood') as HTMLInputElement).value.trim();
    const promptExample = (form.elements.namedItem('prompt_example') as HTMLTextAreaElement).value.trim();
    const extraKeywordsRaw = (form.elements.namedItem('extra_keywords') as HTMLInputElement).value;
    let sampleImageAssetId = selectedSampleImageAssetId || undefined;
    if (selectedDraftSampleId) {
      const selectedDraft = draftSampleImages.find((item) => item.id === selectedDraftSampleId);
      if (!selectedDraft) {
        addToast('请选择有效样例图后再保存。', 'error');
        return;
      }
      if (!activeSessionId) {
        addToast('请先选中一个会话，再保存带样例图的风格。', 'error');
        return;
      }
      setLoading(true);
      try {
        const createdAsset = await api.uploadImageAsset(activeSessionId, selectedDraft.file);
        sampleImageAssetId = createdAsset.id;
      } catch {
        setLoading(false);
        addToast('样例图上传失败，请稍后重试。', 'error');
        return;
      }
    }
    const stylePayload: StylePayload = {
      painting_style: paintingStyle,
      color_mood: colorMood,
      style_prompt: promptExample,
      prompt_example: promptExample,
      sample_image_asset_id: sampleImageAssetId,
      extra_keywords: extraKeywordsRaw
        .split(/[，,]/)
        .map((item) => item.trim())
        .filter(Boolean),
    };

    setLoading(true);
    const success = editingStyle?.id
      ? await updateStyle(editingStyle.id, name, stylePayload)
      : await createStyle(undefined, name, stylePayload);
    setLoading(false);
    if (!success) return;
    setIsFormVisible(false);
    setEditingStyle(null);
    clearDraftSampleImages();
    setSelectedSampleImageAssetId('');
  };

  const handleUploadSampleImage = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (files.length === 0) return;
    addDraftSampleFiles(files);
    if (uploadImageInputRef.current) uploadImageInputRef.current.value = '';
  };

  const addDraftSampleFiles = (files: File[]) => {
    const imageFiles = files.filter((file) => file.type.startsWith('image/'));
    if (imageFiles.length === 0) {
      addToast('仅支持上传图片文件。', 'error');
      return;
    }
    const localImages = imageFiles.map((file, index) => ({
      id: `${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      preview_url: URL.createObjectURL(file),
    }));
    setDraftSampleImages((current) => [...current, ...localImages]);
    if (!selectedDraftSampleId) {
      setSelectedDraftSampleId(localImages[0].id);
    }
    setSelectedSampleImageAssetId('');
    addToast('样例图已加入草稿，点击保存后才会入库。', 'success');
  };

  const handleSampleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (loading) return;
    setIsSampleDragActive(true);
  };

  const handleSampleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsSampleDragActive(false);
  };

  const handleSampleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsSampleDragActive(false);
    if (loading) return;
    const droppedFiles = Array.from(event.dataTransfer.files || []);
    if (!droppedFiles.some((file) => file.type.startsWith('image/'))) {
      addToast('请拖拽图片文件到样例图上传区域。', 'error');
      return;
    }
    addDraftSampleFiles(droppedFiles);
    if (uploadImageInputRef.current) uploadImageInputRef.current.value = '';
  };

  const handleRemoveDraftSample = (draftId: string) => {
    setDraftSampleImages((current) => {
      const target = current.find((item) => item.id === draftId);
      if (target) URL.revokeObjectURL(target.preview_url);
      const next = current.filter((item) => item.id !== draftId);
      if (selectedDraftSampleId === draftId) {
        setSelectedDraftSampleId(next[0]?.id || '');
      }
      return next;
    });
  };

  const renderStyleCard = (style: StyleProfile) => (
    <div key={style.id} style={{ background: 'var(--bg-glass)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
        <div>
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{style.name}</div>
          <div style={{ marginTop: '4px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {style.is_builtin ? '全局预设' : style.session_id ? '会话风格' : '全局自定义'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button className="btn btn-primary" title="应用风格" onClick={() => onApply(style)} style={{ padding: '6px' }}>
            <CheckCircle2 size={16} />
          </button>
          {!style.is_builtin && (
            <>
              <button className="btn btn-secondary" title="编辑风格" onClick={() => handleEdit(style)} style={{ padding: '6px' }}>
                <Edit2 size={16} />
              </button>
              <button className="btn btn-secondary" title="删除风格" onClick={() => handleDelete(style.id)} style={{ padding: '6px' }}>
                <Trash2 size={16} />
              </button>
            </>
          )}
        </div>
      </div>
      <div style={{ marginTop: '8px', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
        <div><strong>绘画风格：</strong>{style.style_payload.painting_style || '-'}</div>
        <div><strong>色彩情绪：</strong>{style.style_payload.color_mood || '-'}</div>
        <div><strong>风格细节关键词：</strong>{(style.style_payload.extra_keywords || []).length > 0 ? style.style_payload.extra_keywords.join('、') : '无'}</div>
      </div>
      {style.sample_image_preview_url && (
        <div style={{ marginTop: '10px' }}>
          <img
            src={style.sample_image_preview_url}
            alt="风格样例图"
            style={{ width: '100%', maxHeight: '120px', objectFit: 'cover', borderRadius: '6px', border: '1px solid var(--border-color)' }}
          />
        </div>
      )}
    </div>
  );

  const renderStyleSection = (title: string, list: StyleProfile[]) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <h4 style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-secondary)' }}>{title}</h4>
      {list.length === 0 ? (
        <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>暂无记录</div>
      ) : (
        list.map(renderStyleCard)
      )}
    </div>
  );

  return (
    <div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: '420px', background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-color)', boxShadow: '-4px 0 24px rgba(0,0,0,0.3)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
      <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0, fontSize: '1.1rem' }}>风格管理</h3>
        <button className="btn btn-secondary" style={{ padding: '4px' }} onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {isFormVisible ? (
          <form key={formRenderKey} onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格名称</label>
            <input name="name" className="input" defaultValue={editingStyle?.name || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>绘画风格</label>
            <input name="painting_style" className="input" defaultValue={editingStyle?.style_payload?.painting_style || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>色彩情绪</label>
            <input name="color_mood" className="input" defaultValue={editingStyle?.style_payload?.color_mood || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格提示词样例（给智能体对话参考）</label>
            <textarea name="prompt_example" className="input" defaultValue={editingStyle?.style_payload?.prompt_example || ''} required style={{ minHeight: '70px' }} />
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              系统会基于“风格提示词样例 + 对话上下文”自动生成当次母提示词，无需手动填写。
            </div>
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格细节关键词（可选，逗号分隔）</label>
            <input
              name="extra_keywords"
              className="input"
              placeholder="示例：泛黄纸张，手绘箭头，纸胶带，复古邮票"
              defaultValue={(editingStyle?.style_payload?.extra_keywords || []).join(', ')}
            />
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              用来补充画面细节偏好（纹理、装饰、小元素、氛围词），会参与最终生图提示词生成。
            </div>
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>样例图</label>
            <input
              ref={uploadImageInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={handleUploadSampleImage}
              style={{ display: 'none' }}
            />
            <div
              onDragOver={handleSampleDragOver}
              onDragLeave={handleSampleDragLeave}
              onDrop={handleSampleDrop}
              className={`upload-dropzone ${isSampleDragActive ? 'upload-dropzone-active' : ''}`}
              style={{ borderRadius: '8px' }}
            >
              <button
                type="button"
                className="btn btn-secondary"
                disabled={loading}
                onClick={() => uploadImageInputRef.current?.click()}
                style={{ width: '100%' }}
              >
                <Upload size={16} /> 上传新样例图（支持拖拽）
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {draftSampleImages.length > 0 && (
                <div
                  style={{
                    maxHeight: '220px',
                    overflowY: 'auto',
                    display: 'grid',
                    gap: '8px',
                    paddingRight: '4px',
                  }}
                >
                  {draftSampleImages.map((item, index) => {
                    const selected = item.id === selectedDraftSampleId;
                    return (
                      <div
                        key={item.id}
                        style={{
                          border: `1px solid ${selected ? 'var(--accent-warning)' : 'var(--border-color)'}`,
                          background: selected ? 'rgba(255, 122, 89, 0.12)' : 'var(--bg-glass)',
                          borderRadius: '8px',
                          padding: '8px',
                          display: 'grid',
                          gridTemplateColumns: '60px 1fr auto',
                          gap: '10px',
                          alignItems: 'center',
                        }}
                      >
                        <img
                          src={item.preview_url}
                          alt={`样例图 ${index + 1}`}
                          style={{
                            width: '60px',
                            height: '60px',
                            borderRadius: '6px',
                            objectFit: 'cover',
                            border: '1px solid var(--border-color)',
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => setSelectedDraftSampleId(item.id)}
                          style={{
                            textAlign: 'left',
                            background: 'transparent',
                            border: 'none',
                            color: 'var(--text-primary)',
                            cursor: 'pointer',
                            minWidth: 0,
                          }}
                          title={item.file.name}
                        >
                          <div style={{ fontSize: '0.84rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {formatFileDisplayName(item.file, index)}
                          </div>
                          <div style={{ marginTop: '4px', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                            本地草稿（保存时上传）
                          </div>
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => handleRemoveDraftSample(item.id)}
                          style={{ padding: '6px' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
              {selectedDraftSample && (
                <div
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    padding: '8px',
                    background: 'var(--bg-glass)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>当前绑定样例图（保存后生效）</div>
                  <img
                    src={selectedDraftSample.preview_url}
                    alt="当前样例图预览"
                    style={{ width: '100%', maxHeight: '140px', objectFit: 'cover', borderRadius: '6px', border: '1px solid var(--border-color)' }}
                  />
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-primary)' }}>{selectedDraftSample.file.name}</div>
                </div>
              )}
              {!selectedDraftSample && editingStyle?.sample_image_preview_url && (
                <div
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    padding: '8px',
                    background: 'var(--bg-glass)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>当前绑定样例图（已保存，可替换）</div>
                  <img
                    src={editingStyle.sample_image_preview_url}
                    alt="当前样例图预览"
                    style={{ width: '100%', maxHeight: '140px', objectFit: 'cover', borderRadius: '6px', border: '1px solid var(--border-color)' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ padding: '6px 10px', color: 'var(--error)' }}
                      onClick={() => {
                        setSelectedSampleImageAssetId('');
                        addToast('已移除当前样例图，保存后生效。', 'info');
                      }}
                    >
                      <Trash2 size={14} /> 删除当前样例图
                    </button>
                  </div>
                </div>
              )}
              {!selectedDraftSample && !editingStyle?.sample_image_preview_url && (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>未上传样例图，保存后将以纯文本风格参数生成。</div>
              )}
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <button type="submit" className="btn btn-primary" disabled={loading} style={{ flex: 1 }}>保存</button>
              <button type="button" className="btn btn-secondary" disabled={loading} style={{ flex: 1 }} onClick={() => setIsFormVisible(false)}>取消</button>
            </div>
          </form>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                className="input"
                placeholder="搜索风格名称/关键词"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
              <button className="btn btn-primary" onClick={handleAddNew} style={{ whiteSpace: 'nowrap' }}>
                <Plus size={16} /> 新增
              </button>
            </div>
            {renderStyleSection('全局与系统风格', groupedStyles.globalStyles)}
            {renderStyleSection('智能体会话风格（只读参考）', groupedStyles.sessionStyles)}
            {renderStyleSection('其他会话风格（只读参考）', groupedStyles.otherStyles)}
          </>
        )}
      </div>

      {styleToDelete && (
        <div style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1000,
          background: 'rgba(0,0,0,0.6)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center'
        }}>
          <div style={{
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            borderRadius: '12px',
            padding: '24px',
            width: '320px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px'
          }}>
            <h3 style={{ margin: 0, fontSize: '1.1rem', color: 'var(--text-primary)' }}>删除风格</h3>
            <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-secondary)' }}>确认要删除这个风格吗？</p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '8px' }}>
              <button type="button" className="btn btn-secondary" onClick={() => setStyleToDelete(null)} disabled={loading}>
                取消
              </button>
              <button type="button" className="btn btn-primary" onClick={confirmDelete} disabled={loading}>
                确定
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
