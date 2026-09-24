import { useState } from "react"
import { Link } from "react-router-dom"
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { fmtDateTime } from "@/lib/format"
import { filterAndSortReceipts, fmtMoney, num, receipts, totalSaved, type ReceiptSortKey } from "@/lib/receipts"

const COLUMNS: { key: ReceiptSortKey; label: string; align?: "right" }[] = [
  { key: "date", label: "Date" },
  { key: "store", label: "Store" },
  { key: "items", label: "Items", align: "right" },
  { key: "total", label: "Total", align: "right" },
]

const PAGE_SIZES = [10, 25, 50] as const
type PageSize = (typeof PAGE_SIZES)[number]

export function ReceiptsPage() {
  const [query, setQuery] = useState("")
  const [sortKey, setSortKey] = useState<ReceiptSortKey>("date")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<PageSize>(10)

  const grandTotal = receipts.reduce((sum, r) => sum + num(r.total), 0)
  const grandSaved = receipts.reduce((sum, r) => sum + totalSaved(r), 0)
  const rows = filterAndSortReceipts(receipts, query, sortKey, sortDir)

  // Clamped rather than stored directly -- a filter/sort/page-size change
  // can shrink totalPages out from under a stale `page` value, and this
  // avoids needing an effect just to pull it back in range.
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const pageRows = rows.slice((currentPage - 1) * pageSize, currentPage * pageSize)

  function toggleSort(key: ReceiptSortKey) {
    setPage(1)
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir(key === "date" ? "desc" : "asc")
    }
  }

  function handleQueryChange(value: string) {
    setQuery(value)
    setPage(1)
  }

  function changePageSize(size: PageSize) {
    setPageSize(size)
    setPage(1)
  }

  return (
    <>
      <h1 className="text-2xl font-bold">Receipt Radar</h1>
      <p className="mb-6 text-muted-foreground">{receipts.length} receipt(s)</p>

      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-2">
        <Card>
          <CardContent>
            <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Total saved</div>
            <div className="text-2xl font-bold">{fmtMoney(grandSaved)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Total spent</div>
            <div className="text-2xl font-bold">{fmtMoney(grandTotal)}</div>
          </CardContent>
        </Card>
      </div>

      <Input
        placeholder="Filter by store, date, or receipt id..."
        value={query}
        onChange={(e) => handleQueryChange(e.target.value)}
        className="mb-4 max-w-sm"
      />

      <div className="overflow-hidden rounded-[10px]">
        <Table>
          <TableHeader className="bg-thead [&_tr]:border-b-0">
            <TableRow className="hover:bg-thead">
              {COLUMNS.map((col) => (
                <TableHead
                  key={col.key}
                  className={col.align === "right" ? "text-right text-thead-foreground" : "text-thead-foreground"}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort(col.key)}
                    className={`inline-flex items-center gap-1 hover:text-thead-foreground/80 ${col.align === "right" ? "flex-row-reverse" : ""}`}
                  >
                    {col.label}
                    {sortKey === col.key ? (
                      sortDir === "asc" ? (
                        <ArrowUp className="size-3.5" />
                      ) : (
                        <ArrowDown className="size-3.5" />
                      )
                    ) : (
                      <ArrowUpDown className="size-3.5 opacity-40" />
                    )}
                  </button>
                </TableHead>
              ))}
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={5}>{receipts.length === 0 ? "No receipts yet." : "No receipts match your search."}</TableCell>
              </TableRow>
            )}
            {pageRows.map((r, i) => (
              <TableRow key={r.receipt_id} className={i % 2 === 1 ? "bg-muted/60" : ""}>
                <TableCell>{fmtDateTime(r.purchased_at)}</TableCell>
                <TableCell>{r.store.name}</TableCell>
                <TableCell className="text-right tabular-nums">{r.line_items.length}</TableCell>
                <TableCell className="text-right tabular-nums">{fmtMoney(num(r.total), r.currency)}</TableCell>
                <TableCell className="text-right">
                  <Button asChild variant="outline" size="xs">
                    <Link to={`/receipts/${r.receipt_id}`}>Details</Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {rows.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            Showing {(currentPage - 1) * pageSize + 1}–{Math.min(currentPage * pageSize, rows.length)} of {rows.length}
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <span>Per page:</span>
              {PAGE_SIZES.map((size) => (
                <Button
                  key={size}
                  type="button"
                  variant={size === pageSize ? "secondary" : "ghost"}
                  size="xs"
                  onClick={() => changePageSize(size)}
                >
                  {size}
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="icon-xs"
                disabled={currentPage === 1}
                onClick={() => setPage(currentPage - 1)}
                aria-label="Previous page"
              >
                <ChevronLeft />
              </Button>
              <span className="px-2 text-sm text-muted-foreground tabular-nums">
                Page {currentPage} of {totalPages}
              </span>
              <Button
                type="button"
                variant="outline"
                size="icon-xs"
                disabled={currentPage === totalPages}
                onClick={() => setPage(currentPage + 1)}
                aria-label="Next page"
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
