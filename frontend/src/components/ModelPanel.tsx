import { useState, useEffect } from 'react';
import { X, Server, Cpu, Plus, Trash2, Loader2, Save, AlertTriangle } from 'lucide-react';
import * as api from '../api';
import { useAppStore } from '../store';

interface ModelPanelProps {
  onClose: () => void;
}

export default function ModelPanel({ onClose }: ModelPanelProps) {
  const [providers, setProviders] = useState<api.Provider[]>([]);
  const [textModels, setTextModels] = useState<api.ModelItem[]>([]);
  const [imageModels, setImageModels] = useState<api.ModelItem[]>([]);

  const [isLoading, setIsLoading] = useState(true);
  const { addToast } = useAppStore();
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  
  const [textModelInput, setTextModelInput] = useState('');
  const [textProviderId, setTextProviderId] = useState('');
  
  const [imageModelInput, setImageModelInput] = useState('');
  const [imageProviderId, setImageProviderId] = useState('');

  const [isFetchingTextModels, setIsFetchingTextModels] = useState(false);
  const [isFetchingImageModels, setIsFetchingImageModels] = useState(false);

  const [showAddProvider, setShowAddProvider] = useState(false);
  const [newProvider, setNewProvider] = useState({ name: '', base_url: '', api_key: '', api_protocol: 'responses' as 'responses' | 'chat_completions' });

  const fetchModelsSafe = async (providerId: string, capability: string) => {
    if (!providerId) return [];
    try {
      const res = await api.getModels(providerId);
      return res.items.filter(m => m.capabilities.includes(capability));
    } catch (err: unknown) {
      const e = err as { response?: { data?: { message?: string } } };
      const msg = e?.response?.data?.message || '模型列表拉取失败';
      addToast(msg, 'error');
      return [];
    }
  };

  const handleTextProviderChange = async (providerId: string) => {
    const prevModel = textModelInput;
    const prevModels = textModels;
    setTextProviderId(providerId);
    setTextModels([]);
    setTextModelInput('');
    setIsFetchingTextModels(true);
    const models = await fetchModelsSafe(providerId, 'text_generation');
    if (models.length > 0) {
      setTextModels(models);
      setTextModelInput(models[0].name);
    } else {
      setTextModels(prevModels);
      setTextModelInput(prevModel);
    }
    setIsFetchingTextModels(false);
  };

  const handleImageProviderChange = async (providerId: string) => {
    const prevModel = imageModelInput;
    const prevModels = imageModels;
    setImageProviderId(providerId);
    setImageModels([]);
    setImageModelInput('');
    setIsFetchingImageModels(true);
    const models = await fetchModelsSafe(providerId, 'image_generation');
    if (models.length > 0) {
      setImageModels(models);
      setImageModelInput(models[0].name);
    } else {
      setImageModels(prevModels);
      setImageModelInput(prevModel);
    }
    setIsFetchingImageModels(false);
  };

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const [provRes, routingRes] = await Promise.all([
        api.getProviders(),
        api.getModelRouting().catch(() => null)
      ]);
      setProviders(provRes.items || []);
      
      let initialTextProviderId = '';
      let initialImageProviderId = '';
      let initialTextModel = '';
      let initialImageModel = '';

      if (routingRes) {
        initialTextProviderId = routingRes.text_model.provider_id;
        initialTextModel = routingRes.text_model.model_name;
        initialImageProviderId = routingRes.image_model.provider_id;
        initialImageModel = routingRes.image_model.model_name;
      } else if (provRes.items.length > 0) {
        initialTextProviderId = provRes.items[0].id;
        initialImageProviderId = provRes.items[0].id;
      }

      setTextProviderId(initialTextProviderId);
      setImageProviderId(initialImageProviderId);

      if (initialTextProviderId) {
        setIsFetchingTextModels(true);
        const models = await fetchModelsSafe(initialTextProviderId, 'text_generation');
        if (models.length > 0) {
          setTextModels(models);
          if (!models.some(m => m.name === initialTextModel)) {
            initialTextModel = models[0].name;
          }
        } else if (initialTextModel) {
          setTextModels([{ id: initialTextModel, name: initialTextModel, capabilities: ['text_generation'] }]);
        }
        setIsFetchingTextModels(false);
      }
      if (initialImageProviderId) {
        setIsFetchingImageModels(true);
        const models = await fetchModelsSafe(initialImageProviderId, 'image_generation');
        if (models.length > 0) {
          setImageModels(models);
          if (!models.some(m => m.name === initialImageModel)) {
            initialImageModel = models[0].name;
          }
        } else if (initialImageModel) {
          setImageModels([{ id: initialImageModel, name: initialImageModel, capabilities: ['image_generation'] }]);
        }
        setIsFetchingImageModels(false);
      }

      setTextModelInput(initialTextModel);
      setImageModelInput(initialImageModel);

    } catch {
      addToast('加载模型配置失败', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDeleteProvider = async (id: string) => {
    try {
      await api.deleteProvider(id);
      addToast('已删除提供商', 'success');
      setConfirmDeleteId(null);
      fetchData();
    } catch {
      addToast('删除失败', 'error');
    }
  };

  const handleAddProvider = async () => {
    if (!newProvider.name || !newProvider.base_url || !newProvider.api_key) {
      addToast('请填写完整的提供商信息', 'error');
      return;
    }
    try {
      await api.addProvider(newProvider);
      addToast('提供商添加成功', 'success');
      setShowAddProvider(false);
      setNewProvider({ name: '', base_url: '', api_key: '', api_protocol: 'responses' });
      fetchData();
    } catch {
      addToast('添加提供商失败', 'error');
    }
  };

  const handleSaveRouting = async () => {
    if (!textProviderId || !imageProviderId || !textModelInput || !imageModelInput) {
      addToast('请完整填写路由配置', 'error');
      return;
    }

    if (!textModels.some(m => m.name === textModelInput) || !imageModels.some(m => m.name === imageModelInput)) {
      addToast('请选择有效的模型', 'error');
      return;
    }
    try {
      await api.updateModelRouting({
        image_model: { provider_id: imageProviderId, model_name: imageModelInput },
        text_model: { provider_id: textProviderId, model_name: textModelInput }
      });
      addToast('路由配置已保存', 'success');
      onClose();
    } catch (err: unknown) {
      const e = err as { response?: { data?: { message?: string } } };
      const msg = e?.response?.data?.message || '保存失败';
      addToast(`保存失败: ${msg}`, 'error');
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h2 className="panel-title">模型设置</h2>
          <button className="btn btn-icon" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="panel-content">
          {isLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '40px' }}><Loader2 className="animate-spin" /></div>
          ) : (
            <>
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Server size={18} color="var(--accent-color)" /> 提供商管理 (Provider)
                </h3>
                
                {providers.length === 0 ? (
                   <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>暂无提供商配置</div>
                ) : (
                  providers.map(p => (
                    <div key={p.id} className="card" style={{ marginBottom: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <div style={{ fontWeight: 500 }}>{p.name}</div>
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{p.base_url}</div>
                      </div>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <span className={`status-badge ${p.enabled ? 'success' : 'failed'}`}>{p.enabled ? '已启用' : '已禁用'}</span>
                        <button className="btn btn-icon" style={{ padding: '4px' }} onClick={() => setConfirmDeleteId(p.id)}><Trash2 size={16} color="var(--error)" /></button>
                      </div>
                    </div>
                  ))
                )}

                <button className="btn btn-secondary" style={{ width: '100%', marginTop: '8px', borderStyle: 'dashed' }} onClick={() => setShowAddProvider(true)}>
                  <Plus size={16} /> 添加提供商
                </button>
              </div>

              <div>
                <h3 style={{ fontSize: '1rem', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Cpu size={18} color="var(--accent-color)" /> 默认路由设置
                </h3>
                <div style={{ fontSize: '0.85rem', color: 'var(--warning)', marginBottom: '16px', background: 'rgba(245,158,11,0.1)', padding: '8px 12px', borderRadius: '4px' }}>
                  文字模型将同时用于提示词生成与文案生成。
                </div>

                <div className="input-group" style={{ marginBottom: '16px' }}>
                  <label className="input-label">📝 文字模型 (Text Model)</label>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <select className="input" style={{ width: '40%' }} value={textProviderId} onChange={e => handleTextProviderChange(e.target.value)}>
                      <option value="" disabled>选择提供商...</option>
                      {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select className="input" style={{ flex: 1 }} value={textModelInput} onChange={e => setTextModelInput(e.target.value)}>
                      <option value="" disabled>{isFetchingTextModels ? '加载中...' : (textModels.length ? '选择模型...' : '暂无可用模型')}</option>
                      {textModels.map(m => <option key={m.id} value={m.name}>{m.name}</option>)}
                    </select>
                  </div>
                </div>

                <div className="input-group">
                  <label className="input-label">🎨 生图模型 (Image Model)</label>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <select className="input" style={{ width: '40%' }} value={imageProviderId} onChange={e => handleImageProviderChange(e.target.value)}>
                      <option value="" disabled>选择提供商...</option>
                      {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select className="input" style={{ flex: 1 }} value={imageModelInput} onChange={e => setImageModelInput(e.target.value)}>
                       <option value="" disabled>{isFetchingImageModels ? '加载中...' : (imageModels.length ? '选择模型...' : '暂无可用模型')}</option>
                       {imageModels.map(m => <option key={m.id} value={m.name}>{m.name}</option>)}
                    </select>
                  </div>
                </div>
              </div>
              
              <div style={{ marginTop: '24px', display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
                <button className="btn btn-ghost" onClick={onClose}>取消</button>
                <button className="btn btn-primary" onClick={handleSaveRouting}><Save size={16} /> 保存设置</button>
              </div>
            </>
          )}
        </div>
      </div>
      
      {confirmDeleteId && (
        <div className="modal-overlay" onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null); }}>
          <div className="modal-content" style={{ maxWidth: '400px', padding: '24px', textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '16px' }}>
               <AlertTriangle size={48} color="var(--error)" />
            </div>
            <h3 style={{ marginBottom: '8px' }}>确定删除提供商？</h3>
            <p style={{ color: 'var(--text-muted)', marginBottom: '24px', fontSize: '0.9rem' }}>
              此操作不可恢复，确定要继续吗？
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
              <button className="btn btn-secondary" onClick={() => setConfirmDeleteId(null)}>取消操作</button>
              <button className="btn btn-primary" style={{ background: 'var(--error)' }} onClick={() => handleDeleteProvider(confirmDeleteId)}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
      {showAddProvider && (
        <div className="modal-overlay" onClick={(e) => { e.stopPropagation(); setShowAddProvider(false); }}>
          <div className="modal-content" style={{ maxWidth: 'clamp(520px, 60vw, 720px)', padding: '32px' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginBottom: '24px', fontSize: '1.25rem' }}>添加提供商</h3>
            <div className="input-group" style={{ marginBottom: '20px' }}>
              <label className="input-label" style={{ marginBottom: '8px' }}>配置名称</label>
              <input type="text" spellCheck={false} className="input" placeholder="例如: OpenAI 官方" value={newProvider.name} onChange={e => setNewProvider({ ...newProvider, name: e.target.value })} />
            </div>
            <div className="input-group" style={{ marginBottom: '20px' }}>
              <label className="input-label" style={{ marginBottom: '8px' }}>Base URL</label>
              <input type="text" spellCheck={false} className="input" placeholder="https://api.openai.com/v1" value={newProvider.base_url} onChange={e => setNewProvider({ ...newProvider, base_url: e.target.value })} />
            </div>
            <div className="input-group" style={{ marginBottom: '20px' }}>
              <label className="input-label" style={{ marginBottom: '8px' }}>API Key</label>
              <input type="password" className="input" placeholder="sk-..." value={newProvider.api_key} onChange={e => setNewProvider({ ...newProvider, api_key: e.target.value })} />
            </div>
            <div className="input-group" style={{ marginBottom: '32px' }}>
              <label className="input-label" style={{ marginBottom: '8px' }}>API 协议</label>
              <select className="input" value={newProvider.api_protocol} onChange={e => setNewProvider({ ...newProvider, api_protocol: e.target.value as 'responses' | 'chat_completions' })}>
                <option value="responses">Responses 协议</option>
                <option value="chat_completions">Chat Completions 协议</option>
              </select>
            </div>
            <div style={{ display: 'flex', gap: '16px', justifyContent: 'flex-end', marginTop: '16px' }}>
              <button className="btn btn-ghost" onClick={() => setShowAddProvider(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleAddProvider}>确认添加</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
