// studio/api.js — the studio API (contract C6, docs/plans/newsletter-platform.md), served by agent-subscriptions.
//
// Every function here has a twin in mock-api.js with the same name, arguments and result, so the page never
// knows which one it talks to. connect() picks: the mock with ?mock=1, or when GET /catalog answers 404 (the
// routes do not exist until roadmap E ships them); the real API otherwise.
//
// Failures throw ApiError with a plain `message` for the page; a 400 on save also carries the validator's
// `errors` list (["block 3 (section): ...", ...]), which the page maps onto the blocks.

import { authFetch, getIdToken } from '../auth.js';

export const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
  return 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
})();

export class ApiError extends Error {
  constructor(message, { status = 0, errors = [], code = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.errors = errors;
    this.code = code;
  }
}

const MESSAGES = {
  0: 'Could not reach the server. Check your connection and try again.',
  401: 'Your session has ended. Please sign in again.',
  403: 'The studio is for premium members.',
  404: 'That newsletter no longer exists.',
  409: 'You already have the most newsletters you can keep. Delete one first.',
  413: 'That is too large to save.',
  429: 'Too many changes at once. Wait a moment and try again.',
};

function messageFor(status, detail) {
  if (detail === 'ai_unavailable') return 'AI is unavailable right now. Please try again.';
  if (detail === 'limit_reached') return MESSAGES[409];
  return MESSAGES[status] || (status >= 500
    ? 'The server had a problem. Try again in a moment.'
    : 'Something went wrong. Please try again.');
}

async function call(path, { method = 'GET', body, signal, auth = true } = {}) {
  let res;
  try {
    const init = { method, signal };
    if (body !== undefined) init.body = JSON.stringify(body);
    res = auth
      ? await authFetch(`${API_BASE}${path}`, init)
      : await fetch(`${API_BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json' } });
  } catch (e) {
    if (e?.name === 'AbortError') throw e;
    if (e?.message === 'not_authenticated') throw new ApiError(MESSAGES[401], { status: 401 });
    throw new ApiError(MESSAGES[0]);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : '';
    const errors = Array.isArray(data.errors) ? data.errors
      : Array.isArray(data.detail?.errors) ? data.detail.errors : [];
    const msg = errors.length ? 'Some blocks need a fix before saving.' : messageFor(res.status, detail);
    throw new ApiError(msg, { status: res.status, errors, code: detail });
  }
  return data;
}

/** {topics, widgets}. Sends the sign-in token when there is one, so admins also see hidden topics. */
export async function getCatalog() {
  const token = await getIdToken().catch(() => null);
  return call('/catalog', { auth: !!token });
}

/** {newsletters: [{id, definition, subscribed, updated_at}], limit} */
export function listNewsletters() {
  return call('/auth/newsletters');
}

/** Create (no id) or update. Returns {id, definition}. */
export function saveNewsletter({ id, definition }) {
  return call('/auth/newsletters', { method: 'POST', body: id ? { id, definition } : { definition } });
}

export function deleteNewsletter(id) {
  return call(`/auth/newsletters/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** {html, size_bytes, warnings} */
export function previewNewsletter(definition, { signal } = {}) {
  return call('/auth/newsletters/preview', { method: 'POST', body: { definition }, signal });
}

/** {subscribed} */
export function setSubscribed(id, subscribed) {
  return call(`/auth/newsletters/${encodeURIComponent(id)}/${subscribed ? 'subscribe' : 'unsubscribe'}`, { method: 'POST' });
}

/** The existing custom-section helper (Claude Haiku): {refined_topic}. */
export function refineTopic(rawTopic) {
  return call('/auth/sections/refine', { method: 'POST', body: { raw_topic: rawTopic } });
}

export const isMock = false;

/**
 * Pick the API for this page load. Returns {api, catalog, reason}: reason is 'real', 'forced' (?mock=1) or
 * 'not-ready' (the server has no studio routes yet). Other catalog failures throw.
 */
export async function connect({ forceMock = false } = {}) {
  if (forceMock) {
    const mock = await import('./mock-api.js');
    return { api: mock, catalog: await mock.getCatalog(), reason: 'forced' };
  }
  try {
    const self = await import('./api.js');
    return { api: self, catalog: await getCatalog(), reason: 'real' };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      const mock = await import('./mock-api.js');
      return { api: mock, catalog: await mock.getCatalog(), reason: 'not-ready' };
    }
    throw e;
  }
}
