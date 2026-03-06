import { X } from 'lucide-react';
import * as api from '../api';

export function CandidateEditor(
  {
    editableCandidates,
    allocationPlan,
    isLoading,
    onRemoveCandidateItem,
    actionArea,
  }: {
    editableCandidates: api.InspirationAssetCandidates | null;
    allocationPlan: api.InspirationAllocationPlanItem[];
    isLoading: boolean;
    onRemoveCandidateItem: (field: 'foods' | 'scenes' | 'keywords', value: string) => void;
    actionArea: React.ReactNode;
  },
) {
  if (!editableCandidates) return null;

  const renderCandidateGroup = (title: string, field: 'foods' | 'scenes' | 'keywords') => {
    const items = editableCandidates?.[field] || [];
    if (!items.length) return null;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{title}</div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {items.map((item) => (
            <button
              key={`${field}-${item}`}
              type="button"
              disabled={isLoading}
              onClick={() => onRemoveCandidateItem(field, item)}
              className="btn btn-secondary"
              style={{ padding: '2px 8px', fontSize: '0.78rem' }}
              title="点击移除"
            >
              {item} <X size={12} />
            </button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div style={{ marginBottom: '12px', padding: '12px', border: '1px solid var(--border-color)', borderRadius: '8px', background: 'var(--bg-glass)' }}>
      <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>每张图重点内容确认（可点击标签移除误提取项）</div>
      {allocationPlan.length > 0 && (
        <div style={{ display: 'grid', gap: '8px', marginBottom: '10px' }}>
          {allocationPlan.map((item) => (
            <div
              key={`allocation-${item.slot_index}-${item.focus_title}`}
              style={{
                padding: '8px',
                borderRadius: '8px',
                border: '1px solid var(--border-color)',
                background: 'rgba(255,255,255,0.03)',
              }}
            >
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                第{item.slot_index}张：{item.focus_title}
              </div>
              <div style={{ marginTop: '4px', fontSize: '0.82rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                {item.focus_description}
              </div>
            </div>
          ))}
        </div>
      )}
      {renderCandidateGroup('美食', 'foods')}
      {renderCandidateGroup('景点/场景', 'scenes')}
      {renderCandidateGroup('关键词', 'keywords')}
      <div style={{ marginTop: '10px' }}>{actionArea}</div>
    </div>
  );
}
