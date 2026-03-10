export function cleanErrorMessage(msg: string): string {
  if (!msg) return '模型服务暂时不可用，请稍后重试';
  
  const lowerMsg = msg.toLowerCase();
  
  // 1. 优先清洗明显的网关脏错误 / HTML
  const isDirtyHtml = 
    lowerMsg.includes('<!doctype html>') ||
    lowerMsg.includes('<html') ||
    lowerMsg.includes('cloudflare') ||
    lowerMsg.includes('bad gateway') ||
    lowerMsg.includes('502 bad gateway') ||
    lowerMsg.includes('503 service unavailable') ||
    lowerMsg.includes('504 gateway timeout') ||
    lowerMsg.includes('error code: 502');

  if (isDirtyHtml) {
    console.warn('上游返回脏错误(已清洗):', msg);
    return '模型服务响应异常，已同步最新会话状态';
  }
  
  // 2. 真实应用错误但文本过长时，进行 UI 降噪
  if (msg.length > 150) {
    console.error('服务返回长错误详情:', msg);
    // 截取前 80 个字符，防止超长 toast 破坏体验
    return `请求异常: ${msg.substring(0, 80)}...`;
  }
  
  return msg;
}

export function getErrorMessage(error: unknown): string {
  let msg = '请求失败，请稍后重试。';
  if (typeof error === 'string' && error.trim()) {
    msg = error;
  } else if (error instanceof Error && error.message.trim()) {
    msg = error.message;
  } else {
    const maybeResponse = error as {
      response?: {
        data?: {
          message?: string;
          detail?: string | unknown[];
        };
      };
    };
    const responseData = maybeResponse.response?.data;
    if (responseData) {
      if (typeof responseData.message === 'string' && responseData.message.trim()) {
        msg = responseData.message;
      } else if (typeof responseData.detail === 'string' && responseData.detail.trim()) {
        msg = responseData.detail;
      } else if (Array.isArray(responseData.detail)) {
        // Pydantic validation error format
        msg = JSON.stringify(responseData.detail);
      }
    }
  }

  return cleanErrorMessage(msg);
}
