import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, metadata, responseError, upload } from '../src/api.ts'

test('protected metadata sends bearer credentials and preserves the reference', async t => {
  const previous = globalThis.fetch
  t.after(() => { globalThis.fetch = previous })
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/files/42')
    assert.equal(options.headers.Authorization, 'Bearer test-token')
    return Response.json({ file_id: 42, thumbnail_status: 'pending' })
  }
  assert.equal((await metadata(42, 'test-token')).file_id, 42)
})

test('quota, rate limit, and server trace errors remain actionable', async () => {
  const quota = await responseError(Response.json({ detail: { message: 'Storage quota exceeded', remaining_bytes: 1024 } }, { status: 413 }))
  assert.match(quota.message, /1.0 KB remaining/)
  const rate = await responseError(Response.json({ detail: 'limited' }, { status: 429, headers: { 'Retry-After': '17' } }))
  assert.match(rate.message, /17 seconds/)
  const server = await responseError(new Response('failed', { status: 503, headers: { 'X-Request-ID': 'trace-42' } }))
  assert.match(server.message, /trace-42/)
  assert.ok(server instanceof ApiError)
})

class FakeXHR {
  static current
  upload = {}
  headers = {}
  status = 201
  responseText = '{"file_id":7}'
  constructor() { FakeXHR.current = this }
  open(method, path) { this.method = method; this.path = path }
  setRequestHeader(name, value) { this.headers[name] = value }
  getResponseHeader() { return null }
  send(body) { this.body = body }
  abort() { this.onabort?.() }
}

test('upload sends raw file bytes, progress, and authentication', async t => {
  const previous = globalThis.XMLHttpRequest
  globalThis.XMLHttpRequest = FakeXHR
  t.after(() => { globalThis.XMLHttpRequest = previous })
  const file = new File(['image bytes'], 'photo.jpg', { type: 'image/jpeg' })
  const progress = []
  const result = upload(file, 'secret', value => progress.push(value), new AbortController().signal)
  const xhr = FakeXHR.current
  assert.equal(xhr.body, file)
  assert.equal(xhr.method, 'POST')
  assert.equal(xhr.path, '/api/v1/files')
  assert.equal(xhr.headers.Authorization, 'Bearer secret')
  assert.equal(xhr.headers['X-Filename'], 'photo.jpg')
  xhr.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 })
  await xhr.onload()
  assert.deepEqual(await result, { file_id: 7 })
  assert.deepEqual(progress, [50])
})

test('disconnect aborts an in-flight upload', async t => {
  const previous = globalThis.XMLHttpRequest
  globalThis.XMLHttpRequest = FakeXHR
  t.after(() => { globalThis.XMLHttpRequest = previous })
  const controller = new AbortController()
  const result = upload(new File(['x'], 'x.jpg'), 'secret', () => {}, controller.signal)
  controller.abort()
  await assert.rejects(result, error => error.name === 'AbortError')
})
