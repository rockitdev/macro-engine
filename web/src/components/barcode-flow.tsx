/**
 * BarcodeFlow
 *
 * Orchestrates the full scan → lookup → confirm → log pipeline as a
 * bottom-sheet.  States:
 *
 *   closed      → sheet is hidden
 *   scanning    → BarcodeScanner is shown inside the sheet
 *   looking-up  → barcode detected, waiting on OFF API
 *   confirm     → OFF returned a product, BarcodeConfirmSheet shown
 *   not-found   → OFF miss — prompt to search manually and close
 *
 * The Scan button in the Composer / toolbar opens this sheet.
 */

import { useState } from "react"

import { ApiError, lookupBarcode } from "@/api"
import { BarcodeConfirmSheet } from "@/components/barcode-confirm-sheet"
import { BarcodeScanner } from "@/components/barcode-scanner"
import {
  BottomSheet,
  BottomSheetContent,
  BottomSheetTitle,
} from "@/components/ui/bottom-sheet"
import { type Meal } from "@/lib/meals"
import type { BarcodeFood } from "@/types"

type FlowState =
  | { kind: "closed" }
  | { kind: "scanning" }
  | { kind: "looking-up"; barcode: string }
  | { kind: "confirm"; food: BarcodeFood }
  | { kind: "not-found"; barcode: string }

interface Props {
  date: string
  meal: Meal
  onLogged: () => void
  /** Drop user into manual search with this query. */
  onFallback: (query: string) => void
}

export function BarcodeFlow({ date, meal, onLogged, onFallback }: Props) {
  const [flow, setFlow] = useState<FlowState>({ kind: "closed" })

  function open() {
    setFlow({ kind: "scanning" })
  }

  function close() {
    setFlow({ kind: "closed" })
  }

  async function handleDetected(barcode: string) {
    setFlow({ kind: "looking-up", barcode })
    try {
      const food = await lookupBarcode(barcode)
      setFlow({ kind: "confirm", food })
    } catch (err) {
      if (err instanceof ApiError && err.message === "product not found") {
        setFlow({ kind: "not-found", barcode })
      } else {
        // Network error or 502 — treat as not found with a note
        setFlow({ kind: "not-found", barcode })
      }
    }
  }

  function handleFallback(barcode: string) {
    close()
    // Pass barcode as the manual-search seed so the user can find it
    onFallback(barcode)
  }

  function handleLogged() {
    close()
    onLogged()
  }

  const isOpen = flow.kind !== "closed"

  const sheetTitle =
    flow.kind === "scanning" ? "Scan barcode"
    : flow.kind === "looking-up" ? "Looking up…"
    : flow.kind === "confirm" ? "Confirm & log"
    : flow.kind === "not-found" ? "Product not found"
    : "Scan barcode"

  return (
    <>
      {/* Trigger button */}
      <button
        type="button"
        onClick={open}
        aria-label="Scan barcode"
        style={{
          font: "inherit", fontSize: 14, fontWeight: 500,
          height: 36, padding: "0 14px", borderRadius: 9,
          border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
          display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer",
        }}
      >
        <BarcodeIcon />
        Scan
      </button>

      <BottomSheet open={isOpen} onOpenChange={(o) => { if (!o) close() }}>
        <BottomSheetContent
          aria-label={sheetTitle}
          header={
            <BottomSheetTitle>{sheetTitle}</BottomSheetTitle>
          }
        >
          {flow.kind === "scanning" && (
            <BarcodeScanner
              onDetected={(code) => void handleDetected(code)}
              onClose={close}
            />
          )}

          {flow.kind === "looking-up" && (
            <p style={{ fontSize: 14, color: "#5f6b65", margin: 0 }}>
              Looking up barcode {flow.barcode}…
            </p>
          )}

          {flow.kind === "confirm" && (
            <BarcodeConfirmSheet
              food={flow.food}
              date={date}
              meal={meal}
              onLogged={handleLogged}
              onFallback={handleFallback}
              onClose={close}
            />
          )}

          {flow.kind === "not-found" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <p style={{ margin: 0, fontSize: 14, color: "#5f6b65" }}>
                Barcode <strong>{flow.barcode}</strong> was not found in Open Food Facts.
                You can search for the food manually instead.
              </p>
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  type="button"
                  onClick={() => handleFallback(flow.barcode)}
                  style={{
                    font: "inherit", fontSize: 14, fontWeight: 500,
                    height: 40, padding: "0 16px", borderRadius: 10,
                    border: "none", background: "#1e6b45", color: "#f6faf7",
                    cursor: "pointer",
                  }}
                >
                  Search manually
                </button>
                <button
                  type="button"
                  onClick={() => setFlow({ kind: "scanning" })}
                  style={{
                    font: "inherit", fontSize: 14, fontWeight: 500,
                    height: 40, padding: "0 16px", borderRadius: 10,
                    border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
                    cursor: "pointer",
                  }}
                >
                  Scan again
                </button>
                <button
                  type="button"
                  onClick={close}
                  style={{
                    font: "inherit", fontSize: 14, fontWeight: 500,
                    height: 40, padding: "0 16px", borderRadius: 10,
                    border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </BottomSheetContent>
      </BottomSheet>
    </>
  )
}

function BarcodeIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9V6a3 3 0 0 1 3-3h2"/>
      <path d="M21 9V6a3 3 0 0 0-3-3h-2"/>
      <path d="M3 15v3a3 3 0 0 0 3 3h2"/>
      <path d="M21 15v3a3 3 0 0 1-3 3h-2"/>
      <line x1="7" y1="8" x2="7" y2="16"/>
      <line x1="10" y1="8" x2="10" y2="16"/>
      <line x1="13" y1="8" x2="13" y2="16"/>
      <line x1="16" y1="8" x2="16" y2="16"/>
    </svg>
  )
}
