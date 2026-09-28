import { render } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

describe('shared test cleanup', () => {
  test('a test can use DOM, storage and fake timers', () => {
    render(<div>Temporary fixture</div>);
    sessionStorage.setItem('conversation', 'test-only');
    localStorage.setItem('temporary', 'test-only');
    vi.useFakeTimers();
    expect(document.body.textContent).toContain('Temporary fixture');
  });

  test('the next test starts without the previous test state', () => {
    expect(document.body.textContent).not.toContain('Temporary fixture');
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
    expect(vi.isFakeTimers()).toBe(false);
  });
});
