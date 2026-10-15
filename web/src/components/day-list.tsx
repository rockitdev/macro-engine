import { formatDayLabel } from "@/lib/format"
import { dayStats } from "@/lib/week"
import type { WeekDay } from "@/types"

export function DayList({
  days,
  today,
  focus,
  monthStart,
  monthEnd,
  onSelect,
}: {
  days: WeekDay[]
  today: string
  focus: string
  monthStart?: string
  monthEnd?: string
  onSelect: (date: string) => void
}) {
  return (
    <ul className="mt-2 divide-y divide-border">
      {days.map((day) => {
        const outside =
          monthStart != null && monthEnd != null && (day.date < monthStart || day.date > monthEnd)
        const parts = dayStats(day, today)
        const label = formatDayLabel(day.date, outside)
        const selected = day.date === focus
        const isToday = day.date === today
        return (
          <li key={day.date}>
            <button
              type="button"
              onClick={() => onSelect(day.date)}
              aria-current={isToday ? "date" : undefined}
              aria-pressed={selected}
              className="flex w-full flex-col gap-1 py-3 text-left outline-none hover:bg-mist focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <span
                className={`font-medium ${
                  selected
                    ? "underline decoration-spinach decoration-2 underline-offset-4"
                    : isToday
                      ? "text-spinach"
                      : outside
                        ? "text-muted-foreground"
                        : ""
                }`}
              >
                {label}
              </span>
              <span className="text-sm">
                {parts.map((part, index) => (
                  <span key={part.text} className={part.over ? "text-roast" : "text-muted-foreground"}>
                    {index > 0 ? ", " : ""}
                    {part.text}
                  </span>
                ))}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
