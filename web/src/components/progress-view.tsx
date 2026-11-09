import { useEffect, useMemo, useState } from "react"

import { ApiError, checkIn, getProgress, logMetrics, planTargets, saveProfile } from "@/api"
import { Field } from "@/components/field"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { formatLong } from "@/lib/format"
import type { Activity, CheckIn, MacroTargets, Plan, Progress } from "@/types"

const LB = 2.20462

function toUnit(kg: number | null | undefined, units: "kg" | "lb") {
  if (kg == null) return null
  return units === "lb" ? kg * LB : kg
}

function fmt(n: number | null | undefined, digits = 1) {
  return n == null ? "—" : n.toFixed(digits)
}

function signed(n: number | null | undefined, digits = 1) {
  if (n == null) return "—"
  return `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n).toFixed(digits)}`
}

const PHASE_LABEL: Record<string, string> = {
  cut: "Fat-loss phase",
  maintenance: "Maintenance",
  reverse: "Reverse diet",
  diet_break: "Diet break",
}

const STATUS_TONE: Record<string, "good" | "warn" | "neutral"> = {
  on_track: "good",
  reverse_step: "good",
  first_week: "neutral",
  collecting: "neutral",
  slow: "neutral",
  on_break: "neutral",
  fast: "warn",
  too_fast: "warn",
  plateau: "warn",
  plateau_adherence: "warn",
  plateau_confounded: "neutral",
  plateau_exhausted: "warn",
  gaining_fast: "warn",
  rising_streak: "warn",
  drifting_up: "warn",
  drifting_down: "warn",
}

// ── Card shell ───────────────────────────────────────────────────────────────

function Card({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-[#e3e7e4] bg-white p-5 ${className}`}>
      <h2 className="text-sm font-medium text-muted-foreground">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Stat({ value, unit, sub }: { value: string; unit?: string; sub?: React.ReactNode }) {
  return (
    <div>
      <p className="text-3xl font-semibold tracking-tight tabular-nums">
        {value}
        {unit && <span className="ml-1 text-base font-normal text-muted-foreground">{unit}</span>}
      </p>
      {sub && <p className="mt-1 text-sm text-muted-foreground tabular-nums">{sub}</p>}
    </div>
  )
}

// ── Trend chart: daily weigh-ins (dots) + weekly averages (line) ─────────────

function TrendChart({ progress }: { progress: Progress }) {
  const units = progress.profile.units
  const weeks = progress.weeks.filter((w) => w.avg_kg != null)
  const daily = progress.daily.filter((d) => d.weight_kg != null)
  const [hover, setHover] = useState<number | null>(null)

  if (weeks.length === 0 && daily.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Weigh in every morning, after the bathroom, on the same scale. Weekly averages show up after
        three weigh-ins in a week.
      </p>
    )
  }

  const W = 560
  const H = 220
  const pad = { l: 44, r: 12, t: 12, b: 16 }
  const t0 = new Date(progress.weeks[0].start).getTime()
  const t1 = new Date(progress.weeks[progress.weeks.length - 1].end).getTime()
  const values = [
    ...weeks.map((w) => toUnit(w.avg_kg, units) as number),
    ...daily.map((d) => toUnit(d.weight_kg, units) as number),
  ]
  let lo = Math.min(...values)
  let hi = Math.max(...values)
  const span = Math.max(hi - lo, units === "lb" ? 4 : 2)
  lo -= span * 0.15
  hi = lo + span * 1.3
  const x = (iso: string, mid = false) => {
    const t = new Date(iso).getTime() + (mid ? 3.5 * 864e5 : 0)
    return pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r)
  }
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b)
  // Round tick values: the largest 1/2/5 step that still draws 3+ gridlines.
  const mag = 10 ** Math.floor(Math.log10(hi - lo))
  const count = (st: number) => Math.floor(hi / st) - Math.ceil(lo / st) + 1
  const step = [10, 5, 2, 1, 0.5, 0.2, 0.1].map((m) => m * mag).find((st) => count(st) >= 3) ?? mag / 10
  const ticks: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(v)
  const line = weeks.map((w, i) => `${i ? "L" : "M"}${x(w.start, true).toFixed(1)},${y(toUnit(w.avg_kg, units) as number).toFixed(1)}`).join(" ")
  const hovered = hover != null ? weeks[hover] : null

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Bodyweight trend: weekly averages">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="#eef1ef" />
            <text x={pad.l - 6} y={y(v) + 5} textAnchor="end" fontSize="14" fill="#5f6b65">
              {Number.isInteger(step) ? v.toFixed(0) : v.toFixed(1)}
            </text>
          </g>
        ))}
        {daily.map((d) => (
          <circle key={d.date} cx={x(d.date)} cy={y(toUnit(d.weight_kg, units) as number)} r="2.5" fill="#9aa8a0" opacity="0.6" />
        ))}
        <path d={line} fill="none" stroke="#1e6b45" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
        {weeks.map((w, i) => (
          <g key={w.start} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <circle cx={x(w.start, true)} cy={y(toUnit(w.avg_kg, units) as number)} r="12" fill="transparent" />
            <circle cx={x(w.start, true)} cy={y(toUnit(w.avg_kg, units) as number)} r={hover === i ? 5 : 3.5} fill="#1e6b45" stroke="#fff" strokeWidth="1.5" />
          </g>
        ))}
      </svg>
      <p className="mt-1 min-h-5 text-xs text-muted-foreground tabular-nums">
        {hovered
          ? `Week of ${formatLong(hovered.start)}: ${fmt(toUnit(hovered.avg_kg, units))} ${units} avg (${hovered.n} weigh-ins)` +
            (hovered.change_pct != null ? `, ${signed(hovered.change_pct, 2)}% vs prior week` : "")
          : "Line: weekly average. Dots: daily weigh-ins — 1–2% daily swings are water, not fat."}
      </p>
    </div>
  )
}

// ── Weigh-in form ────────────────────────────────────────────────────────────

function WeighIn({ date, units, onSaved }: { date: string; units: "kg" | "lb"; onSaved: () => void }) {
  const [weight, setWeight] = useState("")
  const [waist, setWaist] = useState("")
  const [bf, setBf] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function save() {
    const body: Parameters<typeof logMetrics>[0] = { date, unit: units, length_unit: units === "lb" ? "in" : "cm" }
    if (weight.trim()) body.weight = Number(weight)
    if (waist.trim()) body.waist = Number(waist)
    if (bf.trim()) body.body_fat_pct = Number(bf)
    if (body.weight == null && body.waist == null && body.body_fat_pct == null) {
      setMsg("Enter a weight (or a measurement).")
      return
    }
    setBusy(true)
    try {
      await logMetrics(body)
      setWeight(""); setWaist(""); setBf(""); setMsg("Saved.")
      onSaved()
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Could not save")
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void save() }}>
      <div className="grid grid-cols-3 gap-2">
        <Field id="wi-weight" label={`Weight (${units})`}>
          <Input id="wi-weight" inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} />
        </Field>
        <Field id="wi-waist" label={`Waist (${units === "lb" ? "in" : "cm"})`}>
          <Input id="wi-waist" inputMode="decimal" value={waist} onChange={(e) => setWaist(e.target.value)} />
        </Field>
        <Field id="wi-bf" label="Body fat %">
          <Input id="wi-bf" inputMode="decimal" value={bf} onChange={(e) => setBf(e.target.value)} />
        </Field>
      </div>
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy}>{busy ? "Saving" : `Log ${formatLong(date)}`}</Button>
        {msg && <span className="text-sm text-muted-foreground">{msg}</span>}
      </div>
    </form>
  )
}

// ── Targets diff ─────────────────────────────────────────────────────────────

function TargetsLine({ t, prev }: { t: MacroTargets; prev?: MacroTargets | null }) {
  const cell = (label: string, v: number, p?: number) => (
    <span className="tabular-nums">
      <span className="font-medium">{Math.round(v)}</span>
      <span className="text-muted-foreground"> {label}</span>
      {p != null && Math.round(p) !== Math.round(v) && (
        <span className={v > p ? "text-spinach" : "text-roast"}> ({signed(v - p, 0)})</span>
      )}
    </span>
  )
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {cell("kcal", t.kcal, prev?.kcal)}
      {cell("P", t.protein_g, prev?.protein_g)}
      {cell("C", t.carb_g, prev?.carb_g)}
      {cell("F", t.fat_g, prev?.fat_g)}
      {t.fiber_g != null && cell("fiber", t.fiber_g, prev?.fiber_g ?? undefined)}
    </p>
  )
}

// ── Check-in ─────────────────────────────────────────────────────────────────

const FLAGS = [
  ["period", "On my period"],
  ["high_stress", "High stress"],
  ["poor_sleep", "Poor sleep"],
  ["illness", "Sick / injured"],
  ["new_medication", "New medication"],
] as const

function CheckInCard({ progress, onApplied }: { progress: Progress; onApplied: () => void }) {
  const [flags, setFlags] = useState<Record<string, boolean>>({})
  const [result, setResult] = useState<CheckIn | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const out = await checkIn({ date: progress.date, flags: { ...flags, injury: flags.illness ?? false }, apply })
      setResult(out)
      if (apply) onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Check-in failed")
    } finally {
      setBusy(false)
    }
  }

  const tone = result ? STATUS_TONE[result.status] ?? "neutral" : "neutral"
  const toneClass = tone === "good" ? "bg-[#e7f2ec] text-spinach" : tone === "warn" ? "bg-fat-bg text-fat-fg" : "bg-mist text-foreground"

  return (
    <Card title="Weekly check-in">
      <p className="text-sm text-muted-foreground">
        Same day each week. Anything below can fake a plateau — the coach waits an extra week instead of cutting.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {FLAGS.map(([key, label]) => (
          <label key={key} className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${flags[key] ? "border-spinach bg-[#e7f2ec]" : "border-[#e3e7e4]"}`}>
            <input type="checkbox" className="sr-only" checked={!!flags[key]} onChange={(e) => setFlags({ ...flags, [key]: e.target.checked })} />
            {label}
          </label>
        ))}
      </div>
      <div className="mt-4">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void run(false)}>
          {busy && !result ? "Checking" : "Run check-in"}
        </Button>
      </div>
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
      {result && (
        <div className="mt-4 grid gap-3">
          <span className={`w-fit rounded-full px-3 py-1 text-sm font-medium ${toneClass}`}>
            {result.status.replace(/_/g, " ")}
          </span>
          <ul className="grid gap-2 text-sm">
            {[...result.reasons, ...result.advice].map((r) => <li key={r}>{r}</li>)}
          </ul>
          {result.proposed_targets && (
            <div className="rounded-lg bg-mist p-3">
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {result.applied ? "Applied" : "Proposed targets"}
              </p>
              <TargetsLine t={result.proposed_targets} prev={progress.targets} />
              {!result.applied && (
                <Button type="button" className="mt-3" disabled={busy} onClick={() => void run(true)}>
                  Apply from today
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

// ── Setup: profile + plan ────────────────────────────────────────────────────

function SetupCard({ progress, onApplied }: { progress: Progress; onApplied: () => void }) {
  const p = progress.profile
  const [sex, setSex] = useState(p.sex ?? "")
  const [age, setAge] = useState(() => (p.birth_year ? String(new Date().getFullYear() - p.birth_year) : ""))
  const [height, setHeight] = useState(p.height_cm ? String(p.units === "lb" ? Math.round(p.height_cm / 2.54) : p.height_cm) : "")
  const [activity, setActivity] = useState<Activity>(p.activity)
  const [units, setUnits] = useState(p.units)
  const [goal, setGoal] = useState<"cut" | "maintain" | "reverse">("cut")
  const [rate, setRate] = useState("0.6")
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function go(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      await saveProfile({
        sex: (sex || undefined) as "male" | "female" | undefined,
        age: age ? Number(age) : undefined,
        ...(units === "lb" ? { height_in: height ? Number(height) : undefined } : { height_cm: height ? Number(height) : undefined }),
        activity,
        units,
      } as Parameters<typeof saveProfile>[0])
      const out = await planTargets({ goal, rate_pct: goal === "cut" ? Number(rate) : undefined, date: progress.date, apply })
      setPlan(out)
      if (apply) onApplied()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not plan targets")
    } finally {
      setBusy(false)
    }
  }

  const select = "h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"

  return (
    <Card title="Set targets from your body">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field id="su-sex" label="Sex">
          <select id="su-sex" className={select} value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="">—</option><option value="male">Male</option><option value="female">Female</option>
          </select>
        </Field>
        <Field id="su-age" label="Age">
          <Input id="su-age" inputMode="numeric" value={age} onChange={(e) => setAge(e.target.value)} />
        </Field>
        <Field id="su-height" label={`Height (${units === "lb" ? "in" : "cm"})`}>
          <Input id="su-height" inputMode="decimal" value={height} onChange={(e) => setHeight(e.target.value)} />
        </Field>
        <Field id="su-activity" label="Activity">
          <select id="su-activity" className={select} value={activity} onChange={(e) => setActivity(e.target.value as Activity)}>
            <option value="sedentary">Sedentary</option>
            <option value="light">Light</option>
            <option value="moderate">Moderate (desk + training)</option>
            <option value="very">Very active</option>
            <option value="extra">Extra active</option>
          </select>
        </Field>
        <Field id="su-units" label="Units">
          <select id="su-units" className={select} value={units} onChange={(e) => setUnits(e.target.value as "kg" | "lb")}>
            <option value="kg">kg / cm</option><option value="lb">lb / in</option>
          </select>
        </Field>
        <Field id="su-goal" label="Goal">
          <select id="su-goal" className={select} value={goal} onChange={(e) => setGoal(e.target.value as typeof goal)}>
            <option value="cut">Lose fat</option><option value="maintain">Maintain</option><option value="reverse">Reverse diet</option>
          </select>
        </Field>
        {goal === "cut" && (
          <Field id="su-rate" label="Rate (% bodyweight / week)">
            <select id="su-rate" className={select} value={rate} onChange={(e) => setRate(e.target.value)}>
              <option value="0.4">0.4% — gentle</option>
              <option value="0.5">0.5%</option>
              <option value="0.6">0.6% — default</option>
              <option value="0.7">0.7%</option>
              <option value="0.8">0.8% — aggressive</option>
              <option value="1.0">1.0% — max</option>
            </select>
          </Field>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void go(false)}>Preview</Button>
        {plan && <Button type="button" disabled={busy} onClick={() => void go(true)}>Use these targets</Button>}
      </div>
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
      {plan && (
        <div className="mt-4 grid gap-2 text-sm">
          <TargetsLine t={plan.targets} prev={progress.targets} />
          <p className="text-muted-foreground">
            Maintenance {plan.maintenance.kcal} kcal ({plan.maintenance.source})
            {plan.deficit && <> · deficit {plan.deficit.kcal_per_day} kcal/day ≈ {fmt(toUnit(plan.deficit.kg_per_week, units), 2)} {units}/week</>}
            {" · "}protein {plan.protein_basis}
          </p>
          {[...plan.warnings, ...plan.notes].map((w) => <p key={w} className={plan.warnings.includes(w) ? "text-fat-fg" : "text-muted-foreground"}>{w}</p>)}
        </div>
      )}
    </Card>
  )
}

// ── View ─────────────────────────────────────────────────────────────────────

export function ProgressView({ date, onTargetsChanged }: { date: string; onTargetsChanged: () => void }) {
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let active = true
    getProgress(date)
      .then((p) => { if (active) { setProgress(p); setError(null) } })
      .catch((err: unknown) => { if (active) setError(err instanceof ApiError ? err.message : "Could not load progress") })
    return () => { active = false }
  }, [date, tick])

  const reload = () => setTick((t) => t + 1)
  const latest = useMemo(() => progress ? [...progress.weeks].reverse().find((w) => w.avg_kg != null) ?? null : null, [progress])

  if (error) return <p className="mt-6 text-sm text-roast" role="alert">{error}</p>
  if (!progress) return <p className="mt-8 text-sm text-muted-foreground">Loading progress…</p>

  const u = progress.profile.units
  const m = progress.maintenance
  const a = progress.adherence_14d
  const needsSetup = !progress.targets || progress.body.missing.length > 0

  return (
    <div className="mt-6 grid gap-4 lg:grid-cols-3">
      <Card title="Bodyweight trend" className="lg:col-span-2">
        <TrendChart progress={progress} />
      </Card>
      <Card title="Weigh-in">
        <WeighIn date={date} units={u} onSaved={reload} />
      </Card>

      <Card title="This week">
        <Stat
          value={fmt(toUnit(latest?.avg_kg, u))}
          unit={`${u} avg`}
          sub={latest?.change_kg != null
            ? `${signed(toUnit(latest.change_kg, u), 2)} ${u} (${signed(latest.change_pct, 2)}%) vs last week`
            : "Need 3+ weigh-ins in two weeks to compare"}
        />
      </Card>
      <Card title={progress.phase ? PHASE_LABEL[progress.phase.kind] : "Phase"}>
        {progress.phase ? (
          <Stat
            value={`Week ${progress.phase.week_number}`}
            sub={<>
              since {formatLong(progress.phase.start_date)}
              {progress.phase.params.rate_pct != null && <> · goal {progress.phase.params.rate_pct}%/wk</>}
              {progress.phase.params.pace && <> · {progress.phase.params.pace}</>}
              {progress.phase_change_kg != null && <> · {signed(toUnit(progress.phase_change_kg, u))} {u}</>}
            </>}
          />
        ) : (
          <p className="text-sm text-muted-foreground">No phase yet. Set targets below to start one.</p>
        )}
      </Card>
      <Card title="Maintenance">
        {m.measured.kcal != null ? (
          <Stat
            value={String(m.measured.kcal)}
            unit="kcal measured"
            sub={<>
              {m.measured.confidence} confidence
              {m.predicted && <> · predicted {m.predicted.kcal}</>}
              {m.vs_predicted_pct != null && <> ({signed(m.vs_predicted_pct)}%)</>}
            </>}
          />
        ) : (
          <Stat
            value={m.predicted ? String(m.predicted.kcal) : "—"}
            unit={m.predicted ? "kcal predicted" : undefined}
            sub={m.measured.reason ?? "Measured from your logs once there are 2+ weeks of data"}
          />
        )}
        {m.gate && <p className="mt-2 text-sm text-fat-fg">{m.gate}</p>}
      </Card>

      <Card title="Last 14 days">
        <Stat
          value={`${Math.round(a.logging_coverage * 100)}%`}
          unit="days logged"
          sub={<>
            {a.kcal_compliance != null && <>on calories (±5%) {Math.round(a.kcal_compliance * 100)}% · </>}
            {a.protein_compliance != null && <>protein hit {Math.round(a.protein_compliance * 100)}%</>}
            {a.weekend_drift_kcal != null && Math.abs(a.weekend_drift_kcal) > 150 && (
              <> · weekends {signed(a.weekend_drift_kcal, 0)} kcal vs weekdays</>
            )}
          </>}
        />
      </Card>
      <div className="lg:col-span-2">
        <CheckInCard progress={progress} onApplied={() => { reload(); onTargetsChanged() }} />
      </div>
      <div className={needsSetup ? "lg:col-span-3 lg:order-first" : "lg:col-span-3"}>
        <SetupCard progress={progress} onApplied={() => { reload(); onTargetsChanged() }} />
      </div>
    </div>
  )
}
