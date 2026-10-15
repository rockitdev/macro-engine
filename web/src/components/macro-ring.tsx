import { formatGrams, formatKcal } from "@/lib/format"
import type { Day } from "@/types"

const MACROS = [
  { key: "protein_g" as const, label: "Protein", color: "#1e6b45" },
  { key: "carb_g"   as const, label: "Carbs",   color: "#b07d12" },
  { key: "fat_g"    as const, label: "Fat",      color: "#c2512e" },
  { key: "fiber_g"  as const, label: "Fiber",    color: "#3f4a45" },
]

// SVG ring params
const R = 72
const CX = 84
const CY = 84
const CIRC = 2 * Math.PI * R  // ≈ 452.4

export function MacroRing({ day }: { day: Day }) {
  const eaten = day.eaten.kcal
  const target = day.targets?.kcal ?? null
  const left = day.remaining?.kcal ?? null
  const over = left != null && left < 0

  const progress = target && target > 0
    ? Math.min(1, eaten / target)
    : 0
  const dashFilled = progress * CIRC
  const dashRest = CIRC - dashFilled

  return (
    <section
      className="me-card"
      style={{ padding: 24, display: "flex", flexWrap: "wrap", gap: 28, alignItems: "center" }}
      aria-label="Today's nutrition summary"
    >
      {/* Kcal ring */}
      <div style={{ position: "relative", width: 168, height: 168, flex: "none" }}>
        <svg
          role="img"
          aria-label={target
            ? `${formatKcal(eaten)} of ${formatKcal(target)} kcal eaten`
            : `${formatKcal(eaten)} kcal eaten`}
          width="168"
          height="168"
          viewBox="0 0 168 168"
        >
          <circle cx={CX} cy={CY} r={R} fill="none" stroke="#eef1ef" strokeWidth="14" />
          {progress > 0 && (
            <circle
              cx={CX} cy={CY} r={R}
              fill="none"
              stroke={over ? "#c2512e" : "#1e6b45"}
              strokeWidth="14"
              strokeLinecap="round"
              strokeDasharray={`${dashFilled} ${dashRest}`}
              transform="rotate(-90 84 84)"
            />
          )}
        </svg>
        <div
          style={{
            position: "absolute", inset: 0, display: "flex",
            flexDirection: "column", alignItems: "center", justifyContent: "center",
          }}
        >
          {left != null ? (
            <>
              <div
                className="num"
                style={{ fontSize: 38, fontWeight: 600, letterSpacing: "-.03em", lineHeight: 1, color: over ? "#c2512e" : "#111814" }}
              >
                {formatKcal(Math.abs(left))}
              </div>
              <div style={{ marginTop: 4, fontSize: 13, color: "#5f6b65" }}>{over ? "kcal over" : "kcal left"}</div>
            </>
          ) : (
            <>
              <div className="num" style={{ fontSize: 32, fontWeight: 600, letterSpacing: "-.03em", lineHeight: 1 }}>
                {formatKcal(eaten)}
              </div>
              <div style={{ marginTop: 4, fontSize: 13, color: "#5f6b65" }}>kcal eaten</div>
            </>
          )}
        </div>
      </div>

      {/* Eaten / Target + macro bars */}
      <div style={{ flex: "1 1 260px", minWidth: 0 }}>
        {target != null && (
          <div style={{ display: "flex", gap: 24 }}>
            <div>
              <div style={{ fontSize: 13, color: "#5f6b65" }}>Eaten</div>
              <div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{formatKcal(eaten)}</div>
            </div>
            <div>
              <div style={{ fontSize: 13, color: "#5f6b65" }}>Target</div>
              <div className="num" style={{ fontSize: 20, fontWeight: 600 }}>{formatKcal(target)}</div>
            </div>
          </div>
        )}

        <div style={{ marginTop: target != null ? 20 : 0, display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "16px 20px" }}>
          {MACROS.map(({ key, label, color }) => {
            const goal   = day.targets?.[key] ?? null
            const have   = day.eaten[key]
            const remain = day.remaining?.[key] ?? null
            const isOver = remain != null && remain < 0
            const width  = goal != null && goal > 0 ? Math.min(100, (have / goal) * 100) : 0

            return (
              <div key={key}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
                    {label}
                  </span>
                  <span className="num" style={{ color: isOver ? "#b03d17" : "#5f6b65" }}>
                    {remain == null
                      ? `${formatGrams(have)} g`
                      : isOver
                        ? `${formatGrams(Math.abs(remain))} g over`
                        : `${formatGrams(remain)} g left`}
                  </span>
                </div>
                <div style={{ marginTop: 6, height: 8, borderRadius: 99, background: "#eef1ef", overflow: "hidden" }}>
                  {goal != null && (
                    <div
                      style={{
                        width: `${width}%`, height: "100%",
                        background: isOver ? "#c2512e" : color,
                        borderRadius: 99,
                      }}
                    />
                  )}
                </div>
                {goal != null && (
                  <div className="num" style={{ marginTop: 4, fontSize: 12, color: "#5f6b65" }}>
                    {formatGrams(have)} / {formatGrams(goal)} g
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
