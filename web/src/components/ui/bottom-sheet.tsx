/**
 * BottomSheet — a Base UI Dialog restyled as a slide-up sheet.
 *
 * - Anchored to the bottom edge; rounded top corners; grab handle.
 * - Header slot is sticky; body slot scrolls; sheet caps at 88dvh.
 * - On desktop (md+) the sheet is centered at max-w-[560px].
 * - Dimmed backdrop; tap backdrop / press Esc / close button all dismiss.
 * - Base UI Dialog handles: focus-trap, body-scroll-lock, Esc key, aria roles.
 */

import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { cn } from "cn"
import { X } from "lucide-react"

// ─── Root ────────────────────────────────────────────────────────────────────

function BottomSheet({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="bottom-sheet" {...props} />
}

// ─── Trigger ─────────────────────────────────────────────────────────────────

function BottomSheetTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="bottom-sheet-trigger" {...props} />
}

// ─── Close ───────────────────────────────────────────────────────────────────

function BottomSheetClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="bottom-sheet-close" {...props} />
}

// ─── Backdrop ────────────────────────────────────────────────────────────────

function BottomSheetBackdrop({ className, ...props }: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="bottom-sheet-backdrop"
      className={cn(
        "fixed inset-0 z-50 bg-black/40",
        // open/close animations via tw-animate-css data attributes
        "duration-200 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className,
      )}
      {...props}
    />
  )
}

// ─── Content (the sheet panel) ───────────────────────────────────────────────

export interface BottomSheetContentProps extends DialogPrimitive.Popup.Props {
  /** Sticky header rendered above the scrollable body. */
  header?: React.ReactNode
  /** Accessible label for the dialog (required). */
  "aria-label"?: string
  showCloseButton?: boolean
}

function BottomSheetContent({
  className,
  children,
  header,
  showCloseButton = true,
  ...props
}: BottomSheetContentProps) {
  return (
    <DialogPrimitive.Portal>
      <BottomSheetBackdrop />
      <DialogPrimitive.Popup
        data-slot="bottom-sheet-content"
        className={cn(
          // positioning — fixed to viewport bottom, full width, centered on md+
          "fixed bottom-0 left-0 right-0 z-50",
          "mx-auto w-full md:max-w-[560px]",
          // shape
          "rounded-t-2xl bg-popover text-popover-foreground ring-1 ring-foreground/10",
          // cap height so it never overflows
          "max-h-[88dvh] flex flex-col",
          // slide-up / slide-down animations
          "duration-250 ease-out",
          "data-open:animate-in data-open:slide-in-from-bottom data-open:fade-in-0",
          "data-closed:animate-out data-closed:slide-out-to-bottom data-closed:fade-out-0",
          className,
        )}
        {...props}
      >
        {/* ── Grab handle ─────────────────────────────────────────────────── */}
        <div
          aria-hidden="true"
          className="flex justify-center pt-3 pb-1 shrink-0"
        >
          <div className="h-1 w-10 rounded-full bg-foreground/20" />
        </div>

        {/* ── Close button (top-right, always 44×44 px) ────────────────── */}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="bottom-sheet-close"
            className="absolute top-3 right-3 flex h-11 w-11 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-mist hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </DialogPrimitive.Close>
        )}

        {/* ── Sticky header ────────────────────────────────────────────────── */}
        {header && (
          <div
            data-slot="bottom-sheet-header"
            className="shrink-0 border-b px-5 py-3"
          >
            {header}
          </div>
        )}

        {/* ── Scrollable body ──────────────────────────────────────────────── */}
        <div
          data-slot="bottom-sheet-body"
          className="flex-1 overflow-y-auto overscroll-contain px-5 py-4"
        >
          {children}
        </div>
      </DialogPrimitive.Popup>
    </DialogPrimitive.Portal>
  )
}

// ─── Title / Description passthroughs ────────────────────────────────────────

function BottomSheetTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="bottom-sheet-title"
      className={cn("font-heading text-base font-medium leading-none", className)}
      {...props}
    />
  )
}

function BottomSheetDescription({ className, ...props }: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="bottom-sheet-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  BottomSheet,
  BottomSheetBackdrop,
  BottomSheetClose,
  BottomSheetContent,
  BottomSheetDescription,
  BottomSheetTitle,
  BottomSheetTrigger,
}
