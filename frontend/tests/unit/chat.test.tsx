/**
 * T020 / FR-001, FR-004, FR-014, FR-015. Render the public entry point so
 * these tests collect before T031 exists and later exercise its real state.
 * Proposed accessible UI contract: Question textbox, Ask/Send/Submit button,
 * End session button, and a live region containing results/errors. Context
 * controls, when shown, have Campus/Program/Course/Academic term labels.
 * Fetch is the only substituted boundary; the real T017 client validates replies.
 * Synthetic source content below is test data, not a university policy assertion.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../src/App';
import type { ChatResponse } from '../../src/types/api';

const question = 'Where is the fixture registration guidance?';
const answer = {
  outcome: 'answer',
  answer: 'Consult the fixture registration instructions for this term.',
  citations: [{ title: 'PNW Registrar fixture', url: 'https://www.pnw.edu/registrar/', contextLabel: 'Hammond — Fall 2026' }],
  appliedContext: { campus: 'hammond', academicTerm: 'Fall 2026' },
} satisfies ChatResponse;
const submitName = /ask|send|submit/i;
let requests: Record<string, unknown>[];
let respond: () => Promise<Response>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

beforeEach(() => {
  requests = [];
  respond = async () => json(answer);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/runtime-config.json') return json({ apiBaseUrl: '/api', timeoutMs: 12000 });
    if (url !== '/api/v1/chat/answers' || init?.method !== 'POST') throw new Error('Unexpected request');
    requests.push(JSON.parse(String(init.body)) as Record<string, unknown>);
    return respond();
  }));
});

async function ask(text = question) {
  const input = await screen.findByRole('textbox', { name: /question/i });
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: submitName }));
}
function expectAnnounced(text: string) {
  const regions = document.querySelectorAll('[aria-live="polite"], [aria-live="assertive"], [role="status"], [role="alert"], [role="log"]');
  expect([...regions].some(region => region.textContent?.includes(text))).toBe(true);
}
function expectCleared() {
  expect(screen.queryByText(answer.answer)).toBeNull();
  expect(screen.queryByText(question)).toBeNull();
  expect(screen.queryByRole('link', { name: answer.citations[0].title })).toBeNull();
  expect(screen.queryByText(/Fall 2026/)).toBeNull();
  for (const control of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input, textarea, select')) {
    expect(control.value).toBe('');
  }
  for (const storage of [localStorage, sessionStorage]) {
    const values = Array.from({ length: storage.length }, (_, i) => storage.getItem(storage.key(i)!));
    for (const marker of [question, answer.answer, 'Fall 2026']) expect(JSON.stringify(values)).not.toContain(marker);
  }
}

describe('student answers and session state (T020)', () => {
  it.each(['referral', 'unresolved'] as const)('renders the %s limitation and descriptive office contact', async outcome => {
    respond = async () => json({ outcome, limitation: 'A reliable fixture answer is unavailable.',
      officeName: 'PNW fixture office', contactUrl: 'https://www.pnw.edu/registrar/' });
    render(<App />);
    await ask();
    await screen.findByText('A reliable fixture answer is unavailable.');
    expectAnnounced('A reliable fixture answer is unavailable.');
    expect(screen.getByRole('link', { name: 'PNW fixture office' }).getAttribute('href')).toBe('https://www.pnw.edu/registrar/');
  });

  it('prominently announces emergency guidance and renders actionable contacts', async () => {
    respond = async () => json({ outcome: 'emergency', guidance: 'Fixture emergency guidance.', contacts: [{
      officeName: 'Fixture safety office', contactUrl: 'https://www.pnw.edu/public-safety/', phone: '911', email: 'fixture@pnw.edu',
    }] });
    render(<App />);
    await ask();
    await screen.findByText('Fixture emergency guidance.');
    expect(screen.getByRole('alert').textContent).toContain('Fixture emergency guidance.');
    expect(screen.getByRole('link', { name: '911' }).getAttribute('href')).toBe('tel:911');
    expect(screen.getByRole('link', { name: 'fixture@pnw.edu' }).getAttribute('href')).toBe('mailto:fixture@pnw.edu');
    expect(screen.queryByRole('heading', { name: 'Answer' })).toBeNull();
  });

  it('clears memory on page exit and returns focus to the question at session end', async () => {
    render(<App />);
    await ask();
    await screen.findByText(answer.answer);
    act(() => window.dispatchEvent(new Event('pagehide')));
    expectCleared();
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /question/i }));
  });

  it('submits a labeled question without requiring unrelated context', async () => {
    render(<App />);
    await ask();
    await screen.findByText(answer.answer);
    expect(requests).toEqual([{ question }]);
  });

  it('renders descriptive source links, applied context and an announced answer', async () => {
    render(<App />);
    await ask();
    await screen.findByText(answer.answer);
    expect(screen.getByRole('link', { name: answer.citations[0].title }).getAttribute('href')).toBe(answer.citations[0].url);
    expect(screen.getAllByText(/Hammond/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Fall 2026/).length).toBeGreaterThan(0);
    expectAnnounced(answer.answer);
  });

  it.each(['', '   '])('does not submit an empty question (%j)', async text => {
    render(<App />);
    await ask(text);
    expect(requests).toEqual([]);
  });

  it.each([
    [400, 'Please check your question and context.'],
    [429, 'Too many requests. Please try again shortly.'],
    [500, 'The service is unavailable. Please try again later.'],
  ])('announces safe HTTP %i errors and permits a subsequent question', async (status, message) => {
    respond = async () => json({ detail: 'PRIVATE_SERVER_DETAIL' }, status);
    render(<App />);
    await ask();
    await screen.findByText(message);
    expectAnnounced(message);
    expect(screen.queryByText(/PRIVATE_SERVER_DETAIL/)).toBeNull();
    expect(screen.queryByRole('link', { name: answer.citations[0].title })).toBeNull();
    respond = async () => json(answer);
    await ask('A new fixture question');
    await screen.findByText(answer.answer);
    expect(requests).toHaveLength(2);
  });

  it('shows a safe network failure instead of transport details', async () => {
    respond = async () => { throw new Error('PRIVATE_TRANSPORT_DETAIL'); };
    render(<App />);
    await ask();
    await screen.findByText('The service is unavailable. Please try again later.');
    expect(screen.queryByText(/PRIVATE_TRANSPORT_DETAIL/)).toBeNull();
  });

  it('does not display an unverified malformed answer', async () => {
    respond = async () => json({ outcome: 'answer', answer: 'UNCITED_POLICY', citations: [], appliedContext: {} });
    render(<App />);
    await ask();
    await screen.findByText('The service could not provide a verified response. Please try again later.');
    expect(screen.queryByText('UNCITED_POLICY')).toBeNull();
  });

  it('clears answers, citations, context and an unsent draft at session end, including after remount', async () => {
    const view = render(<App />);
    await ask();
    await screen.findByText(answer.answer);
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), { target: { value: 'PRIVATE_UNSENT_DRAFT' } });
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));
    await waitFor(expectCleared);
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain('PRIVATE_UNSENT_DRAFT');
    view.unmount();
    render(<App />);
    await screen.findByRole('textbox', { name: /question/i });
    expectCleared();
    await ask('A fresh session question');
    await screen.findByText(answer.answer);
    expect(requests.at(-1)).toEqual({ question: 'A fresh session question' });
  });

  it('clears pending follow-up and all supplied context at session end', async () => {
    const followUp = 'Which campus, program, course and academic term apply?';
    respond = async () => json({ outcome: 'needs_context', question: followUp,
      requiredFields: ['campus', 'program', 'course', 'academicTerm'] });
    render(<App />);
    await ask();
    await screen.findByText(followUp);
    for (const [label, value] of [
      [/campus/i, 'westville'], [/program/i, 'PRIVATE_PROGRAM'],
      [/course/i, 'TEST 123'], [/academic term/i, 'Spring 2027'],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));
    await waitFor(() => expect(screen.queryByText(followUp)).toBeNull());
    expectCleared();
    for (const marker of ['PRIVATE_PROGRAM', 'TEST 123', 'Spring 2027', 'westville']) {
      expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(marker);
    }
    respond = async () => json(answer);
    await ask('Question without inherited context');
    await screen.findByText(answer.answer);
    expect(requests.at(-1)).toEqual({ question: 'Question without inherited context' });
  });

  it('clears displayed errors at session end', async () => {
    respond = async () => json({}, 500);
    render(<App />);
    await ask();
    const message = 'The service is unavailable. Please try again later.';
    await screen.findByText(message);
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));
    await waitFor(() => expect(screen.queryByText(message)).toBeNull());
    expectCleared();
  });

  it('ignores a late answer from an ended session and allows a new request', async () => {
    let finish!: (response: Response) => void;
    respond = () => new Promise(resolve => { finish = resolve; });
    render(<App />);
    await ask();
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));
    await act(async () => { finish(json(answer)); });
    expectCleared();
    respond = async () => json(answer);
    await ask('Fresh question after cancellation');
    await screen.findByText(answer.answer);
    expect(requests.at(-1)).toEqual({ question: 'Fresh question after cancellation' });
  });
});
