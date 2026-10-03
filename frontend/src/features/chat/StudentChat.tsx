import { useEffect, useReducer, useRef } from 'react';
import type { FormEvent } from 'react';
import { loadApiClient, ServiceError } from '../../api/client';
import type { ChatResponse, StudentQuestion } from '../../types/api';
import { chatReducer, contextLabels, initialChatState } from './chatState';

function Result({ response }: { response: ChatResponse }) {
  switch (response.outcome) {
    case 'answer': return <>
      <h2>Answer</h2><p className="response-text">{response.answer}</p>
      <h3>Official sources</h3>
      <ul>{response.citations.map((citation, index) => <li key={index}>
        <a href={citation.url}>{citation.title}</a>
        {citation.contextLabel && <span> — {citation.contextLabel}</span>}
      </li>)}</ul>
      {Object.values(response.appliedContext).some(Boolean) && <>
        <h3>Applied context</h3><dl>{Object.entries(response.appliedContext).filter(([, value]) => value).map(([field, value]) =>
          <div key={field}><dt>{contextLabels[field as keyof typeof contextLabels]}</dt>
            <dd>{value === 'hammond' ? 'Hammond' : value === 'westville' ? 'Westville' : value}</dd></div>)}</dl>
      </>}
    </>;
    case 'needs_context': return <><h2>More information needed</h2><p>{response.question}</p></>;
    case 'referral': case 'unresolved': return <>
      <h2>{response.outcome === 'referral' ? 'Contact an office' : 'Reliable information unavailable'}</h2>
      <p>{response.limitation}</p><a href={response.contactUrl}>{response.officeName}</a>
    </>;
    case 'emergency': return <>
      <h2>Emergency guidance</h2><p className="response-text">{response.guidance}</p>
      <ul>{response.contacts.map((contact, index) => <li key={index}>
        <a href={contact.contactUrl}>{contact.officeName}</a>
        {contact.phone && <p>Phone: <a href={`tel:${contact.phone.replace(/[^+\d]/g, '')}`}>{contact.phone}</a></p>}
        {contact.email && <p>Email: <a href={`mailto:${contact.email}`}>{contact.email}</a></p>}
      </li>)}</ul>
    </>;
  }
}

export default function StudentChat() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const active = useRef<AbortController | null>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const end = () => {
      active.current?.abort(); active.current = null;
      dispatch({ type: 'end' });
    };
    window.addEventListener('pagehide', end);
    return () => { window.removeEventListener('pagehide', end); active.current?.abort(); active.current = null; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!state.draft.trim() || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    const request: StudentQuestion = { question: state.draft.trim() };
    dispatch({ type: 'start' });
    try {
      const client = await loadApiClient();
      if (active.current !== controller) return;
      const response = await client.ask(request, controller.signal);
      if (active.current === controller) dispatch({ type: 'response', response });
    } catch (error) {
      if (active.current === controller) dispatch({ type: 'error', error: error instanceof ServiceError
        ? error.message : 'The service is unavailable. Please try again later.' });
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  function endSession() {
    active.current?.abort(); active.current = null;
    dispatch({ type: 'end' });
    questionInput.current?.focus();
  }
  return <section aria-label="Student chat">
    <p>Ask about general PNW information. Avoid sharing personal records or sensitive details.</p>
    <p id="privacy-note">Your question and results stay in this page’s memory. End session or leave this page to clear them.</p>
    <form onSubmit={submit} aria-describedby="privacy-note">
      <label htmlFor="student-question">Question</label>
      <textarea id="student-question" ref={questionInput} value={state.draft} maxLength={4000} required
        onChange={event => dispatch({ type: 'draft', value: event.target.value })} />
      <button type="submit" disabled={state.pending}>Ask</button>
      <button type="button" onClick={endSession}>End session</button>
    </form>
    <div aria-live="polite" aria-atomic="true">
      {state.pending && <p>Checking approved information…</p>}
      {state.error && <p>{state.error}</p>}
      {state.response?.outcome !== 'emergency' && state.response && <Result response={state.response} />}
    </div>
    <div role="alert" aria-atomic="true" className={state.response?.outcome === 'emergency' ? 'emergency' : undefined}>
      {state.response?.outcome === 'emergency' && <Result response={state.response} />}
    </div>
    {state.response?.outcome === 'needs_context' && <fieldset>
      <legend>Requested context</legend>
      {[...new Set(state.response.requiredFields)].map(field => <div key={field}>
        <label htmlFor={`context-${field}`}>{contextLabels[field]}</label>
        {field === 'campus' ? <select id={`context-${field}`} value={state.context[field] ?? ''}
          onChange={event => dispatch({ type: 'context', field, value: event.target.value })}>
          <option value="">Select campus</option><option value="hammond">Hammond</option><option value="westville">Westville</option>
        </select> : <input id={`context-${field}`} value={state.context[field] ?? ''}
          maxLength={field === 'program' ? 160 : field === 'course' ? 32 : 80}
          onChange={event => dispatch({ type: 'context', field, value: event.target.value })} />}
      </div>)}
    </fieldset>}
  </section>;
}
