// Mirrors backend/app/services/ingestion/detector.py's EXTENSION_MAP — kept
// as a client-side pre-check only; the backend re-validates and remains the
// source of truth (a mismatch here just means a slower round-trip, not a
// security gap).
export const ALLOWED_EXTENSIONS = [
  '.pdf', '.docx', '.pptx', '.html', '.htm', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp',
  '.md', '.markdown', '.txt', '.xlsx', '.xls', '.csv', '.json', '.xml', '.sql',
  '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.go', '.rs', '.c', '.h', '.cpp', '.cc', '.hpp', '.rb', '.php', '.cs',
]

// Mirrors .env's MAX_UPLOAD_SIZE_MB — update alongside it if that changes.
export const MAX_UPLOAD_SIZE_MB = 100

export function validateUploadFile(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `"${ext}" isn't a supported file type`
  }
  if (file.size > MAX_UPLOAD_SIZE_MB * 1024 * 1024) {
    return `File exceeds the ${MAX_UPLOAD_SIZE_MB}MB limit`
  }
  return null
}
