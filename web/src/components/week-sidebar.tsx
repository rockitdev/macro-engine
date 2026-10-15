import { formatGrams, formatSpan, weekdayShort } from "@/lib/format"
import { carbDayLabel } from "@/lib/week"
import { WEEK_STARTS } from "@/lib/week"
import type { Week } from "@/types"

function ChevDown() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  )
}

export function WeekSidebar({
  week,
  onSelect,
  onWeekStart,
}: {
  week: Week
  onSelect: (date: string) => void
  onWeekStart: (day: string) => Promise<void>
}) {
  const { saved, room_today } = week
  const carbSaved = saved?.carb_g ?? 0
  const fatSaved = saved?.fat_g ?? 0
  const proteinEaten = week.week.eaten.protein_g
  const proteinTarget = week.week.target.protein_g
  const roomCarbs = room_today?.carb_g ?? 0
  const roomFat = room_today?.fat_g ?? 0
  return (
    <section className="me-card" style={{ padding: 20 }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>This week</h2>
          <div style={{ fontSize: 13, color: "#5f6b65", marginTop: 2 }}>
            {formatSpan(week.start, week.end)}
          </div>
        </div>
        <div style={{ position: "relative" }}>
          <select
            aria-label="Week starts on"
            value={week.week_starts_on}
            onChange={(e) => void onWeekStart(e.target.value)}
            style={{
              appearance: "none", height: 30, paddingLeft: 10, paddingRight: 24, fontSize: 13, fontWeight: 500,
              border: "1px solid #e3e7e4", borderRadius: 8, background: "#fff", color: "#111814",
              cursor: "pointer", outline: "none",
            }}
          >
            {WEEK_STARTS.map((s) => (
              <option key={s.id} value={s.id}>Starts {s.label}</option>
            ))}
          </select>
          <ChevDown />
        </div>
      </div>

      {/* Saved carbs / fat cards */}
      {(carbSaved !== 0 || fatSaved !== 0) && (
        <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
          <div style={{ padding: 12, borderRadius: 10, background: "#fbf6ea" }}>
            <div style={{ fontSize: 13, color: "#6b4e0b" }}>{carbSaved >= 0 ? "Carbs saved" : "Carbs over"}</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 600, color: "#6b4e0b" }}>{formatGrams(Math.abs(carbSaved))} g</div>
          </div>
          <div style={{ padding: 12, borderRadius: 10, background: "#fbeee9" }}>
            <div style={{ fontSize: 13, color: "#8a3518" }}>{fatSaved >= 0 ? "Fat saved" : "Fat over"}</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 600, color: "#8a3518" }}>{formatGrams(Math.abs(fatSaved))} g</div>
          </div>
        </div>
      )}

      {/* Room today banner */}
      {room_today && (roomCarbs > 0 || roomFat > 0) && (
        <p style={{ margin: "14px 0 0", fontSize: 14, lineHeight: 1.5 }}>
          You can have{" "}
          {roomCarbs > 0 && <><strong className="num">{formatGrams(roomCarbs)} g</strong> of carbs</>}
          {roomCarbs > 0 && roomFat > 0 && " and "}
          {roomFat > 0 && <><strong className="num">{formatGrams(roomFat)} g</strong> of fat</>}
          {" "}today and still finish the week on target.
        </p>
      )}

      {/* Day sparkbars */}
      <ol
        aria-label="Carbs eaten each day this week"
        style={{ listStyle: "none", margin: "18px 0 0", padding: 0, display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 4 }}
      >
        {week.days.map((day) => {
          const future = day.date > week.today
          const goal = day.targets?.carb_g
          const showTrack = !future && goal != null
          const over = goal != null && day.eaten.carb_g > goal
          const ratio = goal != null && goal > 0 ? Math.min(1, day.eaten.carb_g / goal) : 0
          const isToday = day.date === week.today
          const isSelected = day.date === week.focus
          return (
            <li key={day.date}>
              <button
                type="button"
                aria-pressed={isSelected}
                aria-current={isToday ? "date" : undefined}
                aria-label={carbDayLabel(day, week.today)}
                onClick={() => onSelect(day.date)}
                style={{
                  all: "unset", boxSizing: "border-box", width: "100%",
                  display: "flex", flexDirection: "column", alignItems: "center",
                  gap: 6, padding: "6px 0", borderRadius: 8, cursor: "pointer",
                  background: isToday ? "#e7f2ec" : "transparent",
                }}
              >
                <span
                  style={{
                    height: 64, width: 10, borderRadius: 99,
                    background: showTrack ? "#eef1ef" : "transparent",
                    border: future ? "1px dashed #d5dbd7" : "none",
                    boxSizing: "border-box",
                    display: "flex", alignItems: "flex-end",
                    overflow: "hidden",
                  }}
                >
                  {showTrack && ratio > 0 && (
                    <span
                      style={{
                        width: "100%", borderRadius: 99,
                        background: over ? "#c2512e" : "#b07d12",
                        height: `${ratio * 100}%`,
                      }}
                    />
                  )}
                </span>
                <span
                  style={{
                    fontSize: 12,
                    color: isToday ? "#1e6b45" : "#5f6b65",
                    fontWeight: isToday ? 600 : 400,
                  }}
                >
                  {weekdayShort(day.date)}
                </span>
              </button>
            </li>
          )
        })}
      </ol>

      {/* Protein bar */}
      {proteinTarget != null && (
        <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid #eef1ef" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>Protein this week</span>
            <span className="num" style={{ color: "#5f6b65" }}>
              {formatGrams(proteinEaten)} / {formatGrams(proteinTarget)} g
            </span>
          </div>
          <div style={{ marginTop: 6, height: 6, borderRadius: 99, background: "#eef1ef", overflow: "hidden" }}>
            <div style={{
              height: "100%", borderRadius: 99, background: "#1e6b45",
              width: `${Math.min(100, (proteinEaten / proteinTarget) * 100)}%`,
            }} />
          </div>
        </div>
      )}
    </section>
  )
}
