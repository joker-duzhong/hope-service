import imageCompression from 'browser-image-compression'
import * as qiniu from 'qiniu-js'
import type { AuthTokens } from '@/types/auth'

const KEYS = {
  accessToken: 'auth_access_token',
  refreshToken: 'auth_refresh_token',
  tokenType: 'auth_token_type',
} as const

const DEFAULT_COMPRESSION_OPTIONS = {
  enabled: true,
  maxSizeMB: 1,
  maxWidthOrHeight: 1920,
  useWebWorker: true,
  initialQuality: 0.85,
} as const

const DEFAULT_THUMBNAIL_OPTIONS = {
  enabled: true,
  maxWidthOrHeight: 512,
  quality: 0.85,
} as const

const MIME_EXTENSION_MAP: Record<string, string> = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/webp': '.webp',
  'image/heic': '.heic',
  'image/heif': '.heif',
  'video/mp4': '.mp4',
  'audio/mpeg': '.mp3',
  'application/pdf': '.pdf',
}

export interface StorageUploadTokenResponse {
  token: string
  domain: string
}

export interface StorageUploadFinalizePayload {
  index: number
  total: number
  appKey: string
  originalFile: File
  uploadedFile: File
  thumbnailFile: File | null
  key: string
  thumbKey: string | null
  hash: string
  token: StorageUploadTokenResponse
  qiniu: QiniuUploadResponse
  thumbQiniu: QiniuUploadResponse | null
}

export interface StorageUploadAdapter<TResource = unknown> {
  getUploadToken: () => Promise<StorageUploadTokenResponse>
  confirmUpload: (payload: StorageUploadFinalizePayload) => Promise<TResource>
}

export interface StorageObjectKeyContext {
  kind: 'original' | 'thumbnail'
  index: number
  originalFile: File
  processedFile: File
}

export interface ImageCompressionOptions {
  enabled?: boolean
  maxSizeMB?: number
  maxWidthOrHeight?: number
  useWebWorker?: boolean
  fileType?: string
  initialQuality?: number
}

export interface ImageThumbnailOptions {
  enabled?: boolean
  maxWidthOrHeight?: number
  fileType?: string
  quality?: number
}

export type StorageUploadPhase =
  | 'preparing'
  | 'uploading_original'
  | 'uploading_thumbnail'
  | 'confirming'
  | 'completed'

export interface StorageUploadProgress {
  index: number
  total: number
  file: File
  phase: StorageUploadPhase
  percent: number
}

export interface StorageResolvedUrls<TResource = unknown> {
  url: string
  thumbUrl?: string | null
  resource: TResource
}

export interface StorageBatchUploadOptions<TResource = unknown> {
  adapter: StorageUploadAdapter<TResource>
  appKey: string
  compression?: ImageCompressionOptions | boolean
  thumbnail?: ImageThumbnailOptions | boolean
  keyFactory?: (file: File, context: StorageObjectKeyContext) => string
  onFileProgress?: (progress: StorageUploadProgress) => void
  resolveResourceUrls?: (
    resource: TResource,
    context: Pick<StorageUploadFinalizePayload, 'key' | 'thumbKey' | 'token'>
  ) => StorageResolvedUrls<TResource>
}

export interface StorageUploadedFile<TResource = unknown> {
  index: number
  file: File
  originalFile: File
  uploadedFile: File
  thumbnailFile: File | null
  resource: TResource
  url: string
  thumbUrl: string | null
  key: string
  thumbKey: string | null
  hash: string
  token: StorageUploadTokenResponse
  qiniu: QiniuUploadResponse
  thumbQiniu: QiniuUploadResponse | null
}

interface NormalizedCompressionOptions {
  enabled: boolean
  maxSizeMB: number
  maxWidthOrHeight: number
  useWebWorker: boolean
  fileType?: string
  initialQuality: number
}

interface NormalizedThumbnailOptions {
  enabled: boolean
  maxWidthOrHeight: number
  fileType?: string
  quality: number
}

interface QiniuUploadResponse extends Record<string, unknown> {
  key?: string
  hash?: string
  etag?: string
}

export function getStoredTokens(): { accessToken: string; refreshToken: string; tokenType: string } | null {
  const accessToken = localStorage.getItem(KEYS.accessToken)
  const refreshToken = localStorage.getItem(KEYS.refreshToken)
  const tokenType = localStorage.getItem(KEYS.tokenType)
  if (!accessToken || !refreshToken) return null
  return { accessToken, refreshToken, tokenType: tokenType ?? 'bearer' }
}

export function setStoredTokens(tokens: AuthTokens): void {
  localStorage.setItem(KEYS.accessToken, tokens.access_token)
  localStorage.setItem(KEYS.refreshToken, tokens.refresh_token)
  localStorage.setItem(KEYS.tokenType, tokens.token_type)
}

export function clearStoredTokens(): void {
  localStorage.removeItem(KEYS.accessToken)
  localStorage.removeItem(KEYS.refreshToken)
  localStorage.removeItem(KEYS.tokenType)
}

export function getUuid(len: number = 17): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'.replace(/[x]/g, () => ((Math.random() * 16) | 0).toString(16))

  return `h${random}`.slice(0, len)
}

export function buildStoragePublicUrl(domain: string, key: string): string {
  if (!domain) return key
  if (/^https?:\/\//i.test(key)) return key
  return `${domain.replace(/\/+$/, '')}/${key.replace(/^\/+/, '')}`
}

export async function uploadStorageFiles<TResource = unknown>(
  files: File[],
  options: StorageBatchUploadOptions<TResource>,
): Promise<StorageUploadedFile<TResource>[]> {
  if (!files.length) return []
  if (!options.appKey.trim()) {
    throw new Error('Storage upload requires a non-empty appKey')
  }

  const token = await options.adapter.getUploadToken()
  const total = files.length

  return Promise.all(
    files.map((file, index) => uploadSingleStorageFile(file, index, total, token, options)),
  )
}

export async function uploadStorageFile<TResource = unknown>(
  file: File,
  options: StorageBatchUploadOptions<TResource>,
): Promise<StorageUploadedFile<TResource>> {
  const [uploaded] = await uploadStorageFiles([file], options)
  return uploaded
}

async function uploadSingleStorageFile<TResource>(
  originalFile: File,
  index: number,
  total: number,
  token: StorageUploadTokenResponse,
  options: StorageBatchUploadOptions<TResource>,
): Promise<StorageUploadedFile<TResource>> {
  const compressionOptions = normalizeCompressionOptions(options.compression)
  const thumbnailOptions = normalizeThumbnailOptions(options.thumbnail)
  const keyFactory = options.keyFactory ?? defaultKeyFactory

  notifyProgress(options.onFileProgress, {
    index,
    total,
    file: originalFile,
    phase: 'preparing',
    percent: 0,
  })

  const uploadedFile = await compressForUpload(originalFile, compressionOptions)
  const thumbnailFile = await createThumbnailFile(uploadedFile, thumbnailOptions)

  const key = keyFactory(uploadedFile, {
    kind: 'original',
    index,
    originalFile,
    processedFile: uploadedFile,
  })

  const qiniuResult = await uploadFileToQiniu(uploadedFile, key, token.token, (percent) => {
    notifyProgress(options.onFileProgress, {
      index,
      total,
      file: originalFile,
      phase: 'uploading_original',
      percent,
    })
  })

  let thumbQiniu: QiniuUploadResponse | null = null
  let thumbKey: string | null = null

  if (thumbnailFile) {
    thumbKey = keyFactory(thumbnailFile, {
      kind: 'thumbnail',
      index,
      originalFile,
      processedFile: thumbnailFile,
    })

    thumbQiniu = await uploadFileToQiniu(thumbnailFile, thumbKey, token.token, (percent) => {
      notifyProgress(options.onFileProgress, {
        index,
        total,
        file: originalFile,
        phase: 'uploading_thumbnail',
        percent,
      })
    })

    thumbKey = getQiniuKey(thumbQiniu, thumbKey)
  }

  notifyProgress(options.onFileProgress, {
    index,
    total,
    file: originalFile,
    phase: 'confirming',
    percent: 100,
  })

  const finalizedKey = getQiniuKey(qiniuResult, key)
  const hash = await resolveUploadHash(uploadedFile, qiniuResult)
  const payload: StorageUploadFinalizePayload = {
    index,
    total,
    appKey: options.appKey,
    originalFile,
    uploadedFile,
    thumbnailFile,
    key: finalizedKey,
    thumbKey,
    hash,
    token,
    qiniu: qiniuResult,
    thumbQiniu,
  }

  const resource = await options.adapter.confirmUpload(payload)
  const resolved = resolveUploadedUrls(resource, payload, options.resolveResourceUrls)

  notifyProgress(options.onFileProgress, {
    index,
    total,
    file: originalFile,
    phase: 'completed',
    percent: 100,
  })

  return {
    index,
    file: originalFile,
    originalFile,
    uploadedFile,
    thumbnailFile,
    resource,
    url: resolved.url,
    thumbUrl: resolved.thumbUrl ?? null,
    key: finalizedKey,
    thumbKey,
    hash,
    token,
    qiniu: qiniuResult,
    thumbQiniu,
  }
}

function normalizeCompressionOptions(options?: ImageCompressionOptions | boolean): NormalizedCompressionOptions {
  if (options === false) {
    return {
      ...DEFAULT_COMPRESSION_OPTIONS,
      enabled: false,
    }
  }

  if (options === true || options === undefined) {
    return {
      ...DEFAULT_COMPRESSION_OPTIONS,
    }
  }

  return {
    enabled: options.enabled ?? DEFAULT_COMPRESSION_OPTIONS.enabled,
    maxSizeMB: options.maxSizeMB ?? DEFAULT_COMPRESSION_OPTIONS.maxSizeMB,
    maxWidthOrHeight: options.maxWidthOrHeight ?? DEFAULT_COMPRESSION_OPTIONS.maxWidthOrHeight,
    useWebWorker: options.useWebWorker ?? DEFAULT_COMPRESSION_OPTIONS.useWebWorker,
    fileType: options.fileType,
    initialQuality: options.initialQuality ?? DEFAULT_COMPRESSION_OPTIONS.initialQuality,
  }
}

function normalizeThumbnailOptions(options?: ImageThumbnailOptions | boolean): NormalizedThumbnailOptions {
  if (options === false) {
    return {
      ...DEFAULT_THUMBNAIL_OPTIONS,
      enabled: false,
    }
  }

  if (options === true || options === undefined) {
    return {
      ...DEFAULT_THUMBNAIL_OPTIONS,
    }
  }

  return {
    enabled: options.enabled ?? DEFAULT_THUMBNAIL_OPTIONS.enabled,
    maxWidthOrHeight: options.maxWidthOrHeight ?? DEFAULT_THUMBNAIL_OPTIONS.maxWidthOrHeight,
    fileType: options.fileType,
    quality: options.quality ?? DEFAULT_THUMBNAIL_OPTIONS.quality,
  }
}

async function compressForUpload(file: File, options: NormalizedCompressionOptions): Promise<File> {
  if (!options.enabled || !isCompressibleImage(file)) return file

  try {
    return await imageCompression(file, {
      maxSizeMB: options.maxSizeMB,
      maxWidthOrHeight: options.maxWidthOrHeight,
      useWebWorker: options.useWebWorker,
      fileType: options.fileType,
      initialQuality: options.initialQuality,
    })
  } catch {
    return file
  }
}

async function createThumbnailFile(file: File, options: NormalizedThumbnailOptions): Promise<File | null> {
  if (!options.enabled || !isCompressibleImage(file)) return null

  const url = URL.createObjectURL(file)

  try {
    const image = await getImageElement(url)
    const maxSide = Math.max(image.width, image.height)
    if (!maxSide || maxSide <= options.maxWidthOrHeight) return null

    const scale = options.maxWidthOrHeight / maxSide
    const width = Math.max(1, Math.round(image.width * scale))
    const height = Math.max(1, Math.round(image.height * scale))
    const canvas = document.createElement('canvas')
    const context = canvas.getContext('2d')
    if (!context) return null

    canvas.width = width
    canvas.height = height
    context.drawImage(image, 0, 0, width, height)

    const type = resolveCanvasOutputType(file.type, options.fileType)
    const blob = await canvasToBlob(canvas, type, options.quality)
    if (!blob) return null

    return new File(
      [blob],
      buildVariantFileName(file.name, type, 'thumb'),
      {
        type,
        lastModified: Date.now(),
      },
    )
  } finally {
    URL.revokeObjectURL(url)
  }
}

function getImageElement(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('Failed to load image'))
    image.src = src
  })
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, type, quality)
  })
}

function resolveCanvasOutputType(fileType: string, preferredType?: string): string {
  if (preferredType) return preferredType
  if (fileType === 'image/png' || fileType === 'image/webp') return fileType
  return 'image/jpeg'
}

function buildVariantFileName(fileName: string, mimeType: string, suffix: string): string {
  const baseName = fileName.includes('.') ? fileName.slice(0, fileName.lastIndexOf('.')) : fileName
  const extension = MIME_EXTENSION_MAP[mimeType] ?? '.jpg'
  return `${baseName}_${suffix}${extension}`
}

function isCompressibleImage(file: File): boolean {
  const type = file.type.toLowerCase()
  return type.startsWith('image/') && type !== 'image/gif' && type !== 'image/svg+xml'
}

function defaultKeyFactory(file: File): string {
  return `${getUuid(33)}${getFileExtension(file)}`
}

function getFileExtension(file: File): string {
  const fromType = MIME_EXTENSION_MAP[file.type]
  if (fromType) return fromType

  const fileName = file.name || ''
  const index = fileName.lastIndexOf('.')
  return index >= 0 ? fileName.slice(index).toLowerCase() : ''
}

function notifyProgress(
  callback: StorageBatchUploadOptions['onFileProgress'],
  progress: StorageUploadProgress,
): void {
  callback?.({
    ...progress,
    percent: clampPercent(progress.percent),
  })
}

function clampPercent(percent: number): number {
  if (!Number.isFinite(percent)) return 0
  return Math.max(0, Math.min(100, percent))
}

function uploadFileToQiniu(
  file: File,
  key: string,
  token: string,
  onProgress?: (percent: number) => void,
): Promise<QiniuUploadResponse> {
  return new Promise((resolve, reject) => {
    qiniu.upload(
      file,
      key,
      token,
      {
        fname: file.name || key,
        mimeType: file.type || undefined,
      },
    ).subscribe({
      next(progress) {
        onProgress?.(progress.total.percent)
      },
      error(error) {
        reject(error instanceof Error ? error : new Error('Qiniu upload failed'))
      },
      complete(result) {
        resolve(result as QiniuUploadResponse)
      },
    })
  })
}

function getQiniuKey(result: QiniuUploadResponse, fallbackKey: string): string {
  return typeof result.key === 'string' && result.key ? result.key : fallbackKey
}

async function resolveUploadHash(file: File, result: QiniuUploadResponse): Promise<string> {
  if (typeof result.hash === 'string' && result.hash) return result.hash
  if (typeof result.etag === 'string' && result.etag) return result.etag
  return getSha256Hash(file)
}

async function getSha256Hash(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

function resolveUploadedUrls<TResource>(
  resource: TResource,
  payload: Pick<StorageUploadFinalizePayload, 'key' | 'thumbKey' | 'token'>,
  resolver?: StorageBatchUploadOptions<TResource>['resolveResourceUrls'],
): StorageResolvedUrls<TResource> {
  if (resolver) {
    return resolver(resource, payload)
  }

  const candidate = resource as Record<string, unknown>
  const url = typeof candidate.url === 'string' && candidate.url
    ? candidate.url
    : buildStoragePublicUrl(payload.token.domain, payload.key)

  const thumbUrl = typeof candidate.thumb_url === 'string'
    ? candidate.thumb_url
    : payload.thumbKey
      ? buildStoragePublicUrl(payload.token.domain, payload.thumbKey)
      : null

  return {
    resource,
    url,
    thumbUrl,
  }
}
