import { useEffect, useState } from "react"

import { WEEK_STARTS } from "@/lib/week"

export function WeekStartSelect({
  value,
  onWeekStart,
}: {
  value: string
  onWeekStart: (day: string) => Promise<void>
}) {
  const [choice, setChoice] = useState(value)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setChoice(value)
  }, [value])

  async function changeStart(next: string) {
    setChoice(next)
    setSaving(true)
    try {
      await onWeekStart(next)
    } catch {
      setChoice(value)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="week-start" className="text-sm text-muted-foreground">
        Week starts
      </label>
      <select
        id="week-start"
        value={choice}
        disabled={saving}
        onChange={(event) => void changeStart(event.target.value)}
        className="h-8 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {WEEK_STARTS.map((day) => (
          <option key={day.id} value={day.id}>
            {day.label}
          </option>
        ))}
      </select>
    </div>
  )
}
