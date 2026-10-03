import type { ChatResponse, ContextField } from '../../types/api';

export const contextLabels: Record<ContextField, string> = {
  campus: 'Campus', program: 'Program', course: 'Course', academicTerm: 'Academic term',
};
export interface ChatState {
  draft: string;
  context: Partial<Record<ContextField, string>>;
  pending: boolean;
  response: ChatResponse | null;
  error: string | null;
}
// No browser storage: unmounting or ending the session discards every field.
export const initialChatState: ChatState = {
  draft: '', context: {}, pending: false, response: null, error: null,
};
type Action =
  | { type: 'draft'; value: string }
  | { type: 'context'; field: ContextField; value: string }
  | { type: 'start' }
  | { type: 'response'; response: ChatResponse }
  | { type: 'error'; error: string }
  | { type: 'end' };
export function chatReducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'draft': return { ...state, draft: action.value };
    case 'context': return { ...state, context: { ...state.context, [action.field]: action.value } };
    case 'start': return { ...state, pending: true, response: null, error: null };
    case 'response': return { ...state, pending: false, response: action.response };
    case 'error': return { ...state, pending: false, error: action.error };
    case 'end': return initialChatState;
  }
}
