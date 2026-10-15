import { formatGrams, formatKcal, weekdayName } from "@/lib/format"
import { WEEK_STARTS } from "@/lib/week"
import type { Week, WeekDay } from "@/types"

function InfoIcon() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1e6b45" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
    </svg>
  )
}

function MacroBars({ day, today }: { day: WeekDay; today: string }) {
  if (day.date > today) {
    return <span style={{ fontSize: 13, color: "#5f6b65" }}>Later</span>
  }
  const macros = [
    { key: "protein_g" as const, label: "P", color: "#1e6b45", target: day.targets?.protein_g },
    { key: "carb_g"   as const, label: "C", color: "#b07d12", target: day.targets?.carb_g },
    { key: "fat_g"    as const, label: "F", color: "#c2512e", target: day.targets?.fat_g },
  ]
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 12 }}>
      {macros.map(({ key, label, color, target }) => {
        const eaten = day.eaten[key]
        const under = day.under?.[key]
        const over = under != null && under < 0
        const width = target != null && target > 0
          ? Math.min(100, (eaten / target) * 100)
          : eaten > 0 ? 100 : 0
        return (
          <div key={key}>
            <div className="num" style={{ fontSize: 12, color: over ? "#b03d17" : "#5f6b65", fontWeight: over ? 500 : 400 }}>
              {over
                ? `${label} ${formatGrams(Math.abs(under!))} g over`
                : target != null
                  ? `${label} ${formatGrams(eaten)} / ${formatGrams(target)}`
                  : `${label} ${formatGrams(eaten)}`}
            </div>
            <div style={{ marginTop: 4, height: 6, borderRadius: 99, background: "#eef1ef", overflow: "hidden" }}>
              <div style={{ width: `${width}%`, height: "100%", background: over ? "#c2512e" : color }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function WeekView({
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
  const roomCarbs = room_today?.carb_g ?? 0
  const roomFat = room_today?.fat_g ?? 0
  const proteinEaten = week.week.eaten.protein_g
  const proteinTarget = week.week.target.protein_g

  return (
    <div style={{ marginTop: 24 }}>
      {/* Week start picker */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div style={{ position: "relative" }}>
          <select
            aria-label="Week starts on"
            value={week.week_starts_on}
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
          <div style={{ fontSize: 13, color: "#5f6b65" }}>{carbSaved >= 0 ? "Carbs saved" : "Carbs over"}</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-.02em", color: "#6b4e0b" }}>
            {formatGrams(Math.abs(carbSaved))} g
          </div>
          <div style={{ fontSize: 12, color: "#5f6b65" }}>from earlier this week</div>
        </div>
        <div className="me-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: "#5f6b65" }}>{fatSaved >= 0 ? "Fat saved" : "Fat over"}</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-.02em", color: "#8a3518" }}>
            {formatGrams(Math.abs(fatSaved))} g
          </div>
          <div style={{ fontSize: 12, color: "#5f6b65" }}>from earlier this week</div>
        </div>
        <div className="me-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: "#5f6b65" }}>Protein</div>
          <div className="num" style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-.02em" }}>
            {formatGrams(proteinEaten)}{" "}
            {proteinTarget != null && (
              <span style={{ fontSize: 16, color: "#5f6b65", fontWeight: 500 }}>/ {formatGrams(proteinTarget)} g</span>
            )}
          </div>
          {proteinTarget != null && (
            <div style={{ marginTop: 8, height: 6, borderRadius: 99, background: "#eef1ef", overflow: "hidden" }}>
              <div style={{ width: `${Math.min(100, (proteinEaten / proteinTarget) * 100)}%`, height: "100%", background: "#1e6b45" }} />
            </div>
          )}
        </div>
      </div>

      {/* Room today banner */}
      {room_today && (roomCarbs > 0 || roomFat > 0) && (
        <div
          className="me-card"
          style={{ marginTop: 16, padding: "14px 18px", display: "flex", gap: 12, alignItems: "center", background: "#f2f8f4", borderColor: "#cfe3d7" }}
        >
          <InfoIcon />
          <p style={{ margin: 0, fontSize: 14 }}>
            You can have{" "}
            {roomCarbs > 0 && <><strong className="num">{formatGrams(roomCarbs)} g</strong> of carbs</>}
            {roomCarbs > 0 && roomFat > 0 && " and "}
            {roomFat > 0 && <><strong className="num">{formatGrams(roomFat)} g</strong> of fat</>}
            {" "}today and still finish the week on target.
          </p>
        </div>
      )}

      {/* Day table */}
      <section className="me-card" style={{ marginTop: 20, overflow: "hidden" }}>
        {/* Column headers */}
        <div style={{
          padding: "14px 20px",
          display: "grid",
          gridTemplateColumns: "120px minmax(0,1fr) 90px",
          gap: 16,
          fontSize: 12, fontWeight: 500, textTransform: "uppercase", letterSpacing: ".05em",
          color: "#5f6b65", background: "#f7f8f7",
        }}>
          <span>Day</span>
          <span>Macros</span>
          <span style={{ textAlign: "right" }}>kcal</span>
        </div>

        {/* Day rows */}
        {week.days.map((day) => {
          const isToday = day.date === week.today
          const isFuture = day.date > week.today
          return (
            <button
              key={day.date}
              type="button"
              onClick={() => onSelect(day.date)}
              aria-current={isToday ? "date" : undefined}
              style={{
                all: "unset", boxSizing: "border-box", width: "100%",
                display: "grid", gridTemplateColumns: "120px minmax(0,1fr) 90px",
                gap: 16, alignItems: "center", padding: "14px 20px",
                borderTop: "1px solid #eef1ef",
                background: isToday ? "#f2f8f4" : "transparent",
                cursor: "pointer",
              }}
              onMouseOver={(e) => { if (!isToday) e.currentTarget.style.background = "#f7f8f7" }}
              onMouseOut={(e) => { e.currentTarget.style.background = isToday ? "#f2f8f4" : "transparent" }}
            >
              <span>
                <span style={{
                  display: "flex", gap: 6, alignItems: "center",
                  fontWeight: isToday ? 600 : 500,
                  color: isToday ? "#1e6b45" : isFuture ? "#5f6b65" : "#111814",
                }}>
                  {weekdayName(day.date)}
                  {isToday && (
                    <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", background: "#1e6b45", color: "#fff", padding: "1px 6px", borderRadius: 99 }}>
                      Today
                    </span>
                  )}
                </span>
                <span style={{ display: "block", fontSize: 12, color: "#5f6b65" }}>
                  {new Date(day.date + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </span>
              </span>
              <MacroBars day={day} today={week.today} />
              <span className="num" style={{ textAlign: "right", fontWeight: 500, color: isFuture ? "#5f6b65" : "#111814" }}>
                {isFuture ? "—" : formatKcal(day.eaten.kcal)}
              </span>
            </button>
          )
        })}
      </section>
    </div>
  )
}
