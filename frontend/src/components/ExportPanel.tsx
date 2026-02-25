import { useEffect, useRef, useState } from 'react';
import { File, Image as ImageIcon, Loader2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';

export default function ExportPanel() {
  const { activeSessionId, latestJob, addToast } = useAppStore();
  const [isExporting, setIsExporting] = useState(false);
  const [exportType, setExportType] = useState<'long_image' | 'pdf' | null>(null);
  const exportPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearExportPoll = () => {
    if (exportPollRef.current) {
      clearInterval(exportPollRef.current);
      exportPollRef.current = null;
    }
  };

  useEffect(() => {
    return () => {
      clearExportPoll();
    };
  }, []);

  const canExport = activeSessionId && latestJob && (latestJob.status === 'success' || latestJob.status === 'partial_success');

  const handleExport = async (format: 'long_image' | 'pdf') => {
    if (!canExport || !activeSessionId || !latestJob) return;

    setIsExporting(true);
    setExportType(format);
    clearExportPoll();

    try {
      const task = await api.createExport({
        session_id: activeSessionId,
        job_id: latestJob.id,
        export_format: format
      });

      exportPollRef.current = setInterval(async () => {
        try {
          const exportTask = await api.getExport(task.id);
          if (exportTask.status === 'success') {
            setIsExporting(false);
            setExportType(null);
            clearExportPoll();
            if (exportTask.file_url) {
              window.open(exportTask.file_url, '_blank');
              addToast('导出文件已打开', 'success');
            } else {
              addToast('导出成功，但未返回文件链接', 'success');
            }
          } else if (exportTask.status === 'failed') {
            setIsExporting(false);
            setExportType(null);
            clearExportPoll();
            addToast(`导出失败: ${exportTask.error_message || '未知错误'}`, 'error');
          }
        } catch {
          setIsExporting(false);
          setExportType(null);
          clearExportPoll();
          addToast('获取状态失败', 'error');
        }
      }, 2000);
    } catch {
      setIsExporting(false);
      setExportType(null);
      clearExportPoll();
      addToast('创建任务失败', 'error');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div className="panel-header" style={{ padding: '0 0 12px 0', borderBottom: 'none' }}>
        <h2 className="panel-title">导出与分享</h2>
      </div>

      <div style={{ display: 'flex', gap: '12px' }}>
        <button 
          className="btn btn-secondary" 
          style={{ flex: 1, padding: '12px' }}
          disabled={!canExport || isExporting}
          onClick={() => handleExport('long_image')}
        >
          {isExporting && exportType === 'long_image' ? <Loader2 size={18} className="animate-spin" /> : <ImageIcon size={18} />}
          长图导出
        </button>
        <button 
          className="btn btn-secondary" 
          style={{ flex: 1, padding: '12px' }}
          disabled={!canExport || isExporting}
          onClick={() => handleExport('pdf')}
        >
          {isExporting && exportType === 'pdf' ? <Loader2 size={18} className="animate-spin" /> : <File size={18} />}
          PDF 导出
        </button>
      </div>

      <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', textAlign: 'center', marginTop: '4px' }}>
        提示：请确保已有一组成功的生成结果
      </div>
    </div>
  );
}
