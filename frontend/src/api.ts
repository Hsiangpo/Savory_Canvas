import axios from 'axios';

const api = axios.create({
  baseURL: 'http://127.0.0.1:8887/api/v1',
});

// --- Interfaces ---
export interface Session {
  id: string;
  title: string;
  content_mode: 'food' | 'scenic' | 'food_scenic';
  created_at: string;
  updated_at: string;
}

export interface ModelItem {
  id: string;
  name: string;
  capabilities: string[];
}

export interface ModelListResponse {
  provider_id: string;
  items: ModelItem[];
}

export interface Asset {
  id: string;
  session_id: string;
  asset_type: 'food_name' | 'scenic_name' | 'image' | 'text' | 'video' | 'transcript';
  content?: string;
  file_path?: string;
  status: 'ready' | 'processing' | 'failed';
  created_at: string;
}

export interface StyleOptionBlock {
  title: string;
  items: string[];
  max: number;
}

export interface StyleChatResponse {
  reply: string;
  options: StyleOptionBlock;
  stage: string;
  next_stage: string;
  is_finished: boolean;
  fallback_used: boolean;
}

export interface InspirationAttachment {
  id: string;
  asset_id?: string;
  type: 'image' | 'video' | 'text' | 'transcript';
  name?: string;
  preview_url?: string;
  status: 'ready' | 'processing' | 'failed';
  usage_type?: 'style_reference' | 'content_asset';
}

export interface StylePayload {
  painting_style: string;
  color_mood: string;
  prompt_example: string;
  style_prompt: string;
  sample_image_asset_id?: string | null;
  extra_keywords: string[];
}

export interface InspirationAssetCandidates {
  foods?: string[];
  scenes?: string[];
  keywords?: string[];
  source_asset_ids?: string[];
  confidence?: number;
}

export interface InspirationStyleContext {
  style_profile_id?: string;
  style_name?: string;
  sample_image_asset_id?: string;
  sample_image_preview_url?: string;
  style_payload?: StylePayload;
}

export interface InspirationMessage {
  id: string;
  role: 'assistant' | 'user' | 'system';
  content: string;
  options?: StyleOptionBlock | null;
  fallback_used?: boolean;
  attachments?: InspirationAttachment[];
  asset_candidates?: InspirationAssetCandidates;
  style_context?: InspirationStyleContext;
  created_at: string;
}

export interface InspirationDraft {
  stage: 'style_collecting' | 'prompt_revision' | 'asset_confirming' | 'locked';
  style_payload?: StylePayload | Record<string, unknown>;
  image_count?: number;
  draft_style_id?: string;
  locked: boolean;
}

export interface InspirationConversationResponse {
  session_id: string;
  messages: InspirationMessage[];
  draft: InspirationDraft;
}


export interface StyleProfile {
  id: string;
  session_id?: string;
  name: string;
  style_payload: StylePayload;
  sample_image_preview_url?: string;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface GenerationJob {
  id: string;
  session_id: string;
  style_profile_id?: string;
  image_count: number;
  status: 'queued' | 'running' | 'partial_success' | 'success' | 'failed' | 'canceled';
  progress_percent: number;
  current_stage: 'asset_extract' | 'asset_allocate' | 'prompt_generate' | 'image_generate' | 'copy_generate' | 'finalize';
  stage_message: string;
  error_code?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface ImageResult {
  image_index: number;
  asset_refs?: string[];
  prompt_text: string;
  image_url: string;
}

export interface CopySection {
  heading: string;
  content: string;
}

export interface CopyResult {
  title: string;
  intro: string;
  guide_sections: CopySection[];
  ending: string;
  full_text: string;
}

export interface GenerationResult {
  job_id: string;
  status: string;
  images: ImageResult[];
  copy: CopyResult;
}

export interface ExportTask {
  id: string;
  session_id: string;
  job_id: string;
  export_format: string;
  status: 'queued' | 'running' | 'success' | 'failed';
  file_url?: string;
  error_code?: string;
  error_message?: string;
  created_at: string;
}

export interface JobStage {
  stage: string;
  status: string;
  stage_message: string;
  created_at: string;
}

export interface JobStageListResponse {
  job_id: string;
  items: JobStage[];
}

export interface SourceAssetRef {
  asset_id: string;
  asset_type: string;
  content: string;
}

export interface ExtractedAssets {
  foods?: string[];
  scenes?: string[];
  keywords?: string[];
}

export interface JobAssetBreakdownResponse {
  job_id: string;
  session_id: string;
  content_mode: string;
  source_assets: SourceAssetRef[];
  extracted: ExtractedAssets;
  created_at: string;
}

export interface Provider {
  id: string;
  name: string;
  base_url: string;
  api_key_masked?: string;
  api_protocol: 'responses' | 'chat_completions';
  enabled: boolean;
}

export interface ModelReference {
  provider_id: string;
  model_name: string;
}

export interface ModelRoutingConfig {
  image_model: ModelReference;
  text_model: ModelReference;
}

// --- API Calls ---

export const getSessions = () => api.get<{items: Session[]}>('/sessions').then(res => res.data);
export const createSession = (data: { title: string, content_mode: string }) => api.post<Session>('/sessions', data).then(res => res.data);
export const getSessionDetail = (id: string) => api.get<{session: Session, assets: Asset[], jobs: GenerationJob[], exports: ExportTask[]}>('/sessions/' + id).then(res => res.data);
export const updateSession = (id: string, data: { title: string }) => api.patch<Session>(`/sessions/${id}`, data).then(res => res.data);
export const deleteSession = (id: string) => api.delete(`/sessions/${id}`).then(res => res.data);

export const uploadVideoAsset = (sessionId: string, file: File) => {
  const formData = new FormData();
  formData.append('session_id', sessionId);
  formData.append('file', file);
  return api.post<Asset>('/assets/video', formData).then(res => res.data);
};

export const createTextAsset = (data: { session_id: string, asset_type: string, content: string }) => api.post<Asset>('/assets/text', data).then(res => res.data);

export const getTranscript = (assetId: string) => api.get(`/assets/${assetId}/transcript`).then(res => res.data);

export const styleChat = (data: { session_id: string, stage: string, user_reply: string, selected_items: string[] }) => api.post<StyleChatResponse>('/styles/chat', data).then(res => res.data);

export const getInspirationConversation = (sessionId: string) => api.get<InspirationConversationResponse>(`/inspirations/${sessionId}`).then(res => res.data);

export const postInspirationMessage = (data: {
  session_id: string,
  text?: string,
  selected_items?: string[],
  action?: string,
  image_usages?: ('style_reference' | 'content_asset')[],
  images?: File[],
  videos?: File[]
}) => {
  const formData = new FormData();
  formData.append('session_id', data.session_id);
  if (data.text) formData.append('text', data.text);
  if (data.action) formData.append('action', data.action);
  if (data.selected_items) {
    data.selected_items.forEach(i => formData.append('selected_items', i));
  }
  if (data.images) {
    data.images.forEach(i => formData.append('images', i));
  }
  if (data.image_usages) {
    data.image_usages.forEach(usage => formData.append('image_usages', usage));
  }
  if (data.videos) {
    data.videos.forEach(v => formData.append('videos', v));
  }
  return api.post<InspirationConversationResponse>('/inspirations/messages', formData).then(res => res.data);
};


export const createGenerationJob = (data: { session_id: string, style_profile_id: string, image_count: number }) => api.post<GenerationJob>('/jobs/generate', data).then(res => res.data);
export const getGenerationJob = (jobId: string) => api.get<GenerationJob>(`/jobs/${jobId}`).then(res => res.data);
export const getGenerationResult = (jobId: string) => api.get<GenerationResult>(`/jobs/${jobId}/results`).then(res => res.data);
export const getGenerationStages = (jobId: string) => api.get<JobStageListResponse>(`/jobs/${jobId}/stages`).then(res => res.data);
export const getGenerationAssetBreakdown = (jobId: string) => api.get<JobAssetBreakdownResponse>(`/jobs/${jobId}/asset-breakdown`).then(res => res.data);
export const cancelGenerationJob = (jobId: string) => api.post(`/jobs/${jobId}/cancel`).then(res => res.data);

export interface ModelRoutingConfigRequest {
  image_model: ModelReference;
  text_model: ModelReference;
}

export const getProviders = () => api.get<{items: Provider[]}>('/providers').then(res => res.data);
export const addProvider = (data: { name: string; base_url: string; api_key: string; api_protocol: 'responses' | 'chat_completions' }) => api.post<Provider>('/providers', data).then(res => res.data);
export const deleteProvider = (id: string) => api.delete(`/providers/${id}`).then(res => res.data);
export const getModels = (providerId: string) => api.get<ModelListResponse>(`/models?provider_id=${providerId}`).then(res => res.data);
export const getModelRouting = () => api.get<ModelRoutingConfig>('/config/model-routing').then(res => res.data);
export const updateModelRouting = (data: ModelRoutingConfigRequest) => api.post<ModelRoutingConfig>('/config/model-routing', data).then(res => res.data);

export const createExport = (data: { session_id: string, job_id: string, export_format: string }) => api.post<ExportTask>('/exports', data).then(res => res.data);
export const getExport = (exportId: string) => api.get<ExportTask>(`/exports/${exportId}`).then(res => res.data);
export const getStyleProfiles = () => api.get<{items: StyleProfile[]}>('/styles').then(res => res.data);
export const createStyleProfile = (data: { session_id?: string, name: string, style_payload: StylePayload }) => api.post<StyleProfile>('/styles', data).then(res => res.data);
export const updateStyleProfile = (id: string, data: { name?: string, style_payload?: StylePayload }) => api.patch<StyleProfile>(`/styles/${id}`, data).then(res => res.data);
export const deleteStyleProfile = (id: string) => api.delete<{deleted: boolean}>(`/styles/${id}`).then(res => res.data);

export default api;
