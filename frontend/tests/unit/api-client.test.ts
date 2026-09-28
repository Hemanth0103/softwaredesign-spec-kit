import { expect, test, vi } from 'vitest';
import { createApiClient, loadRuntimeConfig } from '../../src/api/client';

const outcomes = [
  { outcome: 'answer', answer: 'Policy', citations: [{ title: 'Policy', url: 'https://www.pnw.edu/policy' }], appliedContext: {} },
  { outcome: 'needs_context', question: 'Which campus?', requiredFields: ['campus'] },
  { outcome: 'referral', limitation: 'Cannot determine your record', officeName: 'Registrar', contactUrl: 'https://www.pnw.edu/registrar/' },
  { outcome: 'unresolved', limitation: 'Conflicting sources', officeName: 'Dean', contactUrl: 'https://www.pnw.edu/dean-of-students/' },
  { outcome: 'emergency', guidance: 'Call 911', contacts: [{ officeName: 'Public Safety', contactUrl: 'https://www.pnw.edu/public-safety/', phone: '911' }] },
];

test.each(outcomes)('accepts $outcome and posts privately', async (payload) => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  expect(await createApiClient().ask({ question: 'Policy?' })).toEqual(payload);
  expect(fetcher).toHaveBeenCalledWith('/api/v1/chat/answers', expect.objectContaining({
    method: 'POST', cache: 'no-store', credentials: 'omit', redirect: 'error', body: '{"question":"Policy?"}',
  }));
});

test.each([400, 429, 500])('does not expose %s response details', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private-secret', { status })));
  await expect(createApiClient().ask({ question: 'Hello' })).rejects.toMatchObject({
    code: status === 400 ? 'invalid_request' : status === 429 ? 'rate_limited' : 'unavailable',
  });
});

test.each([
  { outcome: 'answer', answer: 'x', citations: [], appliedContext: {} },
  { outcome: 'needs_context', question: 'x', requiredFields: ['identity'] },
  { ...outcomes[2], contactUrl: 'javascript:alert(1)' },
  { outcome: 'emergency', guidance: 'x', contacts: [] },
  { outcome: 'unknown' },
])('rejects malformed outcomes', async (payload) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { headers: { 'content-type': 'application/json' } })));
  await expect(createApiClient().ask({ question: 'Hello' })).rejects.toMatchObject({ code: 'invalid_response' });
});

test('loads runtime configuration without caching', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{"apiBaseUrl":"https://api.example.edu/api","timeoutMs":5000}'));
  vi.stubGlobal('fetch', fetcher);
  expect((await loadRuntimeConfig()).timeoutMs).toBe(5000);
  expect(fetcher).toHaveBeenCalledWith('/runtime-config.json', expect.objectContaining({ cache: 'no-store' }));
});

test('rejects invalid input without sending it', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  await expect(createApiClient().ask({ question: 'x'.repeat(4001) })).rejects.toMatchObject({ code: 'invalid_request' });
  expect(fetcher).not.toHaveBeenCalled();
});

test.each(['//evil.example/api', 'http://remote.example/api', 'https://user:pass@api.example/api', '/api?token=secret'])('rejects unsafe runtime base %s', (apiBaseUrl) => {
  expect(() => createApiClient({ apiBaseUrl, timeoutMs: 1000 })).toThrow('configuration');
});

test('redacts network errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('private-question-secret')));
  await expect(createApiClient().ask({ question: 'Hello' })).rejects.toMatchObject({ code: 'unavailable', message: 'The service is unavailable. Please try again later.' });
});

test('rejects broken JSON', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{bad', { headers: { 'content-type': 'application/json' } })));
  await expect(createApiClient().ask({ question: 'Hello' })).rejects.toMatchObject({ code: 'invalid_response' });
});

test('aborts timed out requests', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new Error('aborted')));
  })));
  const request = createApiClient({ apiBaseUrl: '/api', timeoutMs: 50 }).ask({ question: 'Hello' });
  const assertion = expect(request).rejects.toMatchObject({ code: 'timeout' });
  await vi.advanceTimersByTimeAsync(51);
  await assertion;
});

test('supports cancellation without a network request', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const controller = new AbortController(); controller.abort();
  await expect(createApiClient().ask({ question: 'Hello' }, controller.signal)).rejects.toMatchObject({ code: 'cancelled' });
  expect(fetcher).not.toHaveBeenCalled();
});
