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

interface ExistingSampleImage {
  asset_id: string;
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

const STYLE_SEARCH_INPUT_ID = 'style-management-search-input';
const STYLE_NAME_INPUT_ID = 'style-management-name-input';
const STYLE_PAINTING_INPUT_ID = 'style-management-painting-style-input';
const STYLE_COLOR_INPUT_ID = 'style-management-color-mood-input';
const STYLE_PROMPT_EXAMPLE_INPUT_ID = 'style-management-prompt-example-input';
const STYLE_KEYWORDS_INPUT_ID = 'style-management-keywords-input';
const STYLE_SAMPLE_IMAGES_INPUT_ID = 'style-management-sample-images-input';

export default function StyleManagementDrawer({ onClose, onApply }: Props) {
  const { styleProfileList, fetchStyles, createStyle, updateStyle, deleteStyle, activeSessionId, addToast } = useAppStore();
  const [editingStyle, setEditingStyle] = useState<Partial<StyleProfile> | null>(null);
  const [isFormVisible, setIsFormVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [existingSampleImages, setExistingSampleImages] = useState<ExistingSampleImage[]>([]);
  const [draftSampleImages, setDraftSampleImages] = useState<LocalSampleImage[]>([]);
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

  const visibleStyles = useMemo(() => {
    const builtinStyles = filteredStyles.filter((style) => style.is_builtin);
    const userStyles = filteredStyles.filter((style) => !style.is_builtin);
    return [...builtinStyles, ...userStyles];
  }, [filteredStyles]);

  const clearDraftSampleImages = useCallback(() => {
    setDraftSampleImages((current) => {
      current.forEach((item) => URL.revokeObjectURL(item.preview_url));
      return [];
    });
  }, []);

  const handleEdit = (style: StyleProfile) => {
    clearDraftSampleImages();
    setEditingStyle(style);
    const payloadIds = Array.isArray(style.style_payload?.sample_image_asset_ids)
      ? style.style_payload.sample_image_asset_ids
      : (style.style_payload?.sample_image_asset_id ? [style.style_payload.sample_image_asset_id] : []);
    const payloadUrls = Array.isArray(style.sample_image_preview_urls)
      ? style.sample_image_preview_urls
      : (style.sample_image_preview_url ? [style.sample_image_preview_url] : []);
    const merged: ExistingSampleImage[] = [];
    const maxLength = Math.max(payloadIds.length, payloadUrls.length);
    for (let index = 0; index < maxLength; index += 1) {
      const assetId = payloadIds[index];
      const previewUrl = payloadUrls[index];
      if (!assetId && !previewUrl) continue;
      merged.push({
        asset_id: assetId || `existing-${index + 1}`,
        preview_url: previewUrl || '',
      });
    }
    setExistingSampleImages(merged);
    setIsFormVisible(true);
    setFormRenderKey((value) => value + 1);
  };

  const handleAddNew = () => {
    clearDraftSampleImages();
    setEditingStyle(null);
    setExistingSampleImages([]);
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
    const sampleImageAssetIds = existingSampleImages
      .map((item) => item.asset_id)
      .filter((assetId) => assetId && !assetId.startsWith('existing-'));
    if (draftSampleImages.length > 0) {
      if (!activeSessionId) {
        addToast('请先选中一个会话，再保存带样例图的风格。', 'error');
        return;
      }
      setLoading(true);
      try {
        for (const draftImage of draftSampleImages) {
          const createdAsset = await api.uploadImageAsset(activeSessionId, draftImage.file);
          sampleImageAssetIds.push(createdAsset.id);
        }
      } catch {
        setLoading(false);
        addToast('样例图上传失败，请稍后重试。', 'error');
        return;
      }
    }
    const normalizedSampleImageAssetIds = Array.from(new Set(sampleImageAssetIds));
    const stylePayload: StylePayload = {
      painting_style: paintingStyle,
      color_mood: colorMood,
      style_prompt: promptExample,
      prompt_example: promptExample,
      sample_image_asset_id: normalizedSampleImageAssetIds[0] || undefined,
      sample_image_asset_ids: normalizedSampleImageAssetIds,
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
    setExistingSampleImages([]);
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
      return current.filter((item) => item.id !== draftId);
    });
  };

  const handleRemoveExistingSample = (assetId: string) => {
    setExistingSampleImages((current) => current.filter((item) => item.asset_id !== assetId));
  };

  const renderStyleCard = (style: StyleProfile) => (
    <div key={style.id} style={{ background: 'var(--bg-glass)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
        <div>
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{style.name}</div>
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
      {(style.sample_image_preview_urls?.length || style.sample_image_preview_url) && (
        <div style={{ marginTop: '10px', display: 'grid', gap: '8px' }}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))',
              gap: '8px',
            }}
          >
            {(style.sample_image_preview_urls?.length ? style.sample_image_preview_urls : [style.sample_image_preview_url]).map((previewUrl, index) => (
              <img
                key={`${style.id}-${index}`}
                src={previewUrl}
                alt={`风格样例图 ${index + 1}`}
                style={{ width: '100%', height: '84px', objectFit: 'cover', borderRadius: '6px', border: '1px solid var(--border-color)' }}
              />
            ))}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            共 {(style.sample_image_preview_urls?.length || 1)} 张样例图
          </div>
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
            <label htmlFor={STYLE_NAME_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格名称</label>
            <input id={STYLE_NAME_INPUT_ID} name="name" className="input" defaultValue={editingStyle?.name || ''} required />
            <label htmlFor={STYLE_PAINTING_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>绘画风格</label>
            <input id={STYLE_PAINTING_INPUT_ID} name="painting_style" className="input" defaultValue={editingStyle?.style_payload?.painting_style || ''} required />
            <label htmlFor={STYLE_COLOR_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>色彩情绪</label>
            <input id={STYLE_COLOR_INPUT_ID} name="color_mood" className="input" defaultValue={editingStyle?.style_payload?.color_mood || ''} required />
            <label htmlFor={STYLE_PROMPT_EXAMPLE_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格提示词样例（给智能体对话参考）</label>
            <textarea id={STYLE_PROMPT_EXAMPLE_INPUT_ID} name="prompt_example" className="input" defaultValue={editingStyle?.style_payload?.prompt_example || ''} required style={{ minHeight: '70px' }} />
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              系统会基于“风格提示词样例 + 对话上下文”自动生成当次母提示词，无需手动填写。
            </div>
            <label htmlFor={STYLE_KEYWORDS_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格细节关键词（可选，逗号分隔）</label>
            <input
              id={STYLE_KEYWORDS_INPUT_ID}
              name="extra_keywords"
              className="input"
              placeholder="示例：泛黄纸张，手绘箭头，纸胶带，复古邮票"
              defaultValue={(editingStyle?.style_payload?.extra_keywords || []).join(', ')}
            />
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              用来补充画面细节偏好（纹理、装饰、小元素、氛围词），会参与最终生图提示词生成。
            </div>
            <label htmlFor={STYLE_SAMPLE_IMAGES_INPUT_ID} style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>样例图</label>
            <input
              id={STYLE_SAMPLE_IMAGES_INPUT_ID}
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
              {existingSampleImages.length > 0 && (
                <div style={{ display: 'grid', gap: '8px' }}>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>已保存样例图（保存时可删除）</div>
                  <div
                    style={{
                      maxHeight: '220px',
                      overflowY: 'auto',
                      display: 'grid',
                      gap: '8px',
                      paddingRight: '4px',
                    }}
                  >
                    {existingSampleImages.map((item, index) => (
                      <div
                        key={`${item.asset_id}-${index}`}
                        style={{
                          border: '1px solid var(--border-color)',
                          background: 'var(--bg-glass)',
                          borderRadius: '8px',
                          padding: '8px',
                          display: 'grid',
                          gridTemplateColumns: '60px 1fr auto',
                          gap: '10px',
                          alignItems: 'center',
                        }}
                      >
                        {item.preview_url ? (
                          <img
                            src={item.preview_url}
                            alt={`已保存样例图 ${index + 1}`}
                            style={{
                              width: '60px',
                              height: '60px',
                              borderRadius: '6px',
                              objectFit: 'cover',
                              border: '1px solid var(--border-color)',
                            }}
                          />
                        ) : (
                          <div
                            style={{
                              width: '60px',
                              height: '60px',
                              borderRadius: '6px',
                              border: '1px dashed var(--border-color)',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: '0.68rem',
                              color: 'var(--text-muted)',
                            }}
                          >
                            无预览
                          </div>
                        )}
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: '0.84rem', color: 'var(--text-primary)' }}>样例图 {index + 1}</div>
                          <div style={{ marginTop: '4px', fontSize: '0.72rem', color: 'var(--text-muted)' }}>已保存到风格配置</div>
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => handleRemoveExistingSample(item.asset_id)}
                          style={{ padding: '6px' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {draftSampleImages.length > 0 && (
                <div style={{ display: 'grid', gap: '8px' }}>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>本次新增草稿图（保存时上传）</div>
                  <div
                    style={{
                      maxHeight: '220px',
                      overflowY: 'auto',
                      display: 'grid',
                      gap: '8px',
                      paddingRight: '4px',
                    }}
                  >
                    {draftSampleImages.map((item, index) => (
                      <div
                        key={item.id}
                        style={{
                          border: '1px solid var(--border-color)',
                          background: 'var(--bg-glass)',
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
                          alt={`草稿样例图 ${index + 1}`}
                          style={{
                            width: '60px',
                            height: '60px',
                            borderRadius: '6px',
                            objectFit: 'cover',
                            border: '1px solid var(--border-color)',
                          }}
                        />
                        <div title={item.file.name} style={{ minWidth: 0 }}>
                          <div style={{ fontSize: '0.84rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--text-primary)' }}>
                            {formatFileDisplayName(item.file, index)}
                          </div>
                          <div style={{ marginTop: '4px', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                            本地草稿（保存时上传）
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => handleRemoveDraftSample(item.id)}
                          style={{ padding: '6px' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {existingSampleImages.length === 0 && draftSampleImages.length === 0 && (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>未上传样例图，保存后将以纯文本风格参数生成。</div>
              )}
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <button type="submit" className="btn btn-primary" disabled={loading} style={{ flex: 1 }}>保存</button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={loading}
                style={{ flex: 1 }}
                onClick={() => {
                  setIsFormVisible(false);
                  setEditingStyle(null);
                  setExistingSampleImages([]);
                  clearDraftSampleImages();
                }}
              >
                取消
              </button>
            </div>
          </form>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '8px' }}>
              <label
                htmlFor={STYLE_SEARCH_INPUT_ID}
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
                搜索风格
              </label>
              <input
                id={STYLE_SEARCH_INPUT_ID}
                name="style-search"
                className="input"
                aria-label="搜索风格"
                placeholder="搜索风格名称/关键词"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
              <button className="btn btn-primary" onClick={handleAddNew} style={{ whiteSpace: 'nowrap' }}>
                <Plus size={16} /> 新增
              </button>
            </div>
            {renderStyleSection('风格库', visibleStyles)}
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
