export function getErrorMessage(error: unknown): string {
  if (typeof error === 'string' && error.trim()) return error;
  if (error instanceof Error && error.message.trim()) return error.message;

  const maybeResponse = error as {
    response?: {
      data?: {
        message?: string;
        detail?: string;
      };
    };
  };
  const message = maybeResponse.response?.data?.message || maybeResponse.response?.data?.detail;
  if (typeof message === 'string' && message.trim()) return message;

  return '请求失败，请稍后重试。';
}
