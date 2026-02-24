import { create } from 'zustand';
import * as api from './api';

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'info';
  message: string;
}

interface AppState {
  activeSessionId: string | null;
  sessionList: api.Session[];
  latestJob: api.GenerationJob | null;
  latestResult: api.GenerationResult | null;
  latestStages: api.JobStage[];
  latestAssetBreakdown: api.JobAssetBreakdownResponse | null;
  isStartingJob: boolean;
  toasts: ToastMessage[];
  draft: api.InspirationDraft | null;
  styleProfileList: api.StyleProfile[];
  
  // Actions
  setActiveSessionId: (id: string | null) => void;
  fetchSessions: () => Promise<void>;
  createSession: (title: string, content_mode: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<boolean>;
  removeSession: (id: string) => Promise<void>;
  
  // Job Actions
  startJob: (styleProfileId: string, imageCount: number) => Promise<void>;
  pollJobStatus: () => Promise<void>;
  cancelJob: () => Promise<void>;
  fetchResult: () => Promise<void>;
  fetchStages: () => Promise<void>;
  fetchAssetBreakdown: () => Promise<void>;

  // UI Actions
  addToast: (message: string, type?: 'success' | 'error' | 'info') => void;
  removeToast: (id: string) => void;
  setDraft: (draft: api.InspirationDraft | null) => void;

  // Style Actions
  fetchStyles: () => Promise<void>;
  createStyle: (session_id: string | undefined, name: string, style_payload: api.StylePayload) => Promise<boolean>;
  updateStyle: (id: string, name?: string, style_payload?: api.StylePayload) => Promise<boolean>;
  deleteStyle: (id: string) => Promise<boolean>;
}

export const useAppStore = create<AppState>((set, get) => ({
  activeSessionId: null,
  sessionList: [],
  latestJob: null,
  latestResult: null,
  latestStages: [],
  latestAssetBreakdown: null,
  isStartingJob: false,
  toasts: [],
  draft: null,
  styleProfileList: [],

  fetchStyles: async () => {
    try {
      const res = await api.getStyleProfiles();
      set({ styleProfileList: res.items || [] });
    } catch (e) {
      console.error(e);
    }
  },

  createStyle: async (session_id, name, style_payload) => {
    try {
      await api.createStyleProfile({ session_id, name, style_payload });
      get().fetchStyles();
      get().addToast('风格已创建', 'success');
      return true;
    } catch (e) {
      console.error(e);
      get().addToast('创建风格失败', 'error');
      return false;
    }
  },

  updateStyle: async (id, name, style_payload) => {
    try {
      await api.updateStyleProfile(id, { name, style_payload });
      get().fetchStyles();
      get().addToast('风格已更新', 'success');
      return true;
    } catch (e) {
      console.error(e);
      get().addToast('更新风格失败', 'error');
      return false;
    }
  },

  deleteStyle: async (id) => {
    try {
      await api.deleteStyleProfile(id);
      get().fetchStyles();
      get().addToast('风格已删除', 'success');
      return true;
    } catch (e) {
      console.error(e);
      get().addToast('删除风格失败', 'error');
      return false;
    }
  },

  setDraft: (draft) => set({ draft }),

  addToast: (message, type = 'info') => {
    const id = Date.now().toString() + Math.random().toString();
    set(state => ({ toasts: [...state.toasts, { id, message, type }] }));
    setTimeout(() => {
      get().removeToast(id);
    }, 3000);
  },

  removeToast: (id) => {
    set(state => ({ toasts: state.toasts.filter(t => t.id !== id) }));
  },

  setActiveSessionId: async (id) => {
    set({ activeSessionId: id, latestJob: null, latestResult: null, latestStages: [], latestAssetBreakdown: null, draft: null });
    if (id) {
      try {
        const detail = await api.getSessionDetail(id);
        if (detail.jobs && detail.jobs.length > 0) {
          // Find the most recent job
          const lastJob = detail.jobs.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
          set({ latestJob: lastJob });
          get().fetchStages();
          get().fetchAssetBreakdown();
          if (lastJob.status === 'success' || lastJob.status === 'partial_success') {
            get().fetchResult();
          } else if (lastJob.status === 'queued' || lastJob.status === 'running') {
            // Need to start polling?
            // Optionally we can trigger poll, but pollJobStatus is typically called elsewhere or we can just call it here once, and rely on existing polling loops.
            get().pollJobStatus();
          }
        }
      } catch (e) {
        console.error(e);
      }
    }
  },

  fetchSessions: async () => {
    try {
      const data = await api.getSessions();
      set({ sessionList: data.items || [] });
      if (data.items?.length > 0 && !get().activeSessionId) {
        set({ activeSessionId: data.items[0].id });
      }
    } catch (e) {
      console.error(e);
    }
  },

  createSession: async (title, content_mode) => {
    try {
      const newSession = await api.createSession({ title, content_mode });
      set((state) => ({ 
        sessionList: [newSession, ...state.sessionList],
        activeSessionId: newSession.id,
        latestJob: null,
        latestResult: null,
        latestStages: [],
        latestAssetBreakdown: null,
        draft: null
      }));
    } catch (e) {
      console.error(e);
    }
  },

  renameSession: async (id, title) => {
    try {
      const updated = await api.updateSession(id, { title });
      set(state => ({
        sessionList: state.sessionList.map(s => s.id === id ? updated : s)
      }));
      get().addToast('重命名成功', 'success');
      return true;
    } catch (e) {
      console.error(e);
      get().addToast('重命名失败', 'error');
      return false;
    }
  },

  removeSession: async (id) => {
    try {
      await api.deleteSession(id);
      set(state => {
        const remaining = state.sessionList.filter(s => s.id !== id);
        return {
          sessionList: remaining,
          activeSessionId: state.activeSessionId === id ? (remaining.length > 0 ? remaining[0].id : null) : state.activeSessionId,
          ...(state.activeSessionId === id && { latestJob: null, latestResult: null, latestStages: [], latestAssetBreakdown: null, draft: null })
        };
      });
      get().addToast('会话已删除', 'success');
    } catch (e) {
      console.error(e);
      get().addToast('删除失败', 'error');
    }
  },

  startJob: async (styleProfileId, imageCount) => {
    const { activeSessionId, isStartingJob } = get();
    if (!activeSessionId) return;
    if (isStartingJob) return;

    set({ isStartingJob: true });
    try {
      const job = await api.createGenerationJob({
        session_id: activeSessionId,
        style_profile_id: styleProfileId,
        image_count: imageCount
      });
      set({ latestJob: job, latestResult: null, latestStages: [], latestAssetBreakdown: null });
    } catch (e) {
      console.error(e);
      const errResponse = (e as { response?: { data?: { message?: string, detail?: string } } }).response?.data;
      const errMsg = errResponse?.message || errResponse?.detail || '创建生成任务失败，请确保模型设置正确且有足够可用资产.';
      get().addToast(errMsg, 'error');
    } finally {
      set({ isStartingJob: false });
    }
  },

  pollJobStatus: async () => {
    const { latestJob } = get();
    if (!latestJob?.id) return;

    try {
      const job = await api.getGenerationJob(latestJob.id);
      set({ latestJob: job });

      get().fetchStages();
      get().fetchAssetBreakdown();

      if (job.status === 'success' || job.status === 'partial_success' || job.status === 'failed') {
        if (job.status === 'success' || job.status === 'partial_success') {
          get().fetchResult();
        }
      }
    } catch (e) {
      console.error(e);
    }
  },

  cancelJob: async () => {
    const { latestJob } = get();
    if (!latestJob?.id) return;

    try {
      await api.cancelGenerationJob(latestJob.id);
      get().pollJobStatus();
    } catch (e) {
      console.error(e);
    }
  },

  fetchResult: async () => {
    const { latestJob } = get();
    if (!latestJob?.id) return;

    try {
      const result = await api.getGenerationResult(latestJob.id);
      set({ latestResult: result });
    } catch (e) {
      console.error(e);
    }
  },

  fetchStages: async () => {
    const { latestJob } = get();
    if (!latestJob?.id) return;

    try {
      const response = await api.getGenerationStages(latestJob.id);
      set({ latestStages: response.items || [] });
    } catch (e) {
      console.error(e);
    }
  },

  fetchAssetBreakdown: async () => {
    const { latestJob } = get();
    if (!latestJob?.id) return;

    try {
      const breakdown = await api.getGenerationAssetBreakdown(latestJob.id);
      set({ latestAssetBreakdown: breakdown });
    } catch (e) {
      // 404 is normal if not yet generated
      if ((e as { response?: { status?: number } }).response?.status !== 404) {
        console.error(e);
      }
    }
  }
}));
