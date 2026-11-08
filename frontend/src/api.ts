export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export async function request<T>(path: string, token = '', method = 'GET', body?: object | FormData, signal?: AbortSignal): Promise<T> {
  const headers = new Headers()
  if (token) headers.set('Authorization', 'Bearer ' + token)
  const form = body instanceof FormData
  if (body && !form) headers.set('Content-Type', 'application/json')
  const response = await fetch('/api' + path, {
    method,
    headers,
    body: form ? body : body ? JSON.stringify(body) : undefined,
    signal,
    cache: 'no-store',
  })
  let data
  try {
    data = await response.json()
  } catch {
    throw new ApiError('The server returned an unreadable response. Check that PrintForge is running.', response.status)
  }
  if (!response.ok) {
    const detail = data?.detail
    const message = Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => [item.loc?.slice(1).join(' · '), item.msg].filter(Boolean).join(' — ')).join('; ')
      : typeof detail === 'string' ? detail : 'Request failed (' + response.status + ').'
    throw new ApiError(message, response.status)
  }
  return data as T
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}
