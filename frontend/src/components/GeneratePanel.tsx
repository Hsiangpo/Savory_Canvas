import { Play, Loader2, XCircle, ChevronDown, ChevronUp } from 'lucide-react';
import { useAppStore } from '../store';
import { useEffect, useState, useMemo } from 'react';
import * as api from '../api';

export default function GeneratePanel() {
  const { latestJob, startJob, cancelJob, pollJobStatus, activeSessionId, addToast, draft, latestStages, isStartingJob, latestAssetBreakdown } = useAppStore();
  const [stagesExpanded, setStagesExpanded] = useState(true);

  const isRunning = latestJob?.status === 'running' || latestJob?.status === 'queued';
  const status = latestJob?.status || 'idle';
  const progressPercent = latestJob?.progress_percent || 0;

  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (isRunning) {
      interval = setInterval(() => {
        pollJobStatus();
      }, 1500);
    }
    return () => clearInterval(interval);
  }, [isRunning, pollJobStatus]);

  // Group stages: for stages with the same name, keep latest as primary, older as history
  const groupedStages = useMemo(() => {
    if (!latestStages || latestStages.length === 0) return [];
    type StageItem = typeof latestStages[number];
    const groups: { stage: string; latest: StageItem; history: StageItem[] }[] = [];
    
    for (const s of latestStages) {
      const existingGrp = groups.find(g => g.stage === s.stage);
      if (existingGrp) {
        existingGrp.history.push(existingGrp.latest);
        existingGrp.latest = s;
      } else {
        groups.push({
          stage: s.stage,
          latest: s,
          history: []
        });
      }
    }
    return groups;
  }, [latestStages]);

  // Determine the "current" (most recent non-success) stage for highlighting
  const currentStageName = useMemo(() => {
    if (!groupedStages.length) return null;
    for (let i = groupedStages.length - 1; i >= 0; i--) {
      if (groupedStages[i].latest.status !== 'success' && groupedStages[i].latest.status !== 'partial_success') return groupedStages[i].stage;
    }
    return null; // all success means done
  }, [groupedStages]);

  const handleStart = () => {
    if (!activeSessionId) {
      addToast('请先选择或创建一个会话', 'error');
      return;
    }
    if (draft?.locked) {
      if (!draft.draft_style_id) {
        addToast('锁定草案中未找到生成使用的风格', 'error');
        return;
      }
      if (!draft.image_count) {
        addToast('锁定草案中未找到生成使用的图片数量', 'error');
        return;
      }
      startJob(draft.draft_style_id, draft.image_count);
      return;
    }

    addToast('请先完成灵感对话确认草案并锁定', 'error');
  };

  return (
    <div className="generate-panel-container">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 className="panel-title">任务进度</h2>
        {status === 'idle' && <span className="status-badge">未开始</span>}
        {(status === 'running' || status === 'queued') && <span className="status-badge running"><Loader2 size={12} className="animate-spin" /> 正在生成</span>}
        {status === 'success' && <span className="status-badge success">生成完成</span>}
        {status === 'partial_success' && <span className="status-badge partial">部分生成失败</span>}
        {status === 'failed' && <span className="status-badge failed">生成失败</span>}
        {status === 'canceled' && <span className="status-badge failed">已被取消</span>}
      </div>

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          <span>{latestJob?.stage_message || '任务已创建，等待执行'}</span>
          <span>{progressPercent}%</span>
        </div>
        <div className="progress-bar-container">
          <div 
            className="progress-bar" 
            style={{ width: `${progressPercent}%`, backgroundColor: status === 'failed' ? 'var(--error)' : undefined }}
          ></div>
        </div>

        {status === 'failed' && latestJob?.error_message && (
          <div style={{ marginTop: '12px', padding: '12px', borderRadius: '8px', backgroundColor: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
            <h4 style={{ margin: '0 0 6px 0', fontSize: '0.9rem', color: 'var(--error)' }}>
              任务失败 {latestJob.error_code ? `(${latestJob.error_code})` : ''}
            </h4>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-primary)', wordBreak: 'break-all' }}>
              {latestJob.error_message}
            </div>
            {(latestJob.error_code === 'E-1004' || latestJob.error_message.includes('非图片内容')) && (
              <div style={{ marginTop: '8px', fontSize: '0.85rem', color: 'var(--accent-color)', fontWeight: 500 }}>
                💡 建议：当前生图模型返回非图片，请切换可用生图模型或检查提供商协议
              </div>
            )}
          </div>
        )}
      </div>

      {groupedStages.length > 0 && (
        <div className="stages-card">
          <button 
            className="stages-card-header" 
            onClick={() => setStagesExpanded(!stagesExpanded)}
          >
            <h3 style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-primary)' }}>执行阶段轨迹</h3>
            {stagesExpanded ? <ChevronUp size={16} color="var(--text-secondary)" /> : <ChevronDown size={16} color="var(--text-secondary)" />}
          </button>
          {stagesExpanded && (
            <div className="stages-card-body">
              {groupedStages.map((group, i) => {
                const stage = group.latest;
                const isCurrent = stage.stage === currentStageName;
                const hasHistory = group.history.length > 0;
                
                // Use implicit success if an older stage is stuck but subsequent stages have started
                const isImplicitSuccess = stage.status !== 'success' && stage.status !== 'failed' && stage.status !== 'partial_success' && i < groupedStages.length - 1;
                const isSuccessIcon = stage.status === 'success' || stage.status === 'partial_success' || isImplicitSuccess;
                
                const iconColor = isSuccessIcon ? 'var(--success)' : stage.status === 'failed' ? 'var(--error)' : 'var(--accent-color)';
                const iconElement = isSuccessIcon ? '✓' : stage.status === 'failed' ? '✗' : <Loader2 size={14} className="animate-spin" />;

                return (
                  <div key={i} className={`stage-item ${isCurrent ? 'stage-current' : ''}`} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                      <div className="stage-icon" style={{ color: iconColor }}>
                        {iconElement}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: isCurrent ? 700 : 500, color: isCurrent ? 'var(--accent-color)' : 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <span>{stage.stage}</span>
                          {hasHistory && <span style={{fontSize: '0.75rem', fontWeight: 'normal', color: 'var(--text-muted)'}}>{group.history.length + 1} 条记录</span>}
                        </div>
                        <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{stage.stage_message}</div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginTop: '2px' }}>
                          {new Date(stage.created_at).toLocaleString()}
                        </div>
                      </div>
                    </div>
                    
                    {hasHistory && (
                       <details style={{ marginLeft: '22px', marginTop: '4px', fontSize: '0.75rem' }}>
                         <summary style={{ cursor: 'pointer', color: 'var(--text-muted)', userSelect: 'none' }}>查看历史推进</summary>
                         <div style={{ paddingLeft: '8px', borderLeft: '2px solid var(--border-color)', marginTop: '6px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                           {group.history.map((h, hi) => (
                             <div key={hi}>
                               <div style={{ color: 'var(--text-secondary)' }}>{h.stage_message}</div>
                               <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>{new Date(h.created_at).toLocaleString()}</div>
                             </div>
                           ))}
                         </div>
                       </details>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {draft?.locked ? (
          <div className="stages-card" style={{ padding: '16px', backgroundColor: 'var(--bg-glass-hover)', border: '1px solid var(--accent-alpha)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '0.95rem', color: 'var(--accent-color)' }}>方案已锁定</h3>
              <div style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                待生成：{draft.image_count || '-'} 张
              </div>
            </div>
            
            {((draft.style_payload as api.StylePayload)?.painting_style) ? (
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', display: 'grid', gap: '8px' }}>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <strong style={{ minWidth: '65px', color: 'var(--text-primary)' }}>绘画风格：</strong>
                  <span>{String((draft.style_payload as api.StylePayload).painting_style)}</span>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <strong style={{ minWidth: '65px', color: 'var(--text-primary)' }}>色彩情绪：</strong>
                  <span>{String((draft.style_payload as api.StylePayload).color_mood)}</span>
                </div>
                {((draft.style_payload as api.StylePayload).extra_keywords?.length ?? 0) > 0 ? (
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <strong style={{ minWidth: '65px', color: 'var(--text-primary)' }}>关键词：</strong>
                    <span>{((draft.style_payload as api.StylePayload).extra_keywords || []).join('、')}</span>
                  </div>
                ) : null}
                {(draft.style_payload as api.StylePayload).style_prompt ? (
                  <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                    <strong style={{ minWidth: '65px', color: 'var(--text-primary)' }}>母提示词：</strong>
                    <span style={{ fontStyle: 'italic', opacity: 0.9 }}>
                      {String((draft.style_payload as api.StylePayload).style_prompt).length > 50 
                        ? String((draft.style_payload as api.StylePayload).style_prompt).substring(0, 50) + '...' 
                        : String((draft.style_payload as api.StylePayload).style_prompt)}
                    </span>
                  </div>
                ) : null}
              </div>
            ) : null}
            
            {(latestAssetBreakdown?.extracted?.foods?.length || latestAssetBreakdown?.extracted?.scenes?.length || latestAssetBreakdown?.extracted?.keywords?.length) ? (
              <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-color)', fontSize: '0.85rem' }}>
                <div style={{ marginBottom: '6px', color: 'var(--text-primary)', fontWeight: 500 }}>素材分配摘要：</div>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {(latestAssetBreakdown.extracted?.foods || []).map((f: string, i: number) => <span key={`f-${i}`} style={{ padding: '2px 8px', background: 'rgba(234, 179, 8, 0.1)', color: '#eab308', borderRadius: '12px', fontSize: '0.75rem' }}>{f}</span>)}
                  {(latestAssetBreakdown.extracted?.scenes || []).map((s: string, i: number) => <span key={`s-${i}`} style={{ padding: '2px 8px', background: 'rgba(56, 189, 248, 0.1)', color: '#38bdf8', borderRadius: '12px', fontSize: '0.75rem' }}>{s}</span>)}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="input" style={{ padding: '12px', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
             请先在左侧完成灵感对话确认方案并锁定
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: '12px' }}>
        <button 
          className="btn btn-primary" 
          style={{ flex: 1 }} 
          disabled={isRunning || isStartingJob || !activeSessionId || !draft?.locked}
          onClick={handleStart}
        >
          {isRunning || isStartingJob ? <><Loader2 size={16} className="animate-spin" /> 生成中...</> : <><Play size={16} /> 开始生成</>}
        </button>
        {isRunning && (
          <button 
            className="btn btn-secondary" 
            onClick={cancelJob}
            title="取消任务"
          >
            <XCircle size={18} color="var(--error)" />
          </button>
        )}
      </div>
    </div>
  );
}
