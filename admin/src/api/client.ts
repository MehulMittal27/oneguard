/**
 * One small fetch wrapper, shared by every `api/*.ts` module — same pattern
 * as `frontend/src/api/*.ts`, minus the mock branch (there is no reason to
 * fake the operator endpoints: this console exists to drive the real
 * backend, never a substitute for it). `docs/api-contract.md` §3.8: every
 * error is `{ error: { code, message, detail? } }`; C5/C8 answer `204` with
 * no body.
 */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: unknown

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  const body = contentType.includes('application/json') ? await response.json() : null

  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string; detail?: unknown } } | null)?.error
    throw new ApiError(
      response.status,
      error?.code ?? 'internal',
      error?.message ?? `Request failed (${response.status})`,
      error?.detail,
    )
  }
  return body as T
}

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
}

/** 404 as "nothing there yet" rather than an error — D1/C3/D3-current all use this shape. */
export async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await api.get<T>(path)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}
