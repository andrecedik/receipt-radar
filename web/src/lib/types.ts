// Mirrors receipt_radar.models — decimals arrive as strings from
// `receipt-radar export --format json` (pydantic's JSON mode) and are parsed to
// numbers here; this is a read-only display layer, not a place that needs
// arbitrary-precision arithmetic.

export interface PriceVerdict {
  median_price: string
  current_price: string
  percent_delta: string
  label: string
  observation_count: number
}

export interface LineItem {
  name: string
  quantity: string
  unit_price: string | null
  total_price: string
  tax_class: string | null
  article_number: string | null
  size_value: string | null
  size_unit: string | null
  price_verdict?: PriceVerdict
}

export interface Store {
  name: string
  street: string | null
  city: string | null
  postal_code: string | null
}

export interface Receipt {
  receipt_id: string
  purchased_at: string
  store: Store
  line_items: LineItem[]
  total: string
  currency: string
  source: string
  source_file: string | null
  // Whole-cart Rabattaktion coupon, pulled out of line_items so it's never
  // mis-attributed to a single item -- see Receipt.threshold_coupon_discount
  // in the backend models. Included in lineItemSum()/totalSaved() below.
  threshold_coupon_discount: string | null
  // Whether web-data actually copied the PDF into public/pdfs/ -- distinct
  // from source_file, which is the ingest-time path and can go stale.
  pdf_available: boolean
}

export interface GrocyProduct {
  id: number
  name: string
}

export interface GrocyPendingItem {
  index: number
  name: string
}

export interface GrocyFailedItem {
  index: number
  name: string
  error: string | null
}

export interface GrocyPendingReceipt {
  receipt_id: string
  purchased_at: string
  store_name: string
  total: string
  currency: string
  unresolved: GrocyPendingItem[]
  failed: GrocyFailedItem[]
}

export interface GrocyLocation {
  id: number
  name: string
}

export interface GrocyQuantityUnit {
  id: number
  name: string
}

export interface GrocyProductDefaults {
  location_id: number
  quantity_unit_id: number
}

export interface GrocySettings {
  connected: boolean
  defaults: GrocyProductDefaults | null
  locations: GrocyLocation[]
  quantity_units: GrocyQuantityUnit[]
}
