import { formatGrams, formatKcal } from "@/lib/format"
import { WEEK_STARTS } from "@/lib/week"
import type { Month, WeekDay } from "@/types"

// Build a calendar grid starting from the first day of the display (monday-aligned or week_starts_on-aligned)
function buildCalendarGrid(month: Month): (WeekDay | { date: string; filler: true })[] {
  // Collect all days from all weeks
  const allDays = month.weeks.flatMap((w) => w.days)
  return allDays
}

function statusDot(day: WeekDay, today: string): { color: string; kcalColor: string } | null {
  if (day.date > today) return null
  if (!day.targets || day.under == null) return null
  const kcalTarget = day.targets.kcal
  if (kcalTarget == null) return null
  const isToday = day.date === today
  if (isToday) return { color: "#b07d12", kcalColor: "#111814" } // in progress
  const kcalOver = day.under.kcal != null && day.under.kcal < 0
  if (kcalOver) return { color: "#c2512e", kcalColor: "#b03d17" }
  return { color: "#1e6b45", kcalColor: "#111814" }
}

export function MonthView({
  month,
  onSelect,
  onWeekStart,
}: {
  month: Month
  onSelect: (date: string) => void
  onWeekStart: (day: string) => Promise<void>
}) {
  const calDays = buildCalendarGrid(month)

  // Summary totals from month.month
  const carbTarget = month.month.target.carb_g
  const fatTarget = month.month.target.fat_g
  const proteinTarget = month.month.target.protein_g
  const proteinEaten = month.month.eaten.protein_g
  const carbLeft = month.month.left.carb_g
  const fatLeft = month.month.left.fat_g
  const carbLabel = carbTarget != null
    ? (carbLeft != null && carbLeft < 0 ? `${formatGrams(Math.abs(carbLeft))} g over` : `${formatGrams(Math.abs(carbLeft ?? 0))} g under`)
    : `${formatGrams(month.month.eaten.carb_g)} g`
  const fatLabel = fatTarget != null
    ? (fatLeft != null && fatLeft < 0 ? `${formatGrams(Math.abs(fatLeft))} g over` : `${formatGrams(Math.abs(fatLeft ?? 0))} g under`)
    : `${formatGrams(month.month.eaten.fat_g)} g`

  // Day-of-week headers
  const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

  return (
    <div style={{ marginTop: 24 }}>
      {/* Week start picker */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div style={{ position: "relative" }}>
          <select
            aria-label="Week starts on"
            value={month.week_starts_on}
            onChange={(e) => void onWeekStart(e.target.value)}
            style={{
              appearance: "none", height: 32, paddingLeft: 10, paddingRight: 28, fontSize: 13, fontWeight: 500,
              border: "1px solid #e3e7e4", borderRadius: 8, background: "#fff", color: "#111814",
              cursor: "pointer", outline: "none",
            }}
          >
            {WEEK_STARTS.map((s) => <option key={s.id} value={s.id}>Week starts {s.label}</option>)}
          </select>
          <svg aria-hidden="true" style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 16 }}>
        <div className="me-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: "#5f6b65" }}>Carbs so far</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600, color: fatLeft != null && carbLeft != null && carbLeft < 0 ? "#8a3518" : "#6b4e0b" }}>
            {carbLabel}
          </div>
        </div>
        <div className="me-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: "#5f6b65" }}>Fat so far</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600, color: fatLeft != null && fatLeft < 0 ? "#8a3518" : "#6b4e0b" }}>
            {fatLabel}
          </div>
        </div>
        <div className="me-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: "#5f6b65" }}>Protein so far</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600 }}>
            {formatGrams(proteinEaten)}{" "}
            {proteinTarget != null && (
              <span style={{ fontSize: 16, color: "#5f6b65", fontWeight: 500 }}>/ {formatGrams(proteinTarget)} g</span>
            )}
          </div>
        </div>
      </div>

      {/* Calendar */}
      <section className="me-card" style={{ marginTop: 20, padding: 20 }}>
        {/* Day-of-week headers */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8, fontSize: 13, color: "#5f6b65" }}>
          {DOW_LABELS.map((d) => <span key={d}>{d}</span>)}
        </div>

        {/* Calendar grid */}
        <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8 }}>
          {calDays.map((dayItem) => {
            const day = dayItem as WeekDay
            const isOutside = day.date < month.start || day.date > month.end
            const isToday = day.date === month.today
            const isFuture = day.date > month.today
            const dot = statusDot(day, month.today)
            const dayNum = parseInt(day.date.split("-")[2])

            return (
              <button
                key={day.date}
                type="button"
                onClick={() => onSelect(day.date)}
                aria-current={isToday ? "date" : undefined}
                style={{
                  all: "unset", boxSizing: "border-box",
                  display: "flex", flexDirection: "column", gap: 6,
                  padding: 10, borderRadius: 10,
                  border: isToday ? "1px solid #1e6b45" : "1px solid #eef1ef",
                  minHeight: 84, cursor: "pointer",
                  background: isToday ? "#f2f8f4" : "transparent",
                  opacity: isOutside ? 0.45 : 1,
                  transition: "border-color .1s",
                }}
                onMouseOver={(e) => { if (!isToday) e.currentTarget.style.borderColor = "#cfd8d2" }}
                onMouseOut={(e) => { e.currentTarget.style.borderColor = isToday ? "#1e6b45" : "#eef1ef" }}
              >
                <span
                  className="num"
                  style={{
                    fontSize: 13,
                    fontWeight: isToday ? 600 : isFuture || isOutside ? 400 : 500,
                    color: isToday ? "#1e6b45" : isFuture ? "#5f6b65" : "#111814",
                  }}
                >
                  {dayNum}
                </span>
                {dot && (
                  <span
                    className="num"
                    style={{ fontSize: 12, color: dot.kcalColor }}
                  >
                    {formatKcal(day.eaten.kcal)}
                  </span>
                )}
                {dot && (
                  <span style={{ height: 4, borderRadius: 99, background: dot.color }} />
                )}
              </button>
            )
          })}
        </div>

        {/* Legend */}
        <div style={{ marginTop: 14, display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "#5f6b65" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 14, height: 4, borderRadius: 99, background: "#1e6b45" }} />
            Within target
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 14, height: 4, borderRadius: 99, background: "#c2512e" }} />
            Over target
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 14, height: 4, borderRadius: 99, background: "#b07d12" }} />
            In progress
          </span>
        </div>
      </section>
    </div>
  )
}
