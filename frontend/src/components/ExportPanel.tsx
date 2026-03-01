import { useEffect, useRef, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';

type SavePickerFn = (options?: {
  suggestedName?: string;
  types?: Array<{
    description?: string;
    accept: Record<string, string[]>;
  }>;
}) => Promise<{
  createWritable: () => Promise<{
    write: (data: Blob) => Promise<void>;
    close: () => Promise<void>;
  }>;
}>;

export default function ExportPanel() {
  const { activeSessionId, latestJob, addToast } = useAppStore();
  const [isExporting, setIsExporting] = useState(false);
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

  const resolveFileUrl = (fileUrl: string): string => {
    if (fileUrl.startsWith('http://') || fileUrl.startsWith('https://')) {
      return fileUrl;
    }
    const normalized = fileUrl.replace(/\\/g, '/').replace(/^\/+/, '');
    if (normalized.startsWith('static/')) {
      return `${api.STATIC_BASE_URL}/${normalized}`;
    }
    return `${api.STATIC_BASE_URL}/static/${normalized}`;
  };

  const downloadPdfFile = async (fileUrl: string, exportId: string) => {
    const downloadUrl = resolveFileUrl(fileUrl);
    const response = await fetch(downloadUrl);
    if (!response.ok) {
      throw new Error(`下载失败，状态码: ${response.status}`);
    }
    const blob = await response.blob();
    const fileName = `savory-canvas-${exportId}.pdf`;

    const win = window as Window & { showSaveFilePicker?: SavePickerFn };
    if (typeof win.showSaveFilePicker === 'function') {
      const handle = await win.showSaveFilePicker({
        suggestedName: fileName,
        types: [{ description: 'PDF 文件', accept: { 'application/pdf': ['.pdf'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return;
    }

    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(blobUrl);
  };

  const handleExport = async () => {
    if (!canExport || !activeSessionId || !latestJob) return;

    setIsExporting(true);
    clearExportPoll();

    try {
      const task = await api.createExport({
        session_id: activeSessionId,
        job_id: latestJob.id,
        export_format: 'pdf'
      });

      exportPollRef.current = setInterval(async () => {
        try {
          const exportTask = await api.getExport(task.id);
          if (exportTask.status === 'success') {
            clearExportPoll();
            if (exportTask.file_url) {
              await downloadPdfFile(exportTask.file_url, exportTask.id);
              addToast('PDF 导出成功', 'success');
            } else {
              addToast('导出成功，但未返回文件地址', 'error');
            }
            setIsExporting(false);
          } else if (exportTask.status === 'failed') {
            setIsExporting(false);
            clearExportPoll();
            addToast(`导出失败: ${exportTask.error_message || '未知错误'}`, 'error');
          }
        } catch {
          setIsExporting(false);
          clearExportPoll();
          addToast('导出文件下载失败', 'error');
        }
      }, 2000);
    } catch {
      setIsExporting(false);
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
          onClick={handleExport}
        >
          {isExporting ? <Loader2 size={18} className="animate-spin" /> : <Download size={18} />}
          导出 PDF
        </button>
      </div>

      <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', textAlign: 'center', marginTop: '4px' }}>
        提示：导出时将弹出保存位置选择窗口
      </div>
    </div>
  );
}
