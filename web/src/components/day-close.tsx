import { useState } from "react"

import { Button } from "@/components/ui/button"
import { weekdayName } from "@/lib/format"
import type { Day } from "@/types"

/**
 * "Day complete" toggle. Only days marked complete feed the week bank, so a
 * forgotten meal can't show up as extra room later in the week.
 */
export function DayClose({
  day,
  today,
  onToggle,
}: {
  day: Day
  today: string
  onToggle: (date: string, complete: boolean) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  if (day.date > today) return null
  const name = day.date === today ? "today" : weekdayName(day.date)

  async function toggle(next: boolean) {
    setBusy(true)
    try {
      await onToggle(day.date, next)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      className="me-card"
      aria-label="Day complete"
      style={{ padding: "14px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
    >
      {day.complete ? (
        <>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--me-green-text)" }}>
              <span aria-hidden="true">✓ </span>Day complete
            </div>
            <div style={{ fontSize: 13, color: "var(--me-muted)", marginTop: 2 }}>
              It counts toward this week's saved carbs and fat.
            </div>
          </div>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => void toggle(false)}>
            Reopen
          </Button>
        </>
      ) : (
        <>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              Logged everything for {name}?
            </div>
            <div style={{ fontSize: 13, color: "var(--me-muted)", marginTop: 2 }}>
              Only complete days count toward this week's saved carbs and fat.
            </div>
          </div>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void toggle(true)}>
            Mark day complete
          </Button>
        </>
      )}
    </section>
  )
}
