export type Campus = 'hammond' | 'westville';
export type ContextField = 'campus' | 'program' | 'course' | 'academicTerm';
export interface QuestionContext {
  campus?: Campus | null;
  program?: string | null;
  course?: string | null;
  academicTerm?: string | null;
}
export interface StudentQuestion extends QuestionContext { question: string }
export interface Citation { title: string; url: string; contextLabel?: string | null }
export interface Contact { officeName: string; contactUrl: string; phone?: string | null; email?: string | null }
export type ChatResponse =
  | { outcome: 'answer'; answer: string; citations: Citation[]; appliedContext: QuestionContext }
  | { outcome: 'needs_context'; question: string; requiredFields: ContextField[] }
  | { outcome: 'referral' | 'unresolved'; limitation: string; officeName: string; contactUrl: string }
  | { outcome: 'emergency'; guidance: string; contacts: Contact[] };

const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0;
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const length = (v: string) => Array.from(v).length;
export function isHttpsUrl(v: unknown): v is string {
  if (typeof v !== 'string' || /[\s\\]/u.test(v) || Array.from(v).some(c => c.charCodeAt(0) < 32)) return false;
  try { const url = new URL(v); return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password; }
  catch { return false; }
}
function context(v: unknown): v is QuestionContext {
  if (!object(v) || !keys(v, ['campus', 'program', 'course', 'academicTerm'])) return false;
  return (v.campus == null || v.campus === 'hammond' || v.campus === 'westville') &&
    ([['program', 160], ['course', 32], ['academicTerm', 80]] as const).every(([key, max]) =>
      v[key] == null || typeof v[key] === 'string' && length(v[key]) <= max);
}
export function isStudentQuestion(v: unknown): v is StudentQuestion {
  if (!object(v) || typeof v.question !== 'string' || length(v.question) < 1 || length(v.question) > 4000) return false;
  const rest = { ...v };
  delete rest.question;
  return context(rest);
}
function citation(v: unknown): boolean {
  return object(v) && keys(v, ['title', 'url', 'contextLabel']) && text(v.title) && isHttpsUrl(v.url) && (v.contextLabel == null || text(v.contextLabel));
}
function contact(v: unknown): boolean {
  return object(v) && keys(v, ['officeName', 'contactUrl', 'phone', 'email']) && text(v.officeName) && isHttpsUrl(v.contactUrl) &&
    (v.phone == null || text(v.phone)) && (v.email == null || text(v.email));
}
export function isChatResponse(v: unknown): v is ChatResponse {
  if (!object(v)) return false;
  switch (v.outcome) {
    case 'answer': return keys(v, ['outcome', 'answer', 'citations', 'appliedContext']) && text(v.answer) &&
      Array.isArray(v.citations) && v.citations.length > 0 && v.citations.every(citation) && context(v.appliedContext);
    case 'needs_context': return keys(v, ['outcome', 'question', 'requiredFields']) && text(v.question) &&
      Array.isArray(v.requiredFields) && v.requiredFields.length > 0 && v.requiredFields.every(f => ['campus', 'program', 'course', 'academicTerm'].includes(f));
    case 'referral': case 'unresolved': return keys(v, ['outcome', 'limitation', 'officeName', 'contactUrl']) && text(v.limitation) && text(v.officeName) && isHttpsUrl(v.contactUrl);
    case 'emergency': return keys(v, ['outcome', 'guidance', 'contacts']) && text(v.guidance) && Array.isArray(v.contacts) && v.contacts.length > 0 && v.contacts.every(contact);
    default: return false;
  }
}
