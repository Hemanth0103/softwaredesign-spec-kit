import { expect, test } from '@playwright/test';

test('Vite serves the React entry point without browser errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'PNW Student Information Chatbot',
  );
  await expect(page).toHaveTitle('PNW Student Information Chatbot');
  expect(errors).toEqual([]);
});
