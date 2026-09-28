import { isChatResponse, isHttpsUrl, isStudentQuestion } from '../types/api';
import type { ChatResponse, StudentQuestion } from '../types/api';

export interface RuntimeConfig { apiBaseUrl: string; timeoutMs: number }
export type ServiceErrorCode = 'invalid_request' | 'rate_limited' | 'unavailable' | 'invalid_response' | 'timeout' | 'cancelled' | 'configuration';
const messages: Record<ServiceErrorCode, string> = {
  invalid_request: 'Please check your question and context.',
  rate_limited: 'Too many requests. Please try again shortly.',
  unavailable: 'The service is unavailable. Please try again later.',
  invalid_response: 'The service could not provide a verified response. Please try again later.',
  timeout: 'The request took too long. Please try again.',
  cancelled: 'The request was cancelled.',
  configuration: 'The service configuration is unavailable.',
};
export class ServiceError extends Error {
  constructor(public readonly code: ServiceErrorCode) { super(messages[code]); this.name = 'ServiceError'; }
}
function validateConfig(value: unknown): RuntimeConfig {
  if (!value || typeof value !== 'object') throw new ServiceError('configuration');
  const config = value as Record<string, unknown>;
  const base = config.apiBaseUrl;
  if (typeof base !== 'string' || /[?#\\\s]/u.test(base) ||
      !(base.startsWith('/') && !base.startsWith('//') || isHttpsUrl(base)) ||
      typeof config.timeoutMs !== 'number' || !Number.isInteger(config.timeoutMs) || config.timeoutMs < 1 || config.timeoutMs > 120000)
    throw new ServiceError('configuration');
  return { apiBaseUrl: base.replace(/\/+$/u, ''), timeoutMs: config.timeoutMs };
}
export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/runtime-config.json', { cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal });
    if (!response.ok) throw new ServiceError('configuration');
    return validateConfig(await response.json());
  } catch { throw new ServiceError('configuration'); }
  finally { clearTimeout(timer); }
}
export function createApiClient(configuration: RuntimeConfig = { apiBaseUrl: '/api', timeoutMs: 12000 }) {
  const config = validateConfig(configuration);
  return {
    async ask(question: StudentQuestion, signal?: AbortSignal): Promise<ChatResponse> {
      if (!isStudentQuestion(question)) throw new ServiceError('invalid_request');
      if (signal?.aborted) throw new ServiceError('cancelled');
      const controller = new AbortController();
      const cancel = () => controller.abort();
      signal?.addEventListener('abort', cancel, { once: true });
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, config.timeoutMs);
      try {
        const response = await fetch(`${config.apiBaseUrl}/v1/chat/answers`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(question), cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal,
        });
        if (!response.ok) throw new ServiceError(response.status === 400 ? 'invalid_request' : response.status === 429 ? 'rate_limited' : 'unavailable');
        if (!response.headers.get('content-type')?.toLowerCase().startsWith('application/json')) throw new ServiceError('invalid_response');
        let payload: unknown;
        try { payload = await response.json(); }
        catch { throw new ServiceError('invalid_response'); }
        if (!isChatResponse(payload)) throw new ServiceError('invalid_response');
        return payload;
      } catch (error) {
        if (timedOut) throw new ServiceError('timeout');
        if (signal?.aborted) throw new ServiceError('cancelled');
        if (error instanceof ServiceError) throw error;
        throw new ServiceError('unavailable');
      } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
    },
  };
}
/** Load deployment configuration before constructing the client used by the UI. */
export async function loadApiClient() { return createApiClient(await loadRuntimeConfig()); }
