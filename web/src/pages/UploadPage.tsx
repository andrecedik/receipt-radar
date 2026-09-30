import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { refreshReceipts } from "@/lib/receipts"

type RowStatus =
  | { kind: "pending" }
  | { kind: "uploading" }
  | { kind: "added"; total: string; currency: string }
  | { kind: "duplicate" }
  | { kind: "error"; message: string }

type UploadRow = {
  file: File
  status: RowStatus
}

export function UploadPage() {
  const [rows, setRows] = useState<UploadRow[]>([])
  const [uploading, setUploading] = useState(false)

  function handleSelect(fileList: FileList | null) {
    const files = fileList ? Array.from(fileList) : []
    setRows(files.map((file) => ({ file, status: { kind: "pending" } })))
  }

  function setRowStatus(index: number, status: RowStatus) {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, status } : row)))
  }

  async function uploadFile(file: File): Promise<RowStatus> {
    const body = new FormData()
    body.append("file", file)

    try {
      const res = await fetch("/api/upload", { method: "POST", body })
      const data = await res.json()
      if (!res.ok) {
        return { kind: "error", message: data.detail ?? "Upload failed." }
      }
      if (data.status === "duplicate") {
        return { kind: "duplicate" }
      }
      return { kind: "added", total: data.total, currency: data.currency }
    } catch {
      return { kind: "error", message: "Could not reach the server." }
    }
  }

  async function handleUpload() {
    setUploading(true)
    // Sequential, not Promise.all: a self-hosted/NAS server (CONTEXT.md's
    // Web Upload) shouldn't take N concurrent parse_pdf + full-store
    // export_web_data calls from one batch. Continuing past a failed file
    // (rather than aborting) matches watch.py's scan_once.
    for (let i = 0; i < rows.length; i++) {
      setRowStatus(i, { kind: "uploading" })
      const status = await uploadFile(rows[i].file)
      setRowStatus(i, status)
    }
    try {
      // So the Receipts page shows new uploads without a manual reload --
      // it otherwise only reads `receipts` once, at app boot.
      await refreshReceipts()
    } catch {
      // Best-effort: every row's own status is already shown above, so a
      // failed refresh just means a manual reload is still needed, same as
      // before this existed -- not worth surfacing as an upload error.
    }
    setUploading(false)
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-6">
        <input
          type="file"
          accept="application/pdf,image/png,image/jpeg"
          multiple
          aria-label="Receipt files"
          disabled={uploading}
          onChange={(e) => handleSelect(e.target.files)}
        />
        <p className="text-sm text-muted-foreground">
          Kaufland, REWE and EDEKA receipts as PDFs (Kaufland from July 2024
          onward; older ones are image-only PDFs and will be rejected). Lidl
          receipts as the PNG screenshot the Lidl app exports.
        </p>
        <Button onClick={handleUpload} disabled={rows.length === 0 || uploading}>
          {uploading ? "Uploading..." : "Upload receipts"}
        </Button>
        {rows.length > 0 && (
          <ul className="flex flex-col gap-1 text-sm">
            {rows.map((row, i) => (
              <li key={i} className="flex justify-between gap-4">
                <span className="truncate">{row.file.name}</span>
                <RowStatusLabel status={row.status} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function RowStatusLabel({ status }: { status: RowStatus }) {
  switch (status.kind) {
    case "pending":
      return <span className="text-muted-foreground">Pending</span>
    case "uploading":
      return <span className="text-muted-foreground">Uploading...</span>
    case "added":
      return (
        <span className="text-muted-foreground">
          Added — {status.total} {status.currency}
        </span>
      )
    case "duplicate":
      return <span className="text-muted-foreground">Already in the store</span>
    case "error":
      return <span className="text-destructive">{status.message}</span>
  }
}
