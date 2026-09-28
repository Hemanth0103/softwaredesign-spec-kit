import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import App from '../../src/App';


test('React renders through the TypeScript and DOM test setup', () => {
  render(<App />);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(
    'PNW Student Information Chatbot',
  );
});
