import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Plus, Edit2, Trash2, CheckCircle2, Upload } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';
import type { Asset, StyleProfile, StylePayload } from '../api';

interface Props {
  onClose: () => void;
  onApply: (styleId: string) => void;
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

export default function StyleManagementDrawer({ onClose, onApply }: Props) {
  const { styleProfileList, fetchStyles, createStyle, updateStyle, deleteStyle, activeSessionId, addToast } = useAppStore();
  const [editingStyle, setEditingStyle] = useState<Partial<StyleProfile> | null>(null);
  const [isFormVisible, setIsFormVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [sessionImages, setSessionImages] = useState<Asset[]>([]);
  const [selectedSampleImageAssetId, setSelectedSampleImageAssetId] = useState('');
  const uploadImageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchStyles();
  }, [fetchStyles]);

  const loadSessionImages = useCallback(async () => {
    if (!activeSessionId) {
      setSessionImages([]);
      return;
    }
    try {
      const detail = await api.getSessionDetail(activeSessionId);
      const imageAssets = (detail.assets || []).filter(
        (asset) => asset.asset_type === 'image' && asset.status === 'ready',
      );
      setSessionImages(imageAssets);
    } catch {
      setSessionImages([]);
    }
  }, [activeSessionId]);

  useEffect(() => {
    let cancelled = false;
    const loadAssets = async () => {
      await loadSessionImages();
      if (cancelled) return;
    };
    loadAssets();
    return () => {
      cancelled = true;
    };
  }, [loadSessionImages]);

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

  const handleEdit = (style: StyleProfile) => {
    setEditingStyle(style);
    setSelectedSampleImageAssetId(style.style_payload?.sample_image_asset_id || '');
    setIsFormVisible(true);
  };

  const handleAddNew = () => {
    setEditingStyle(null);
    setSelectedSampleImageAssetId('');
    setIsFormVisible(true);
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm('确认要删除这个风格吗？')) return;
    setLoading(true);
    await deleteStyle(id);
    setLoading(false);
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const name = (form.elements.namedItem('name') as HTMLInputElement).value.trim();
    const paintingStyle = (form.elements.namedItem('painting_style') as HTMLInputElement).value.trim();
    const colorMood = (form.elements.namedItem('color_mood') as HTMLInputElement).value.trim();
    const stylePrompt = (form.elements.namedItem('style_prompt') as HTMLTextAreaElement).value.trim();
    const promptExample = (form.elements.namedItem('prompt_example') as HTMLTextAreaElement).value.trim();
    const extraKeywordsRaw = (form.elements.namedItem('extra_keywords') as HTMLInputElement).value;
    const sampleImageAssetId = selectedSampleImageAssetId || undefined;
    const stylePayload: StylePayload = {
      painting_style: paintingStyle,
      color_mood: colorMood,
      style_prompt: stylePrompt,
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
      : await createStyle(activeSessionId || undefined, name, stylePayload);
    setLoading(false);
    if (!success) return;
    setIsFormVisible(false);
    setEditingStyle(null);
    setSelectedSampleImageAssetId('');
  };

  const handleUploadSampleImage = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!activeSessionId) {
      addToast('请先选择会话后再上传样例图。', 'error');
      return;
    }
    if (!file.type.startsWith('image/')) {
      addToast('仅支持上传图片文件。', 'error');
      return;
    }
    setLoading(true);
    try {
      const created = await api.uploadImageAsset(activeSessionId, file);
      await loadSessionImages();
      setSelectedSampleImageAssetId(created.id);
      addToast('样例图上传成功，已自动选中。', 'success');
    } catch {
      addToast('样例图上传失败，请稍后重试。', 'error');
    } finally {
      if (uploadImageInputRef.current) uploadImageInputRef.current.value = '';
      setLoading(false);
    }
  };

  const renderStyleCard = (style: StyleProfile) => (
    <div key={style.id} style={{ background: 'var(--bg-glass)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
        <div>
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{style.name}</div>
          <div style={{ marginTop: '4px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {style.is_builtin ? '全局预设' : '会话风格'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button className="btn btn-primary" title="应用风格" onClick={() => onApply(style.id)} style={{ padding: '6px' }}>
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
          <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格名称</label>
            <input name="name" className="input" defaultValue={editingStyle?.name || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>绘画风格</label>
            <input name="painting_style" className="input" defaultValue={editingStyle?.style_payload?.painting_style || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>色彩情绪</label>
            <input name="color_mood" className="input" defaultValue={editingStyle?.style_payload?.color_mood || ''} required />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>风格提示词样例</label>
            <textarea name="prompt_example" className="input" defaultValue={editingStyle?.style_payload?.prompt_example || ''} required style={{ minHeight: '70px' }} />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>母提示词</label>
            <textarea name="style_prompt" className="input" defaultValue={editingStyle?.style_payload?.style_prompt || ''} required style={{ minHeight: '90px' }} />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>额外关键词（逗号分隔）</label>
            <input name="extra_keywords" className="input" defaultValue={(editingStyle?.style_payload?.extra_keywords || []).join(', ')} />
            <label style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>样例图（当前会话图片）</label>
            <input
              ref={uploadImageInputRef}
              type="file"
              accept="image/*"
              onChange={handleUploadSampleImage}
              style={{ display: 'none' }}
            />
            <button
              type="button"
              className="btn btn-secondary"
              disabled={loading || !activeSessionId}
              onClick={() => uploadImageInputRef.current?.click()}
            >
              <Upload size={16} /> 上传新样例图
            </button>
            <select
              name="sample_image_asset_id"
              className="input"
              value={selectedSampleImageAssetId}
              onChange={(event) => setSelectedSampleImageAssetId(event.target.value)}
            >
              <option value="">不绑定样例图</option>
              {sessionImages.map((asset) => (
                <option key={asset.id} value={asset.id}>{asset.content || asset.id}</option>
              ))}
            </select>
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
            {renderStyleSection('当前会话风格', groupedStyles.sessionStyles)}
            {renderStyleSection('其他会话风格', groupedStyles.otherStyles)}
          </>
        )}
      </div>
    </div>
  );
}
