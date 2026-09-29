/**
 * T020 browser UI tests. API replies are synthetic and intercepted, so this
 * suite proves browser behavior, not ingestion/grounding (T019/T032) or full
 * WCAG compliance (T046). Accessible names match chat.test.tsx's T031 contract.
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const question = 'Where is the fixture registration guidance?';
const answer = {
  outcome: 'answer', answer: 'Use the fixture registration instructions.',
  citations: [{ title: 'PNW Registrar fixture', url: 'https://www.pnw.edu/registrar/', contextLabel: 'Hammond — Fall 2026' }],
  appliedContext: { campus: 'hammond', academicTerm: 'Fall 2026' },
};
const submitName = /ask|send|submit/i;
async function submit(page: Page, text = question) {
  await page.getByRole('textbox', { name: /question/i }).fill(text);
  await page.getByRole('button', { name: submitName }).click();
}
async function tabTo(page: Page, role: 'textbox' | 'button', name: RegExp) {
  const target = page.getByRole(role, { name });
  await expect(target).toBeVisible();
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press('Tab');
    if (await target.evaluate(element => element === document.activeElement)) return;
  }
  await expect(target).toBeFocused();
}
test.beforeEach(async ({ page }) => {
  await page.route('**/runtime-config.json', route => route.fulfill({ json: { apiBaseUrl: '/api', timeoutMs: 12000 } }));
});

test('keyboard submission receives a cited, announced answer with applicable context', async ({ page }) => {
  const requests: unknown[] = [];
  await page.route('**/api/v1/chat/answers', route => {
    expect(route.request().method()).toBe('POST');
    requests.push(route.request().postDataJSON());
    return route.fulfill({ json: answer, headers: { 'Cache-Control': 'no-store' } });
  });
  await page.goto('/');
  await tabTo(page, 'textbox', /question/i);
  await page.keyboard.type(question);
  await tabTo(page, 'button', submitName);
  await page.keyboard.press('Enter');
  await expect(page.getByText(answer.answer, { exact: true })).toBeVisible();
  expect(requests).toEqual([{ question }]);
  await expect(page.getByRole('link', { name: answer.citations[0].title })).toHaveAttribute('href', answer.citations[0].url);
  await expect(page.getByText(/Hammond.*Fall 2026/).first()).toBeVisible();
  await expect(page.locator('[aria-live], [role="status"], [role="log"]').filter({ hasText: answer.answer }).first()).toBeVisible();
});

for (const status of [400, 429, 500]) {
  test(`HTTP ${status} is safe and recoverable`, async ({ page }) => {
    const messages: Record<number, string> = {
      400: 'Please check your question and context.',
      429: 'Too many requests. Please try again shortly.',
      500: 'The service is unavailable. Please try again later.',
    };
    let failed = false;
    await page.route('**/api/v1/chat/answers', route => {
      if (failed) return route.fulfill({ json: answer });
      failed = true;
      return route.fulfill({ status, json: { detail: 'PRIVATE_SERVER_DETAIL' } });
    });
    await page.goto('/');
    await submit(page);
    await expect(page.getByText(messages[status], { exact: true })).toBeVisible();
    await expect(page.getByText('PRIVATE_SERVER_DETAIL')).toHaveCount(0);
    await expect(page.getByText(answer.answer, { exact: true })).toHaveCount(0);
    await submit(page, 'Try a new fixture question');
    await expect(page.getByText(answer.answer, { exact: true })).toBeVisible();
  });
}

test('ending a session removes transcript, draft, citations and context across reload', async ({ page }) => {
  const requests: unknown[] = [];
  await page.route('**/api/v1/chat/answers', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ json: answer });
  });
  await page.goto('/');
  await submit(page);
  await expect(page.getByText(answer.answer, { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: /question/i }).fill('PRIVATE_UNSENT_DRAFT');
  await tabTo(page, 'button', /end session/i);
  await page.keyboard.press('Enter');
  for (let pass = 0; pass < 2; pass++) {
    await expect(page.getByRole('textbox', { name: /question/i })).toHaveValue('');
    await expect(page.getByText(answer.answer, { exact: true })).toHaveCount(0);
    await expect(page.getByText(question, { exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: answer.citations[0].title })).toHaveCount(0);
    await expect(page.getByText(/Fall 2026/)).toHaveCount(0);
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    for (const marker of [question, answer.answer, 'PRIVATE_UNSENT_DRAFT', 'Fall 2026']) expect(storage).not.toContain(marker);
    if (pass === 0) await page.reload();
  }
  await submit(page, 'Fresh session question');
  await expect(page.getByText(answer.answer, { exact: true })).toBeVisible();
  expect(requests.at(-1)).toEqual({ question: 'Fresh session question' });
});
