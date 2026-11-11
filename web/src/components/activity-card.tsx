import { useState } from "react"

import { addWorkout, ApiError, deleteWorkout, setActivitySettings, updateWorkout } from "@/api"
import { Field } from "@/components/field"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { formatLong, weekdayShort } from "@/lib/format"
import type { ActivityDay, ActivitySummary, DateRange, Workout } from "@/types"

const select =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"

const BASIS: Record<Workout["duration_basis"], string> = {
  recorded: "recorded duration",
  elapsed: "start to end (may include pauses)",
  manual: "entered manually",
}

function when(iso: string | null | undefined) {
  if (!iso) return "never"
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
}

function shortDate(iso: string) {
  const [, m, d] = iso.split("-").map(Number)
  return `${weekdayShort(iso)} ${m}/${d}`
}

function range(r: DateRange) {
  return r.start === r.end ? formatLong(r.start) : `${formatLong(r.start)} – ${formatLong(r.end)}`
}

function n(value: number) {
  return value.toLocaleString()
}

function StepsCell({ day }: { day: ActivityDay }) {
  if (day.future) return <span className="text-muted-foreground">—</span>
  if (day.steps != null) {
    return <span className="tabular-nums">{n(day.steps)}{day.partial && <span className="text-muted-foreground"> so far</span>}</span>
  }
  return <span className="text-muted-foreground">{day.steps_queried ? "No data received" : "Not synced"}</span>
}

function SyncStatus({ a }: { a: ActivitySummary }) {
  const last = a.sync.last_request
  const tone = !last ? "bg-mist text-foreground"
    : last.outcome === "partial" ? "bg-fat-bg text-fat-fg" : "bg-[var(--me-green-soft)] text-spinach"
  return (
    <div className="grid gap-1 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-3 py-0.5 text-xs font-medium ${tone}`}>
          {last ? (last.outcome === "partial" ? "Partial sync" : last.outcome === "updated" ? "Updated" : "No new readings") : "No activity sync yet"}
        </span>
        {last && <span className="text-muted-foreground">last received {when(last.received_at)} (captured {when(last.captured_at)})</span>}
      </p>
      {last?.partial_reasons.map((r) => <p key={r} className="text-fat-fg">{r[0].toUpperCase() + r.slice(1)}.</p>)}
      {a.sync.catch_up.length > 0 && (
        <p className="text-fat-fg">
          Missing days to catch up: {a.sync.catch_up.map(range).join(", ")}. The next Sync Macro Engine run reads 31 days to fill them.
        </p>
      )}
      {a.sync.backlog_beyond_limit.length > 0 && (
        <p className="text-muted-foreground">
          Not synced and past the 30-day catch-up limit: {a.sync.backlog_beyond_limit.map(range).join(", ")}.
        </p>
      )}
    </div>
  )
}

function SourcePicker({ a, onChanged }: { a: ActivitySummary; onChanged: () => void }) {
  const [msg, setMsg] = useState<string | null>(null)
  if (a.source.seen.length === 0) return null
  async function choose(source: string) {
    try {
      await setActivitySettings({ source })
      onChanged()
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Could not save")
    }
  }
  return (
    <div className="grid gap-1 text-sm">
      <Field id="act-source" label={a.source.selected ? "Step and resting HR source" : "Choose your Apple Watch as the step source"}>
        <select id="act-source" className={select} value={a.source.selected ?? ""} onChange={(e) => void choose(e.target.value)}>
          {!a.source.selected && <option value="">Select a source…</option>}
          {a.source.seen.map((s) => (
            <option key={s.source} value={s.source}>{s.source} ({s.days} days)</option>
          ))}
        </select>
      </Field>
      <p className="text-xs text-muted-foreground">
        Only this source counts, so iPhone and Watch steps are never added together. Days without the Watch read low or show no data.
        {a.source.history.length > 1 && ` Source changed ${when(a.source.history[a.source.history.length - 1].at)}; all days now use the new source.`}
      </p>
      {msg && <p className="text-destructive">{msg}</p>}
    </div>
  )
}

function WorkoutForm({ a, date, editing, onDone }: {
  a: ActivitySummary; date: string; editing: Workout | null; onDone: () => void
}) {
  const known = a.manual_workout_types.filter((t) => t !== "Other")
  const initialType = editing ? (known.includes(editing.type) ? editing.type : "Other") : known[0]
  const [type, setType] = useState(initialType)
  const [custom, setCustom] = useState(editing && !known.includes(editing.type) ? editing.type : "")
  const [day, setDay] = useState(editing?.local_date ?? date)
  const [minutes, setMinutes] = useState(editing ? String(editing.minutes) : String(a.plan.strength_minutes_per_session ?? ""))
  const [start, setStart] = useState(editing?.start_at ? editing.start_at.slice(11, 16) : "")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function save() {
    const wtype = type === "Other" ? custom.trim() : type
    const mins = Number(minutes)
    if (!wtype || !(mins > 0)) {
      setMsg("Enter a type and minutes.")
      return
    }
    setBusy(true)
    try {
      const body = { date: day, type: wtype, duration_minutes: mins, start_time: start || null }
      if (editing) await updateWorkout(editing.id, body)
      else await addWorkout(body)
      onDone()
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Could not save")
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="grid gap-3 rounded-lg bg-mist p-3" onSubmit={(e) => { e.preventDefault(); void save() }}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Field id="wo-type" label="Type">
          <select id="wo-type" className={select} value={type} onChange={(e) => setType(e.target.value)}>
            {a.manual_workout_types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field id="wo-date" label="Date">
          <Input id="wo-date" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </Field>
        <Field id="wo-min" label="Minutes">
          <Input id="wo-min" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </Field>
        <Field id="wo-start" label="Start (optional)">
          <Input id="wo-start" type="time" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
      </div>
      {type === "Other" && (
        <Field id="wo-custom" label="Workout name">
          <Input id="wo-custom" value={custom} onChange={(e) => setCustom(e.target.value)} />
        </Field>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>{busy ? "Saving" : editing ? "Save workout" : "Add workout"}</Button>
        <Button type="button" variant="outline" onClick={onDone}>Cancel</Button>
        {msg && <span className="text-sm text-destructive">{msg}</span>}
      </div>
    </form>
  )
}

export function ActivityCard({ activity: a, date, onChanged }: { activity: ActivitySummary; date: string; onChanged: () => void }) {
  const [form, setForm] = useState<{ editing: Workout | null } | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const week = a.weeks[a.weeks.length - 1]
  const prev = a.weeks[a.weeks.length - 2]
  // Averages come from closed days; early in a week, show last week's too.
  const avgWeek = week.closed_days > 0 ? week : prev
  const days = [...a.days].reverse().slice(0, showAll ? a.days.length : 7)
  const planned = a.plan.strength_days_per_week

  async function remove(w: Workout) {
    setError(null)
    try {
      await deleteWorkout(w.id)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete")
    }
  }

  return (
    <section className="rounded-xl border border-[var(--me-line)] bg-[var(--me-surface)] p-5">
      <h2 className="text-sm font-medium text-muted-foreground">Activity</h2>
      <div className="mt-3 grid gap-4">
        <SyncStatus a={a} />

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div>
            <p className="text-xs text-muted-foreground">Today, so far</p>
            <p className="text-2xl font-semibold tabular-nums">{a.today_steps ? n(a.today_steps.value) : "—"}</p>
            <p className="text-xs text-muted-foreground">
              {a.today_steps ? `${a.source.label} · as of ${when(a.today_steps.captured_at)}` : "No data received today"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Avg steps, {avgWeek === week ? "this week" : "last week"}</p>
            <p className="text-2xl font-semibold tabular-nums">{avgWeek?.steps_avg != null ? n(avgWeek.steps_avg) : "—"}</p>
            <p className="text-xs text-muted-foreground">
              {avgWeek ? `${avgWeek.steps_observed_days} of ${avgWeek.closed_days} closed days with data` : "No closed days yet"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Resting HR, {avgWeek === week ? "this week" : "last week"}</p>
            <p className="text-2xl font-semibold tabular-nums">
              {avgWeek?.resting_hr_avg != null ? Math.round(avgWeek.resting_hr_avg) : "—"}
              {avgWeek?.resting_hr_avg != null && <span className="ml-1 text-sm font-normal text-muted-foreground">bpm</span>}
            </p>
            <p className="text-xs text-muted-foreground">
              {avgWeek && avgWeek.resting_hr_days > 0 ? `average of ${avgWeek.resting_hr_days} day(s)` : "No data received"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Workouts this week</p>
            <p className="text-2xl font-semibold tabular-nums">
              {week.workouts.strength.count}
              <span className="ml-1 text-sm font-normal text-muted-foreground">strength{planned ? ` / ${planned} planned` : ""}</span>
            </p>
            <p className="text-xs text-muted-foreground">
              {week.workouts.strength.minutes} min strength · {week.workouts.cardio.count} cardio ({week.workouts.cardio.minutes} min)
              {week.workouts.other.count > 0 && ` · ${week.workouts.other.count} other`}
            </p>
          </div>
        </div>

        {a.notes.length > 0 && (
          <ul className="grid gap-1 text-sm">
            {a.notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        )}

        <SourcePicker a={a} onChanged={onChanged} />

        <div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="py-1 font-normal">Day</th>
                <th className="py-1 font-normal">Steps</th>
                <th className="py-1 font-normal">Resting HR</th>
                <th className="py-1 text-right font-normal">Workouts</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.date} className="border-t border-[var(--me-mist)]">
                  <td className="py-1.5">{shortDate(d.date)}</td>
                  <td className="py-1.5"><StepsCell day={d} /></td>
                  <td className="py-1.5 tabular-nums">{d.resting_hr_bpm != null ? `${Math.round(d.resting_hr_bpm)} bpm` : <span className="text-muted-foreground">—</span>}</td>
                  <td className="py-1.5 text-right tabular-nums">{d.workouts || <span className="text-muted-foreground">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="mt-1 text-xs text-muted-foreground underline" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show 7 days" : `Show ${a.days.length} days`}
          </button>
        </div>

        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Workouts</h3>
            {!form && <Button type="button" variant="outline" onClick={() => setForm({ editing: null })}>Add workout</Button>}
          </div>
          {form && (
            <WorkoutForm key={form.editing?.id ?? "new"} a={a} date={date} editing={form.editing}
                         onDone={() => { setForm(null); onChanged() }} />
          )}
          {a.workouts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No workouts received.</p>
          ) : (
            <ul className="grid gap-1 text-sm">
              {[...a.workouts].reverse().map((w) => (
                <li key={w.id} className="flex flex-wrap items-baseline justify-between gap-x-3 border-t border-[var(--me-mist)] py-1.5">
                  <span>
                    {shortDate(w.local_date)} · {w.type} · <span className="tabular-nums">{w.minutes} min</span>
                    <span className="text-xs text-muted-foreground"> ({BASIS[w.duration_basis]}{w.source !== "manual" ? `, ${w.source}` : ""})</span>
                  </span>
                  {w.source === "manual" && (
                    <span className="flex gap-3 text-xs">
                      <button type="button" className="underline" onClick={() => setForm({ editing: w })}>Edit</button>
                      <button type="button" className="text-destructive underline" onClick={() => void remove(w)}>Delete</button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <div className="grid gap-1 text-xs text-muted-foreground">
          <p>{a.capabilities.workouts_note}</p>
          <p>
            Steps last queried {when(a.freshness.steps.last_query_captured_at)}
            {a.freshness.steps.last_observation_date && `, latest data for ${formatLong(a.freshness.steps.last_observation_date)}`}.
            {" "}Resting HR latest data {a.freshness.resting_heart_rate.last_observation_date ? formatLong(a.freshness.resting_heart_rate.last_observation_date) : "none"}.
          </p>
          <p>{a.timezone_note} Timezone: {a.timezone}.</p>
          <p>Context only: activity doesn't change your targets or calorie budget.</p>
        </div>
      </div>
    </section>
  )
}
