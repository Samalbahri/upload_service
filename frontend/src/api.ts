export type FileMetadata = {
  file_id: number
  filename: string
  size_bytes: number
  content_type: string
  thumbnail_status: string
  created_at: string
}

const BASE = (import.meta.env?.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

export async function responseError(response: Response): Promise<ApiError> {
  let detail: unknown
  try { detail = (await response.json()).detail } catch { /* Non-JSON server error. */ }
  let message = typeof detail === 'string' ? detail : 'The request could not be completed.'
  if (detail && typeof detail === 'object' && 'message' in detail) {
    message = String(detail.message)
    if ('remaining_bytes' in detail) message += ` (${formatBytes(Number(detail.remaining_bytes))} remaining).`
  }
  if (response.status === 401) message = 'Your token was not accepted. Reconnect with a valid token.'
  if (response.status === 429) message = `Upload limit reached. Try again in ${response.headers.get('Retry-After') || '60'} seconds.`
  const trace = response.headers.get('X-Request-ID')
  if (response.status >= 500 && trace) message += ` Request ID: ${trace}`
  return new ApiError(message, response.status)
}

export async function request(path: string, token: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, { headers: { Authorization: `Bearer ${token}` }, signal })
  if (!response.ok) throw await responseError(response)
  return response
}

export async function metadata(id: number, token: string, signal?: AbortSignal): Promise<FileMetadata> {
  return (await request(`/files/${id}`, token, signal)).json()
}

export function upload(file: File, token: string, onProgress: (percent: number) => void, signal: AbortSignal): Promise<{ file_id: number }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    signal.addEventListener('abort', abort, { once: true })
    const clean = () => signal.removeEventListener('abort', abort)
    xhr.open('POST', `${BASE}/files`)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    // Keep HTTP header values ASCII; metadata remains a useful display filename.
    xhr.setRequestHeader('X-Filename', file.name.replace(/[^\x20-\x7E]/g, '_').slice(0, 255))
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)) }
    xhr.onload = async () => {
      clean()
      if (xhr.status === 201) {
        try { resolve(JSON.parse(xhr.responseText)) } catch { reject(new Error('The server returned an invalid upload response.')) }
      } else {
        const headers = new Headers()
        for (const name of ['Retry-After', 'X-Request-ID']) { const value = xhr.getResponseHeader(name); if (value) headers.set(name, value) }
        reject(await responseError(new Response(xhr.responseText, { status: xhr.status || 500, headers })))
      }
    }
    xhr.onerror = () => { clean(); reject(new Error('Cannot reach the API. Check that the backend is running.')) }
    xhr.onabort = () => { clean(); reject(new DOMException('Upload cancelled', 'AbortError')) }
    if (signal.aborted) { clean(); reject(new DOMException('Upload cancelled', 'AbortError')); return }
    xhr.send(file)
  })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
