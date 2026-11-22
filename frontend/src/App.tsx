import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, FormEvent } from 'react'
import { formatBytes, metadata, request, upload } from './api'
import type { FileMetadata } from './api'
import './App.css'

type IconName = 'image' | 'upload' | 'grid' | 'search' | 'download' | 'close' | 'plus'
function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, string> = {
    image: 'M4 4h16v16H4z M4 16l5-5 4 4 3-3 4 4 M15 8h.01',
    upload: 'M12 16V3 M7 8l5-5 5 5 M4 15v6h16v-6', grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    download: 'M12 3v13 M7 11l5 5 5-5 M4 17v4h16v-4', close: 'M6 6l12 12 M18 6L6 18', plus: 'M12 5v14 M5 12h14',
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}

function PhotoCard({ id, token, onLoaded }: { id: number; token: string; onLoaded: (file: FileMetadata) => void }) {
  const [file, setFile] = useState<FileMetadata | null>(null)
  const [image, setImage] = useState('')
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [downloading, setDownloading] = useState(false)
  const downloadController = useRef<AbortController | null>(null)
  useEffect(() => () => downloadController.current?.abort(), [])
  useEffect(() => {
    const controller = new AbortController()
    let url = ''
    let timer: ReturnType<typeof setTimeout> | undefined
    let polls = 0
    setError('')
    setImage('')
    async function refresh() {
      try {
        const next = await metadata(id, token, controller.signal)
        if (controller.signal.aborted) return
        setFile(next)
        onLoaded(next)
        if (next.thumbnail_status === 'ready' || next.thumbnail_status === 'failed') {
          const response = await request(`/files/${id}/thumbnail`, token, controller.signal)
          if (response.status === 202) { timer = setTimeout(refresh, 2000); return }
          const blob = await response.blob()
          if (controller.signal.aborted) return
          url = URL.createObjectURL(blob)
          setImage(url)
        } else if (++polls < 90) timer = setTimeout(refresh, 2000)
        else setError('Still processing. Refresh the preview to check again.')
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Preview unavailable.') }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer); if (url) URL.revokeObjectURL(url) }
  }, [id, token, revision, onLoaded])

  async function downloadOriginal() {
    const controller = new AbortController()
    downloadController.current = controller
    setDownloading(true)
    try {
      const response = await request(`/files/${id}/original`, token, controller.signal)
      const blob = await response.blob()
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = file?.filename || `image-${id}`
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Download failed.') }
    finally { if (!controller.signal.aborted) setDownloading(false) }
  }
  const status = file?.thumbnail_status || 'loading'
  return <article className="photo-card">
    <div className={`photo-preview ${image ? 'has-image' : ''}`}>
      {image ? <img src={image} alt={file?.filename || `Image ${id}`} /> : <div className="preview-pending"><Icon name="image" size={32} /><span>{error ? 'Preview unavailable' : status === 'loading' ? 'Loading image…' : 'Making your preview…'}</span></div>}
      <span className={`badge ${status}`}>{status === 'ready' ? 'Ready' : status === 'failed' ? 'Preview failed' : status === 'loading' ? 'Loading' : 'Processing'}</span>
    </div>
    <div className="photo-info"><div><h3 title={file?.filename}>{file?.filename || `Image #${id}`}</h3><p>{file ? formatBytes(file.size_bytes) : 'Loading details'} <span>·</span> #{id}</p></div><button className="icon-button" disabled={!file || downloading} onClick={() => void downloadOriginal()} aria-label={`Download original ${file?.filename || id}`}><Icon name="download" /></button></div>
    {error && <div className="card-error" role="status">{error} <button onClick={() => setRevision(value => value + 1)}>Refresh preview</button></div>}
  </article>
}

// Stable callback avoids restarting card polling on each render.
function App() {
  const [tokenInput, setTokenInput] = useState('')
  const [token, setToken] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [error, setError] = useState('')
  const [ids, setIds] = useState<number[]>([])
  const [files, setFiles] = useState<Record<number, FileMetadata>>({})
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [reference, setReference] = useState('')
  const [loadingReference, setLoadingReference] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [active, setActive] = useState<{ name: string; progress: number } | null>(null)
  const [notice, setNotice] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const uploadController = useRef<AbortController | null>(null)
  const sessionController = useRef<AbortController | null>(null)
  const busy = useRef(false)
  useEffect(() => () => { uploadController.current?.abort(); sessionController.current?.abort() }, [])
  const onLoaded = useCallback((file: FileMetadata) => setFiles(current => {
    const old = current[file.file_id]
    return old?.thumbnail_status === file.thumbnail_status && old?.filename === file.filename ? current : { ...current, [file.file_id]: file }
  }), [])

  async function connect(event: FormEvent) {
    event.preventDefault()
    if (connecting) return
    setConnecting(true); setConnectionError('')
    const next = tokenInput.trim()
    const controller = new AbortController()
    sessionController.current = controller
    try {
      await request('/metrics/thumbnail-cache', next, controller.signal)
      setToken(next); setTokenInput('')
    } catch (err) { setConnectionError(err instanceof TypeError ? 'Cannot reach the API. Start the backend on port 8000.' : err instanceof Error ? err.message : 'Connection failed.') }
    finally { setConnecting(false) }
  }
  function disconnect() {
    uploadController.current?.abort(); sessionController.current?.abort()
    setToken(''); setIds([]); setFiles({}); setSearch(''); setFilter('all'); setReference(''); setError(''); setNotice(''); setActive(null); busy.current = false
  }
  async function sendFiles(selected: File[]) {
    if (!token || busy.current || !selected.length) return
    const invalid = selected.find(file => file.size > 50 * 1024 * 1024 || !/\.(jpe?g|png|webp|gif)$/i.test(file.name))
    if (invalid) { setError(`${invalid.name}: choose a JPEG, PNG, WebP, or GIF up to 50 MiB.`); return }
    busy.current = true
    setError(''); setNotice('')
    const controller = new AbortController()
    uploadController.current = controller
    let completed = 0
    try {
      for (const file of selected) {
        if (controller.signal.aborted) break
        setActive({ name: file.name, progress: 0 })
        const result = await upload(file, token, progress => setActive({ name: file.name, progress }), controller.signal)
        if (controller.signal.aborted) break
        setIds(current => [result.file_id, ...current.filter(id => id !== result.file_id)])
        completed++
      }
      if (!controller.signal.aborted) setNotice(`${completed} ${completed === 1 ? 'image' : 'images'} uploaded. Previews appear as they finish.`)
    } catch (err) {
      if (!controller.signal.aborted) setError(`${completed ? `${completed} uploaded. ` : ''}${err instanceof Error ? err.message : 'Upload failed.'} Remaining files were not sent.`)
    } finally { if (uploadController.current === controller) { setActive(null); busy.current = false } }
  }
  async function openReference(event: FormEvent) {
    event.preventDefault(); setError('')
    const id = Number(reference)
    if (!Number.isSafeInteger(id) || id <= 0) { setError('Enter a positive file reference ID.'); return }
    setLoadingReference(true)
    const controller = sessionController.current
    try {
      const file = await metadata(id, token, controller?.signal)
      if (controller?.signal.aborted) return
      onLoaded(file); setIds(current => [id, ...current.filter(value => value !== id)]); setReference('')
    } catch (err) { if (!controller?.signal.aborted) setError(err instanceof Error ? err.message : 'Could not open image.') }
    finally { setLoadingReference(false) }
  }
  function drop(event: DragEvent) { event.preventDefault(); setDragging(false); void sendFiles(Array.from(event.dataTransfer.files)) }
  function choose(event: ChangeEvent<HTMLInputElement>) { void sendFiles(Array.from(event.target.files || [])); event.target.value = '' }
  const visible = ids.filter(id => {
    const file = files[id]
    return (!search || `${file?.filename || ''} ${id}`.toLowerCase().includes(search.toLowerCase())) && (filter === 'all' || (filter === 'processing' ? !file || !['ready', 'failed'].includes(file.thumbnail_status) : file?.thumbnail_status === filter))
  })
  const ready = Object.values(files).filter(file => file.thumbnail_status === 'ready').length
  return <div className="workspace">
    <aside className="sidebar"><a className="brand" href="#"><span className="brand-mark"><Icon name="image" size={24} /></span>still<span className="brand-period">.</span></a><p className="sidebar-caption">YOUR IMAGE WORKSPACE</p><div className="nav-active"><Icon name="grid" />Library <span>{ids.length}</span></div><div className="sidebar-bottom"><span className="storage-mark">S</span><div><strong>Space for your images</strong><p>Originals, safely kept.</p></div></div></aside>
    <div className="main-area"><header className="topbar"><span>Workspace <span className="breadcrumb">/</span> <strong>Library</strong></span>{token && <button className="text-button" onClick={disconnect}>Disconnect <Icon name="close" size={16} /></button>}</header>
    <main><div className="page-heading"><div><p className="eyebrow">A LITTLE LESS WAITING. A LOT MORE CREATING.</p><h1>Your image library<span>.</span></h1><p className="subtitle">Drop in your images. We’ll take care of the small details.</p></div><div className="heading-symbol"><Icon name="image" size={42} /></div></div>
    {!token ? <section className="connect-panel"><div className="connect-copy"><span className="section-label">LET’S GET YOU CONNECTED</span><h2>Your images start here.</h2><p>Connect with your API token to upload images and access your originals.</p><div className="privacy-note"><Icon name="image" /><span>Your token stays in memory for this session. It is cleared when you refresh or disconnect.</span></div></div><form onSubmit={event => void connect(event)}><label htmlFor="token">API access token</label><input id="token" type="password" autoComplete="off" value={tokenInput} onChange={event => setTokenInput(event.target.value)} placeholder="Paste your token" required disabled={connecting} /><p className="field-help">Use the original bearer token configured for your account.</p>{connectionError && <p className="error-message" role="alert">{connectionError}</p>}<button className="primary" disabled={connecting || !tokenInput.trim()}>{connecting ? 'Connecting…' : 'Connect to workspace'}</button></form></section> : <>
    <section className={`dropzone ${dragging ? 'dragging' : ''} ${active ? 'uploading' : ''}`} onDragOver={event => { event.preventDefault(); if (!active) setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={drop} aria-label="Image upload">
      <div className="upload-symbol"><Icon name="upload" size={26} /></div><div className="drop-copy"><h2>{active ? `Uploading ${active.name}` : 'Good images belong here.'}</h2><p>{active ? active.progress === 100 ? 'Upload received. Saving your image…' : `${active.progress}% transferred` : 'Drag and drop your images, or choose files to get started.'}</p></div><input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden onChange={choose} /><button className="primary" disabled={!!active} onClick={() => input.current?.click()}><Icon name="plus" size={18} />{active ? 'Uploading…' : 'Choose images'}</button><span className="upload-hint">JPEG, PNG, WebP & GIF · Up to 50 MiB each</span>{active && <progress aria-label="Upload progress" value={active.progress} max={100} />}
    </section>
    {error && <div className="error-banner" role="alert">{error}<button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><Icon name="close" size={16} /></button></div>}{notice && <p className="success-message" role="status">{notice}</p>}
    <div className="library-heading"><div><h2>All images <span className="count">{ids.length}</span></h2><p>{ready} ready to view <span>·</span> Current session</p></div><form className="reference-form" onSubmit={event => void openReference(event)}><label className="sr-only" htmlFor="reference">Existing file reference ID</label><input id="reference" inputMode="numeric" value={reference} onChange={event => setReference(event.target.value)} placeholder="File reference ID" required /><button className="secondary" disabled={loadingReference}>{loadingReference ? 'Opening…' : 'Open image'}</button></form></div>
    <div className="library-toolbar"><div className="filters" aria-label="Filter images">{[['all', 'All images'], ['ready', 'Ready'], ['processing', 'Processing'], ['failed', 'Failed']].map(([value, label]) => <button key={value} className={filter === value ? 'selected' : ''} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div><div className="search"><Icon name="search" size={17} /><input aria-label="Search images" placeholder="Search images…" value={search} onChange={event => setSearch(event.target.value)} /></div></div>
    {ids.length > 0 && <div className="photo-grid">{ids.map(id => <div key={id} hidden={!visible.includes(id)}><PhotoCard id={id} token={token} onLoaded={onLoaded} /></div>)}</div>}{!visible.length && <div className="empty-state"><div className="empty-icon"><Icon name="image" size={36} /></div><h3>{ids.length ? 'No matching images' : 'A fresh space for your next idea.'}</h3><p>{ids.length ? 'Try a different search or filter.' : 'Upload your first image, or open an existing one by its reference ID.'}</p>{!ids.length && <button className="text-button" onClick={() => input.current?.click()}>Choose your first image</button>}</div>}
    </>}
    <footer><span>Made for the bigger picture.</span><span>Originals preserved. Previews made for speed.</span></footer></main></div>
  </div>
}
export default App
