/**
 * BarcodeScanner
 *
 * Opens the user's camera, reads a UPC/EAN barcode, and calls onDetected
 * with the raw barcode string.
 *
 * Strategy:
 *   1. BarcodeDetector (native, Chrome/Android) — fastest, no JS payload.
 *   2. @zxing/browser fallback (Safari, Firefox, older Chrome).
 *
 * Handles permission-denied and no-camera states with clear messages.
 * Mobile-first: rear camera preferred via facingMode: environment.
 */

import { useEffect, useRef, useState } from "react"

type ScannerState =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "scanning" }
  | { kind: "denied"; message: string }
  | { kind: "no-camera" }
  | { kind: "error"; message: string }

interface Props {
  onDetected: (barcode: string) => void
  onClose: () => void
}

// Check for native BarcodeDetector
function hasNativeDetector() {
  return typeof window !== "undefined" && "BarcodeDetector" in window
}

export function BarcodeScanner({ onDetected, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number | null>(null)
  const detectedRef = useRef(false)
  const [state, setState] = useState<ScannerState>({ kind: "idle" })

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopStream()
    }
  }, [])

  function stopStream() {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }

  function handleClose() {
    stopStream()
    onClose()
  }

  function handleDetected(code: string) {
    if (detectedRef.current) return
    detectedRef.current = true
    stopStream()
    onDetected(code)
  }

  async function startScanning() {
    detectedRef.current = false
    setState({ kind: "requesting" })

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      })
    } catch (err) {
      const name = err instanceof DOMException ? err.name : ""
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setState({
          kind: "denied",
          message: "Camera permission was denied. Allow camera access in your browser settings and try again.",
        })
      } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        setState({ kind: "no-camera" })
      } else {
        setState({ kind: "error", message: `Could not start camera: ${err instanceof Error ? err.message : String(err)}` })
      }
      return
    }

    streamRef.current = stream
    setState({ kind: "scanning" })

    const video = videoRef.current
    if (!video) { stopStream(); return }
    video.srcObject = stream
    await video.play().catch(() => {/* ignore */})

    if (hasNativeDetector()) {
      startNativeDetection(video)
    } else {
      startZxingDetection(video)
    }
  }

  // ── Native BarcodeDetector ─────────────────────────────────────────────────

  function startNativeDetection(video: HTMLVideoElement) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const detector = new (window as any).BarcodeDetector({
      formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code"],
    })

    async function tick() {
      if (!streamRef.current) return
      if (video.readyState >= video.HAVE_ENOUGH_DATA) {
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const results: any[] = await detector.detect(video)
          if (results.length > 0) {
            handleDetected(results[0].rawValue as string)
            return
          }
        } catch {
          /* ignore decode errors */
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  // ── ZXing fallback ─────────────────────────────────────────────────────────

  async function startZxingDetection(video: HTMLVideoElement) {
    let ZXing: typeof import("@zxing/browser") | null = null
    try {
      ZXing = await import("@zxing/browser")
    } catch {
      setState({ kind: "error", message: "Barcode library failed to load. Try a different browser." })
      stopStream()
      return
    }

    const hints = new Map()
    try {
      const { BarcodeFormat } = await import("@zxing/library")
      hints.set(2 /* DecodeHintType.POSSIBLE_FORMATS */, [
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
        BarcodeFormat.CODE_128,
        BarcodeFormat.CODE_39,
      ])
    } catch {
      /* hints are optional — ZXing will try all formats */
    }

    const reader = new ZXing.BrowserMultiFormatReader(hints)

    async function tick() {
      if (!streamRef.current) return
      if (video.readyState >= video.HAVE_ENOUGH_DATA) {
        try {
          const result = await reader.decodeOnce(video)
          if (result) {
            handleDetected(result.getText())
            return
          }
        } catch {
          /* NotFoundException is normal between frames */
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  const scanning = state.kind === "scanning"

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Video viewfinder */}
      <div
        style={{
          position: "relative",
          width: "100%",
          aspectRatio: "4/3",
          background: "#000",
          borderRadius: 12,
          overflow: "hidden",
          display: scanning ? "block" : "none",
        }}
      >
        <video
          ref={videoRef}
          muted
          playsInline
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
        {/* Aiming reticle */}
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              width: "70%",
              height: 2,
              background: "rgba(30,107,69,.8)",
              boxShadow: "0 0 8px rgba(30,107,69,.6)",
            }}
          />
        </div>
      </div>

      {/* State messages */}
      {state.kind === "idle" && (
        <p style={{ fontSize: 14, color: "#5f6b65", margin: 0 }}>
          Point your camera at the barcode on the packaging.
        </p>
      )}
      {state.kind === "requesting" && (
        <p style={{ fontSize: 14, color: "#5f6b65", margin: 0 }}>Requesting camera access…</p>
      )}
      {state.kind === "scanning" && (
        <p style={{ fontSize: 14, color: "#5f6b65", margin: 0 }}>
          Hold the barcode steady inside the green line.
        </p>
      )}
      {state.kind === "denied" && (
        <p style={{ fontSize: 14, color: "#c2512e", margin: 0 }} role="alert">
          {state.message}
        </p>
      )}
      {state.kind === "no-camera" && (
        <p style={{ fontSize: 14, color: "#c2512e", margin: 0 }} role="alert">
          No camera found on this device.
        </p>
      )}
      {state.kind === "error" && (
        <p style={{ fontSize: 14, color: "#c2512e", margin: 0 }} role="alert">
          {state.message}
        </p>
      )}

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 10 }}>
        {state.kind !== "scanning" && state.kind !== "requesting" && (
          <button
            type="button"
            onClick={() => void startScanning()}
            style={{
              font: "inherit", fontSize: 14, fontWeight: 500,
              height: 40, padding: "0 18px", borderRadius: 10,
              border: "none", background: "#1e6b45", color: "#f6faf7",
              cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 8,
            }}
          >
            <CameraIcon />
            {state.kind === "idle" ? "Open camera" : "Try again"}
          </button>
        )}
        <button
          type="button"
          onClick={handleClose}
          style={{
            font: "inherit", fontSize: 14, fontWeight: 500,
            height: 40, padding: "0 18px", borderRadius: 10,
            border: "1px solid #e3e7e4", background: "#fff", color: "#111814",
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

function CameraIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
      <circle cx="12" cy="13" r="3"/>
    </svg>
  )
}
