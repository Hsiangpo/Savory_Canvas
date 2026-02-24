import { useState } from 'react';
import { UploadCloud, FileVideo, Image as ImageIcon, Type, Loader2, CheckCircle2 } from 'lucide-react';
import { useAppStore } from '../store';
import * as api from '../api';

export default function AssetInputPanel() {
  const [activeTab, setActiveTab] = useState<'food' | 'text' | 'video'>('food');
  const { activeSessionId, addToast } = useAppStore();
  
  const [foodInput, setFoodInput] = useState('');
  const [textInput, setTextInput] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<'idle' | 'processing' | 'success' | 'failed'>('idle');

  const handleUploadVideo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !activeSessionId) return;

    setIsUploading(true);
    setUploadStatus('idle');
    try {
      const asset = await api.uploadVideoAsset(activeSessionId, file);
      setUploadStatus('processing');
      // Poll for transcript status
      const pollId = setInterval(async () => {
        try {
          const t = await api.getTranscript(asset.id);
          if (t.status === 'ready') {
            setUploadStatus('success');
            setIsUploading(false);
            addToast('转写成功', 'success');
            clearInterval(pollId);
          } else if (t.status === 'failed') {
            setUploadStatus('failed');
            setIsUploading(false);
            addToast('视频转写失败', 'error');
            clearInterval(pollId);
          }
        } catch {
          setUploadStatus('failed');
          setIsUploading(false);
          addToast('视频状态查询失败', 'error');
          clearInterval(pollId);
        }
      }, 2000);
    } catch {
      setUploadStatus('failed');
      addToast('上传视频失败', 'error');
      setIsUploading(false);
    }
  };

  const handleTextSubmit = async () => {
    if (!textInput.trim() || !activeSessionId) return;

    try {
      await api.createTextAsset({
        session_id: activeSessionId,
        asset_type: 'text',
        content: textInput
      });
      setTextInput('');
      addToast('文本提交成功', 'success');
    } catch {
      addToast('文本提交失败', 'error');
    }
  };

  const handleFoodSubmit = async () => {
    if (!foodInput.trim() || !activeSessionId) return;

    try {
      await api.createTextAsset({
        session_id: activeSessionId,
        asset_type: 'food_name',
        content: foodInput
      });
      setFoodInput('');
      addToast('食品名称提交成功', 'success');
    } catch {
      addToast('文本提交失败', 'error');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="panel-header">
        <h2 className="panel-title">素材区</h2>
      </div>
      
      <div className="tabs">
        <div className={`tab ${activeTab === 'food' ? 'active' : ''}`} onClick={() => setActiveTab('food')}>
          <ImageIcon size={16} style={{ display: 'inline', marginRight: '4px', verticalAlign: 'text-bottom' }}/> 食品输入
        </div>
        <div className={`tab ${activeTab === 'text' ? 'active' : ''}`} onClick={() => setActiveTab('text')}>
          <Type size={16} style={{ display: 'inline', marginRight: '4px', verticalAlign: 'text-bottom' }}/> 文本输入
        </div>
        <div className={`tab ${activeTab === 'video' ? 'active' : ''}`} onClick={() => setActiveTab('video')}>
          <FileVideo size={16} style={{ display: 'inline', marginRight: '4px', verticalAlign: 'text-bottom' }}/> 视频上传
        </div>
      </div>
      
      <div className="panel-content">
        {!activeSessionId && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', textAlign: 'center', margin: 'auto' }}>
            请先在左侧选择或创建一个会话
          </div>
        )}

        {activeSessionId && activeTab === 'video' && (
          <div className="upload-area" onClick={() => document.getElementById('video-upload')?.click()}>
            <UploadCloud className="upload-icon" />
            <div className="upload-text">点击或拖拽上传视频<br/><span style={{fontSize: '0.8rem', opacity: 0.6}}>支持 MP4, MOV (最大 500MB)</span></div>
            <input type="file" id="video-upload" style={{ display: 'none' }} accept="video/mp4,video/quicktime" onChange={handleUploadVideo} />
            <button className="btn btn-primary" style={{ marginTop: '8px' }} disabled={isUploading}>
              {isUploading ? <><Loader2 size={16} className="animate-spin"/> 上传中</> : '选择视频'}
            </button>
            {uploadStatus === 'processing' && (
              <div style={{ fontSize: '0.85rem', color: 'var(--warning)', marginTop: '8px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Loader2 size={14} className="animate-spin" /> 视频上传成功，转写处理中...
              </div>
            )}
            {uploadStatus === 'success' && (
              <div style={{ fontSize: '0.85rem', color: 'var(--success)', marginTop: '8px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <CheckCircle2 size={14} /> 视频上传并转写成功
              </div>
            )}
            {uploadStatus === 'failed' && (
              <div style={{ fontSize: '0.85rem', color: 'var(--error)', marginTop: '8px' }}>
                转写处理失败，请重试
              </div>
            )}
          </div>
        )}
        
        {activeSessionId && activeTab === 'food' && (
          <div className="input-group" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <input 
              type="text"
              className="input" 
              placeholder="输入核心食品名称 (如: 小米椒炒肉)..." 
              value={foodInput}
              onChange={(e) => setFoodInput(e.target.value)}
              style={{ padding: '12px', marginBottom: '16px' }}
            />
            <div className="upload-area" style={{ flex: 1, marginTop: 0 }}>
              <ImageIcon className="upload-icon" />
              <div className="upload-text">添加食品图片素材</div>
              <button className="btn btn-secondary" style={{ marginTop: '8px' }}>选择图片 (建设中)</button>
            </div>
            <button className="btn btn-primary" style={{ alignSelf: 'flex-end', marginTop: '12px' }} onClick={handleFoodSubmit}>
              提交食品名称
            </button>
          </div>
        )}

        {activeSessionId && activeTab === 'text' && (
          <div className="input-group" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <textarea 
              className="input" 
              placeholder="输入你想生成的美食描述或文案创意..." 
              style={{ flex: 1 }}
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
            ></textarea>
            <button className="btn btn-primary" style={{ alignSelf: 'flex-end', marginTop: '12px' }} onClick={handleTextSubmit}>
              提交文案
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
