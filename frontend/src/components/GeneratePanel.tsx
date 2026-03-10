import { Loader2, XCircle, ChevronDown, ChevronUp } from 'lucide-react';
import { useAppStore } from '../store';
import { useEffect, useState, useMemo } from 'react';

const STAGE_NAME_MAP: Record<string, string> = {
  plan_ready: '方案加载',
  image_generate: '图片生成',
  copy_generate: '文案生成',
  finalize: '完成',
};

function normalizeStageName(stageName: string): string {
  if (stageName === 'asset_extract' || stageName === 'asset_allocate' || stageName === 'prompt_generate') {
    return 'plan_ready';
  }
  return stageName;
}

export default function GeneratePanel() {
  const { latestJob, cancelJob, pollJobStatus, latestStages } = useAppStore();
  const [stagesExpanded, setStagesExpanded] = useState(true);

  const isRunning = latestJob?.status === 'running' || latestJob?.status === 'queued';
  const status = latestJob?.status || 'idle';
  const progressPercent = latestJob?.progress_percent || 0;
  const currentJobStage = latestJob?.current_stage ? normalizeStageName(latestJob.current_stage) : null;

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
      const normalizedStage = normalizeStageName(s.stage);
      const stageItem = { ...s, stage: normalizedStage };
      const existingGrp = groups.find(g => g.stage === normalizedStage);
      if (existingGrp) {
        existingGrp.history.push(existingGrp.latest);
        existingGrp.latest = stageItem;
      } else {
        groups.push({
          stage: normalizedStage,
          latest: stageItem,
          history: []
        });
      }
    }
    return groups;
  }, [latestStages]);

  // Determine the "current" (most recent non-success) stage for highlighting
  const currentStageName = useMemo(() => {
    if (!groupedStages.length) return null;
    const terminalJobStatus = new Set(['success', 'partial_success', 'failed', 'canceled']);
    if (terminalJobStatus.has(status)) {
      return null;
    }
    for (let i = groupedStages.length - 1; i >= 0; i--) {
      const stageStatus = groupedStages[i].latest.status;
      if (stageStatus !== 'success' && stageStatus !== 'partial_success' && stageStatus !== 'failed' && stageStatus !== 'canceled') {
        return groupedStages[i].stage;
      }
    }
    return null; // all success means done
  }, [groupedStages, status]);

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
            {(latestJob.error_message.includes('非图片内容') || latestJob.error_message.includes('上游未返回可用图片数据')) && (
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
                
                const isTerminalFailedCurrent =
                  (status === 'failed' || status === 'canceled') &&
                  currentJobStage === stage.stage &&
                  stage.status !== 'success' &&
                  stage.status !== 'partial_success';
                const isSuccessIcon = stage.status === 'success' || stage.status === 'partial_success';
                const isFailedIcon = stage.status === 'failed' || stage.status === 'canceled' || isTerminalFailedCurrent;

                const iconColor = isSuccessIcon ? 'var(--success)' : isFailedIcon ? 'var(--error)' : 'var(--accent-color)';
                const iconElement = isSuccessIcon ? '✓' : isFailedIcon ? '✗' : <Loader2 size={14} className="animate-spin" />;

                return (
                  <div key={i} className={`stage-item ${isCurrent ? 'stage-current' : ''}`} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                      <div className="stage-icon" style={{ color: iconColor }}>
                        {iconElement}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: isCurrent ? 700 : 500, color: isCurrent ? 'var(--accent-color)' : 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <span>{STAGE_NAME_MAP[stage.stage] || stage.stage}</span>
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

      {status === 'idle' && (
        <div style={{
          textAlign: 'center',
          color: 'var(--text-secondary)',
          fontSize: '0.85rem',
          padding: '16px 0',
        }}>
          Agent 会在创作方案确认后自动启动生成
        </div>
      )}

      {isRunning && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0' }}>
          <button className="btn btn-secondary" onClick={cancelJob} title="取消任务">
            <XCircle size={18} color="var(--error)" /> 取消生成
          </button>
        </div>
      )}
    </div>
  );
}
