// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { UploadPage } from "./UploadPage"

afterEach(cleanup)

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn())
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

function makePdf(name: string) {
  return new File(["%PDF-1.4"], name, { type: "application/pdf" })
}

function selectFiles(input: HTMLElement, files: File[]) {
  fireEvent.change(input, { target: { files } })
}

describe("UploadPage", () => {
  it("renders a pending row for each selected file", () => {
    render(<UploadPage />)

    selectFiles(screen.getByLabelText("Receipt files"), [
      makePdf("receipt-1.pdf"),
      makePdf("receipt-2.pdf"),
    ])

    expect(screen.getByText("receipt-1.pdf")).toBeTruthy()
    expect(screen.getByText("receipt-2.pdf")).toBeTruthy()
    expect(screen.getAllByText("Pending")).toHaveLength(2)
  })

  it("uploads each selected file and shows the final status per row", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "added", receipt_id: "r1", total: "12.34", currency: "EUR" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "duplicate", receipt_id: "r2" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
    render(<UploadPage />)
    selectFiles(screen.getByLabelText("Receipt files"), [
      makePdf("receipt-1.pdf"),
      makePdf("receipt-2.pdf"),
    ])

    fireEvent.click(screen.getByRole("button", { name: /upload receipts/i }))

    await waitFor(() => {
      expect(screen.getByText("receipt-1.pdf").closest("li")?.textContent).toContain("Added")
      expect(screen.getByText("receipt-2.pdf").closest("li")?.textContent).toContain(
        "Already in the store",
      )
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(fetchMock).toHaveBeenNthCalledWith(3, "data/receipts.json")
  })

  it("continues past a failed file and uploads the rest", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: "Only PDF files are supported." }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "added", receipt_id: "r2", total: "5.00", currency: "EUR" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
    render(<UploadPage />)
    selectFiles(screen.getByLabelText("Receipt files"), [
      makePdf("bad.pdf"),
      makePdf("receipt-2.pdf"),
    ])

    fireEvent.click(screen.getByRole("button", { name: /upload receipts/i }))

    await waitFor(() => {
      expect(screen.getByText("bad.pdf").closest("li")?.textContent).toContain(
        "Only PDF files are supported.",
      )
      expect(screen.getByText("receipt-2.pdf").closest("li")?.textContent).toContain("Added")
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(fetchMock).toHaveBeenNthCalledWith(3, "data/receipts.json")
  })

  it("refreshes the shared receipts data once the whole batch finishes", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "added", receipt_id: "r1", total: "12.34", currency: "EUR" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
    render(<UploadPage />)
    selectFiles(screen.getByLabelText("Receipt files"), [makePdf("receipt-1.pdf")])

    fireEvent.click(screen.getByRole("button", { name: /upload receipts/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenNthCalledWith(2, "data/receipts.json")
  })

  it("does not let a failed refresh break the upload flow", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "added", receipt_id: "r1", total: "12.34", currency: "EUR" }),
      })
      .mockRejectedValueOnce(new Error("network down"))
    render(<UploadPage />)
    selectFiles(screen.getByLabelText("Receipt files"), [makePdf("receipt-1.pdf")])

    fireEvent.click(screen.getByRole("button", { name: /upload receipts/i }))

    await waitFor(() => {
      expect(screen.getByText("receipt-1.pdf").closest("li")?.textContent).toContain("Added")
    })
    await waitFor(() => {
      const button = screen.getByRole("button", { name: /upload receipts/i }) as HTMLButtonElement
      expect(button.disabled).toBe(false)
    })
  })

  it("uploads one file at a time, not in parallel", async () => {
    const first = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const second = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = fetch as ReturnType<typeof vi.fn>
    fetchMock
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
    render(<UploadPage />)
    selectFiles(screen.getByLabelText("Receipt files"), [
      makePdf("receipt-1.pdf"),
      makePdf("receipt-2.pdf"),
    ])

    fireEvent.click(screen.getByRole("button", { name: /upload receipts/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(screen.getByText("receipt-1.pdf").closest("li")?.textContent).toContain("Uploading")
    expect(screen.getByText("receipt-2.pdf").closest("li")?.textContent).toContain("Pending")

    first.resolve({
      ok: true,
      json: async () => ({ status: "added", receipt_id: "r1", total: "12.34", currency: "EUR" }),
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    second.resolve({
      ok: true,
      json: async () => ({ status: "added", receipt_id: "r2", total: "5.00", currency: "EUR" }),
    })
    await waitFor(() =>
      expect(screen.getByText("receipt-2.pdf").closest("li")?.textContent).toContain("Added"),
    )
  })
})
