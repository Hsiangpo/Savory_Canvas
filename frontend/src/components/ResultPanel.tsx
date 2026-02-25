import { useEffect, useMemo, useRef, useState } from 'react';
import { Copy, Image as ImageIcon, FileText, Component, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import { useAppStore } from '../store';
import type { ImageResult } from '../api';

function ResultImage(
  { img, onPreview }: { img: ImageResult; onPreview: (url: string, title?: string) => void },
) {
  const [errorUrl, setErrorUrl] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const isError = errorUrl === img.image_url && retryCount > 0;

  const handleRetry = () => {
    setErrorUrl(null);
    setRetryCount(prev => prev + 1);
  };

  // Add cache busting on retries using retryCount as stable param
  const imgSrc = retryCount > 0
    ? `${img.image_url}${img.image_url.includes('?') ? '&' : '?'}_retry=${retryCount}`
    : img.image_url;

  return (
    <div className="result-image-box">
      {isError ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '8px', padding: '16px' }}>
          <div style={{ textAlign: 'center', color: 'var(--error)', fontSize: '0.85rem' }}>
            图片加载失败，请检查网络或后端日志
          </div>
          <button 
            className="btn btn-ghost" 
            onClick={handleRetry}
            style={{ fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--accent-color)' }}
          >
            <RefreshCw size={14} /> 重试加载
          </button>
        </div>
      ) : (
        <img 
          src={imgSrc} 
          alt={img.prompt_text} 
          className="result-image" 
          title={img.prompt_text} 
          style={{ cursor: 'zoom-in' }}
          onClick={() => onPreview(imgSrc, img.prompt_text || `生成图 ${img.image_index}`)}
          onError={() => {
            if (retryCount === 0) {
              // First failure: auto-retry once with cache busting
              setRetryCount(1);
            } else {
              // Second failure: show error state
              setErrorUrl(img.image_url);
            }
          }}
        />
      )}
    </div>
  );
}

export default function ResultPanel() {
  const { latestJob, latestResult, latestAssetBreakdown, latestStages, addToast, draft } = useAppStore();
  const [copyExpanded, setCopyExpanded] = useState(true);
  const [previewModal, setPreviewModal] = useState<{ url: string; title?: string } | null>(null);
  const [previewLoadFailed, setPreviewLoadFailed] = useState(false);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const copySectionRef = useRef<HTMLDivElement | null>(null);

  const isRunning = latestJob?.status === 'running' || latestJob?.status === 'queued';
  const isSuccess = latestJob?.status === 'success' || latestJob?.status === 'partial_success';
  const isPartialSuccess = latestJob?.status === 'partial_success';

  const copyToClipboard = async () => {
    if (latestResult?.copy?.full_text) {
      try {
        await navigator.clipboard.writeText(latestResult.copy.full_text);
        addToast('已复制到剪贴板', 'success');
      } catch (err) {
        console.error('Failed to copy', err);
      }
    }
  };

  const hasCopy = latestResult?.copy && (latestResult.copy.title || latestResult.copy.intro || latestResult.copy.full_text);
  const hasSections = (latestResult?.copy?.guide_sections?.length ?? 0) > 0;
  const resultSnapshotKey = [
    latestResult?.job_id ?? '',
    latestResult?.images?.length ?? 0,
    latestResult?.copy?.full_text ?? '',
    latestResult?.copy?.intro ?? '',
    latestResult?.copy?.guide_sections?.length ?? 0,
  ].join('|');
  const copyFailureReason = useMemo(() => {
    if (latestJob?.error_message && latestJob.error_message.includes('文案生成失败')) {
      return latestJob.error_message;
    }
    const copyStageItems = (latestStages || []).filter((stage) => stage.stage === 'copy_generate');
    if (!copyStageItems.length) return '';
    const latestCopy = copyStageItems[copyStageItems.length - 1];
    if (latestCopy.status === 'failed') return latestCopy.stage_message || '文案生成失败';
    return '';
  }, [latestJob?.error_message, latestStages]);

  const scrollToCopySection = () => {
    if (!contentRef.current || !copySectionRef.current) return;
    const container = contentRef.current;
    const target = copySectionRef.current;
    const top = target.offsetTop - 8;
    container.scrollTo({ top: top > 0 ? top : 0, behavior: 'smooth' });
  };

  useEffect(() => {
    if (!isSuccess || !latestResult) return;
    if (!contentRef.current) return;
    const container = contentRef.current;
    // 等待 DOM 更新后再回顶，避免被后续渲染覆盖滚动位置。
    requestAnimationFrame(() => {
      container.scrollTo({ top: 0, behavior: 'auto' });
      requestAnimationFrame(() => {
        container.scrollTo({ top: 0, behavior: 'auto' });
      });
    });
  }, [isSuccess, latestResult, resultSnapshotKey]);

  useEffect(() => {
    if (!previewModal) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPreviewModal(null);
        setPreviewLoadFailed(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [previewModal]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="panel-header">
        <h2 className="panel-title">
          <ImageIcon size={20} color="var(--accent-color)" /> 生成结果预览
        </h2>
        {isSuccess && (
          <button className="btn btn-secondary btn-icon" title="一键复制文案" onClick={copyToClipboard}>
            <Copy size={16} />
          </button>
        )}
      </div>

      <div className="panel-content" ref={contentRef}>
        {isSuccess && (
          <div
            style={{
              position: 'sticky',
              top: 0,
              zIndex: 3,
              marginTop: '-8px',
              padding: '10px 12px',
              borderRadius: '8px',
              border: '1px solid var(--border-color)',
              background: 'rgba(18, 18, 20, 0.96)',
              backdropFilter: 'blur(4px)',
            }}
          >
            {hasCopy ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '0.82rem', color: 'var(--accent-color)', marginBottom: '2px' }}>文案已生成</div>
                  <div
                    style={{
                      fontSize: '0.86rem',
                      color: 'var(--text-secondary)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    title={latestResult?.copy?.title || ''}
                  >
                    {latestResult?.copy?.title || '点击右侧按钮查看完整文案'}
                  </div>
                </div>
                <button className="btn btn-secondary" style={{ padding: '6px 10px', flexShrink: 0 }} onClick={scrollToCopySection}>
                  查看完整文案
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '0.86rem', color: copyFailureReason ? 'var(--error)' : 'var(--text-secondary)' }}>
                {copyFailureReason ? `文案生成异常：${copyFailureReason}` : '文案尚未返回，正在同步结果。'}
              </div>
            )}
          </div>
        )}

        {!isRunning && !isSuccess && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
            暂无结果，请调整配置后点击生成
          </div>
        )}

        {isRunning && (
          <>
            <div className="result-grid">
              <div className="result-image-box">
                <div className="result-skeleton"></div>
              </div>
              <div className="result-image-box">
                <div className="result-skeleton" style={{ animationDelay: '0.2s' }}></div>
              </div>
              <div className="result-image-box">
                <div className="result-skeleton" style={{ animationDelay: '0.4s' }}></div>
              </div>
              <div className="result-image-box">
                <div className="result-skeleton" style={{ animationDelay: '0.6s' }}></div>
              </div>
            </div>
            
            <div style={{ marginTop: '16px' }}>
              <div style={{ height: '24px', width: '60%', borderRadius: '4px', marginBottom: '12px' }} className="result-skeleton"></div>
              <div style={{ height: '16px', width: '100%', borderRadius: '4px', marginBottom: '8px' }} className="result-skeleton"></div>
              <div style={{ height: '16px', width: '80%', borderRadius: '4px', marginBottom: '8px' }} className="result-skeleton"></div>
            </div>
          </>
        )}

        {isSuccess && latestResult && (
          <>
            {isPartialSuccess && (
              <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '8px', backgroundColor: 'rgba(234, 179, 8, 0.1)', border: '1px solid rgba(234, 179, 8, 0.2)', color: 'var(--text-primary)', fontSize: '0.9rem' }}>
                <strong style={{ color: 'var(--accent-color)' }}>部分成功：</strong>
                已生成 {latestResult.images?.length || 0}/{draft?.image_count ?? '-'} 张图。上游失败导致部分缺失。
              </div>
            )}

            <div className="copy-section" style={{ marginBottom: '16px' }} ref={copySectionRef}>
                <button className="copy-section-header" onClick={() => setCopyExpanded(!copyExpanded)}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <FileText size={16} color="var(--accent-color)" />
                    <h3 style={{ margin: 0, fontSize: '1rem', color: 'var(--text-primary)' }}>
                      {latestResult.copy?.title || '文案结果'}
                    </h3>
                  </div>
                  {copyExpanded ? <ChevronUp size={16} color="var(--text-secondary)" /> : <ChevronDown size={16} color="var(--text-secondary)" />}
                </button>

                {copyExpanded && (
                  <div className="copy-section-body">
                    {hasCopy && latestResult.copy?.intro && (
                      <div className="copy-block">
                        <span className="copy-label">导语</span>
                        <p className="copy-paragraph">{latestResult.copy.intro}</p>
                      </div>
                    )}

                    {hasCopy && hasSections && latestResult.copy?.guide_sections?.map((sec, i) => (
                      <div key={i} className="copy-block">
                        <span className="copy-label">{sec.heading}</span>
                        <p className="copy-paragraph">{sec.content}</p>
                      </div>
                    ))}

                    {hasCopy && latestResult.copy?.ending && (
                      <div className="copy-block" style={{ borderLeft: '3px solid var(--accent-color)', paddingLeft: '12px' }}>
                        <span className="copy-label">结语</span>
                        <p className="copy-paragraph" style={{ fontStyle: 'italic', opacity: 0.9 }}>{latestResult.copy.ending}</p>
                      </div>
                    )}

                    {!hasCopy && (
                      <div style={{ fontSize: '0.9rem', lineHeight: 1.6 }}>
                        {copyFailureReason ? (
                          <div style={{ color: 'var(--error)' }}>
                            文案生成失败：{copyFailureReason}。当前已保留图片结果，可先使用图片。
                          </div>
                        ) : (
                          <div style={{ color: 'var(--text-muted)' }}>
                            文案暂未生成，请稍后重试或重新发起任务。
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>

            {latestAssetBreakdown && (
              <div className="asset-breakdown" style={{ marginBottom: '16px', padding: '14px', backgroundColor: 'var(--bg-glass-hover)', borderRadius: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', color: 'var(--text-primary)' }}>
                   <Component size={16} color="var(--accent-color)" />
                   <h3 style={{ margin: 0, fontSize: '1rem' }}>素材拆解结果</h3>
                </div>
                {(!latestAssetBreakdown.extracted?.foods?.length && !latestAssetBreakdown.extracted?.scenes?.length && !latestAssetBreakdown.extracted?.keywords?.length) ? (
                  <div style={{ color: 'var(--text-muted)' }}>暂无拆解结果</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {(latestAssetBreakdown.extracted?.foods?.length ?? 0) > 0 && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                        <strong style={{ color: 'var(--text-secondary)', minWidth: '50px', fontSize: '0.85rem' }}>美食:</strong>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                           {latestAssetBreakdown.extracted?.foods?.map((food, i) => (
                             <span key={i} className="tag-badge">{food}</span>
                           ))}
                        </div>
                      </div>
                    )}
                    {(latestAssetBreakdown.extracted?.scenes?.length ?? 0) > 0 && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                        <strong style={{ color: 'var(--text-secondary)', minWidth: '50px', fontSize: '0.85rem' }}>场景:</strong>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                           {latestAssetBreakdown.extracted?.scenes?.map((scene, i) => (
                             <span key={i} className="tag-badge">{scene}</span>
                           ))}
                        </div>
                      </div>
                    )}
                    {(latestAssetBreakdown.extracted?.keywords?.length ?? 0) > 0 && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                        <strong style={{ color: 'var(--text-secondary)', minWidth: '50px', fontSize: '0.85rem' }}>关键词:</strong>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                           {latestAssetBreakdown.extracted?.keywords?.map((kw, i) => (
                             <span key={i} className="tag-badge">{kw}</span>
                           ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="result-grid" style={{ marginBottom: '16px' }}>
              {latestResult.images?.map((img, i) => (
                <ResultImage
                  key={`${img.image_url}-${img.image_index ?? i}`}
                  img={img}
                  onPreview={(url, title) => {
                    setPreviewLoadFailed(false);
                    setPreviewModal({ url, title });
                  }}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {previewModal && (
        <div
          className="modal-overlay"
          style={{ zIndex: 130 }}
          onClick={() => {
            setPreviewModal(null);
            setPreviewLoadFailed(false);
          }}
        >
          <div
            className="modal-content"
            style={{ maxWidth: 'min(92vw, 1200px)', maxHeight: '92vh', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}
            onClick={(event) => event.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
              <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {previewModal.title || '图片预览'}
              </div>
              <button
                className="btn btn-secondary"
                style={{ padding: '4px 10px' }}
                onClick={() => {
                  setPreviewModal(null);
                  setPreviewLoadFailed(false);
                }}
              >
                关闭
              </button>
            </div>
            <div style={{ flex: 1, minHeight: '280px', maxHeight: 'calc(92vh - 90px)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.28)', borderRadius: '8px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
              {previewLoadFailed ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>大图加载失败，请重试。</div>
              ) : (
                <img
                  src={previewModal.url}
                  alt={previewModal.title || '图片预览'}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                  onError={() => setPreviewLoadFailed(true)}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
