import { useEffect, useRef, useState } from "react"
import { CalendarDays, ChefHat, ChevronRight, Refrigerator, Shuffle, Utensils } from "lucide-react"
import { actOnMeal, getMealBoard, type CookbookAction, type MealBoard, type MealChoice } from "@/api"
import { BottomSheet, BottomSheetContent, BottomSheetTitle } from "@/components/ui/bottom-sheet"
import { isoToday, weekdayName } from "@/lib/format"
import { MEALS, mealLabel, type Meal } from "@/lib/meals"
import "./meal-planner.css"

const portionLabel = (n: number) => `${n} serving${n === 1 ? "" : "s"}`
const reader = (id: string) => `/api/cookbooks/index.html#${encodeURIComponent(id)}`

export function MealPlanner({date, meal, onMeal, stamp, onLogged}: {
  date: string; meal: Meal; onMeal: (meal: Meal) => void; stamp: unknown; onLogged: () => void
}) {
  const [mode, setMode] = useState("cook")
  const [linkedId, setLinkedId] = useState(() => new URLSearchParams(location.search).get("cookbook") ?? "")
  const [query, setQuery] = useState("")
  const [minutes, setMinutes] = useState("")
  const [servings, setServings] = useState(1)
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [board, setBoard] = useState<MealBoard | null>(null)
  const [readError, setReadError] = useState("")
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<MealChoice | null>(null)
  const [intent, setIntent] = useState<"eat" | "plan" | "prep">("eat")
  const [days, setDays] = useState(1)
  const [when, setWhen] = useState(date)
  const [planMeal, setPlanMeal] = useState<Meal>(meal)
  const [quantity, setQuantity] = useState("1")
  const [fromStock, setFromStock] = useState(false)
  const [pendingDiscard, setPendingDiscard] = useState<string | null>(null)
  const request = useRef<{payload: string; id: string} | null>(null)
  const inFlight = useRef(false)
  const panel = useRef<HTMLElement>(null)
  const today = isoToday()
  useEffect(() => {
    const search = new URLSearchParams(location.search)
    if (search.get("view") === "meal" || search.has("cookbook")) {
      panel.current?.scrollIntoView({block: "start"})
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setBoard(null)
    setReadError("")
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({date, meal, mode, query: linkedId || query, offset: String(offset), servings: String(servings)})
      if (minutes && mode === "cook") params.set("minutes", minutes)
      getMealBoard(params, controller.signal).then(data => {
        if (!controller.signal.aborted) setBoard(data)
      }).catch(err => { if (!controller.signal.aborted) setReadError(err.message) })
    }, 100)
    return () => { clearTimeout(timer); controller.abort() }
  }, [date, meal, mode, query, linkedId, minutes, servings, offset, revision, stamp])

  async function save(action: CookbookAction, confirmation: string) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setError(""); setMessage("")
    const payload = JSON.stringify(action)
    if (request.current?.payload !== payload) {
      request.current = {payload, id: Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-")}
    }
    try {
      await actOnMeal(action, request.current.id)
      request.current = null
      setSelected(null); setMessage(confirmation); setPendingDiscard(null)
      setRevision(n => n + 1)
      if (action.action === "eat") onLogged()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save this meal")
    } finally {
      inFlight.current = false; setBusy(false)
    }
  }
  function choose(dish: MealChoice) {
    setSelected(dish); setIntent(date > today ? "plan" : "eat")
    setQuantity(String(servings)); setWhen(date); setPlanMeal(meal); setDays(1)
    setFromStock(dish.ready >= servings); setError(""); setMessage("")
  }
  const dish = board?.choices[0]
  const qty = Number(quantity)
  const qtyOk = qty > 0 && qty <= 100 && Number.isInteger(qty * 4)
  const goal = board?.meal_kcal
  const future = date > today

  return <section ref={panel} className="me-card meal-planner" aria-label="Meal planner">
    <div className="mp-heading"><div><span className="mp-kicker"><Utensils size={14}/> Meal planner</span>
      <h2>What should I eat?</h2></div>
      <select aria-label="Meal to plan" value={meal} onChange={e => { onMeal(e.target.value as Meal); setOffset(0) }}>
        {MEALS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}</select></div>
    <div className="mp-modes">
      <button aria-pressed={mode==="cook"} onClick={() => {setMode("cook"); setOffset(0)}}><ChefHat size={17}/>Cook something</button>
      <button aria-pressed={mode==="ready"} onClick={() => {setMode("ready"); setOffset(0)}}><Refrigerator size={17}/>Ready now</button>
    </div>
    {linkedId && <button className="text-btn" onClick={() => {setLinkedId(""); history.replaceState(null,"",location.pathname+location.hash)}}>Show all suggestions</button>}
    <div className="mp-filters">
      <input aria-label="Recipe or ingredient" placeholder="Recipe or ingredient" type="search" value={query}
        onChange={e => {setLinkedId(""); setQuery(e.target.value); setOffset(0)}} />
      {mode === "cook" && <select aria-label="Total recipe time" value={minutes} onChange={e => {setMinutes(e.target.value); setOffset(0)}}>
        <option value="">Any time</option>{[15,30,45,60].map(n => <option key={n} value={n}>≤ {n} min</option>)}</select>}
      <select aria-label="Portion to compare" value={servings} onChange={e => {setServings(Number(e.target.value)); setOffset(0)}}>
        {[0.25,0.5,0.75,1,1.5,2].map(n => <option value={n} key={n}>{portionLabel(n)}</option>)}</select>
    </div>
    {readError ? <p role="alert">{readError} <button onClick={() => setRevision(n => n+1)}>Retry</button></p>
      : !board ? <p role="status">Finding meals…</p>
      : !dish ? <div className="mp-choice"><strong>{mode === "ready" ? "No matching prepared portions" : "No matching recipes"}</strong>
        <p>{mode === "ready" ? "Save the servings you cooked with the Meal prep action." : "Try another ingredient or allow more time. The time filter uses published total times."}</p></div>
      : <div className="mp-choice">
        <div className="mp-choice-top"><small>{mode === "ready" ? `${portionLabel(dish.ready)} prepared` : dish.minutes ? `Ready in ${dish.minutes} min` : dish.book}</small>
          {board.count > 1 && <button aria-label="Show another meal" onClick={() => setOffset(n => n+1)}><Shuffle size={16}/> Swap</button>}</div>
        <h3>{dish.title}</h3>
        <div className="mp-macros"><strong>{Math.round(dish.portion.kcal)} kcal</strong><span>{dish.portion.protein_g} g protein</span>
          <span>{dish.portion.carb_g} g carbs</span><span>{dish.portion.fat_g} g fat</span></div>
        {dish.kitchen_matches.length > 0 && <p>From My Kitchen · {dish.kitchen_matches.join(", ")}</p>}
        {goal != null && <p>About {goal.toLocaleString()} kcal available per meal across {board.slots} remaining {board.slots === 1 ? "meal" : "meals"}.
          {" "}{dish.after.kcal != null && (dish.after.kcal >= 0 ? `This portion leaves ${Math.round(dish.after.kcal).toLocaleString()} kcal after planned meals.` : `This portion puts the day ${Math.round(-dish.after.kcal)} kcal above target including planned meals.`)}</p>}
        <div className="mp-actions"><button className="mp-primary" onClick={() => choose(dish)}>Choose this meal <ChevronRight size={16}/></button>
          <a target="_blank" rel="noreferrer" href={reader(dish.id)}>Full recipe</a></div>
      </div>}
    {(board?.plans.length || board?.prepared.length) ? <details className="mp-agenda" open>
      <summary><CalendarDays size={16}/> Your plan &amp; prep</summary>
      {board.prep_list.map(p => <div className="mp-row" key={`cook-${p.id}`}><div><small>To cook for your next seven days</small>
        <a target="_blank" rel="noreferrer" href={reader(p.id)}>{p.title}</a> · {portionLabel(p.needed)}</div>
        <button className="text-btn" disabled={busy} onClick={() => void save({action:"prep",recipe_id:p.id,servings:Math.min(100,p.needed)},
          `Added ${portionLabel(Math.min(100,p.needed))} to Ready now.`)}>I made {portionLabel(Math.min(100,p.needed))}</button></div>)}
      {board.plans.map(p => {
        const stocked = (board.prepared.find(x => x.id === p.recipe_id)?.servings ?? 0) >= p.servings
        return <div className="mp-row" key={p.id}><div><small>{p.date === today ? "Today" : weekdayName(p.date)} · {mealLabel(p.meal)} · {portionLabel(p.servings)}</small>
          <a target="_blank" rel="noreferrer" href={reader(p.recipe_id)}>{p.title}</a></div><div className="mp-actions">
          {p.date <= today && <button disabled={busy} onClick={() => void save({action:"eat",plan_id:p.id,use_prepared:stocked}, `Logged ${p.title}.`)}>{stocked ? "Eat from prep" : "Log eaten"}</button>}
          <button disabled={busy} onClick={() => void save({action:"remove_plan",plan_id:p.id}, "Meal removed from plan.")}>Remove plan</button></div></div>
      })}
      {board.prepared.map(p => <div className="mp-row" key={p.id}><div><small>Prepared · {portionLabel(p.servings)}</small>
        <a href={reader(p.id)} target="_blank" rel="noreferrer">{p.title}</a></div><div className="mp-actions">
          <button onClick={() => {setMode("ready"); setLinkedId(p.id); setQuery(p.title); setServings(Math.min(1,p.servings)); setOffset(0)}}>Use portion</button>
          {pendingDiscard === p.id ? <><button disabled={busy} onClick={() => void save({action:"discard",recipe_id:p.id,servings:p.servings}, "Cleared remaining portions.")}>Confirm clear</button>
          <button onClick={() => setPendingDiscard(null)}>Keep</button></> : <button onClick={() => setPendingDiscard(p.id)}>Clear stock</button>}
        </div></div>)}
    </details> : null}
    {message && <p role="status">{message}</p>}
    {error && !selected && <p role="alert">{error}</p>}
    <BottomSheet open={!!selected} onOpenChange={open => {if (!open && !busy) setSelected(null)}}>
      <BottomSheetContent aria-label="Choose meal action" header={<BottomSheetTitle>{selected?.title}</BottomSheetTitle>}>
        {selected && <div className="mp-editor">
          <div className="mp-modes">{(["eat","plan","prep"] as const).map(a => <button key={a} disabled={busy || (a === "eat" && future)} aria-pressed={intent===a}
            onClick={() => {setIntent(a); setError(""); setQuantity(String(servings))}}>{a==="eat"?"Log eaten":a==="plan"?"Plan ahead":"Meal prep"}</button>)}</div>
          <label>{intent === "prep" ? "Servings you actually prepared" : "Servings"}<input type="number" min=".25" max="100" step=".25" value={quantity} onChange={e=>setQuantity(e.target.value)} /></label>
          {qtyOk && <div className="mp-macros"><strong>{Math.round(selected.macros.kcal*qty)} kcal</strong><span>{Math.round(selected.macros.protein_g*qty)} g protein</span>
            <span>{Math.round(selected.macros.carb_g*qty)} g carbs</span><span>{Math.round(selected.macros.fat_g*qty)} g fat</span></div>}
          {intent === "plan" && <div className="mp-filters"><label>Plan for<input type="date" value={when} onChange={e=>setWhen(e.target.value)} /></label>
            <label>Consecutive days<select value={days} onChange={e=>setDays(Number(e.target.value))}>{[1,2,3,4,5,6,7].map(n=><option key={n}>{n}</option>)}</select></label>
            <label>Meal<select value={planMeal} onChange={e=>setPlanMeal(e.target.value as Meal)}>{MEALS.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label></div>}
          {intent === "eat" && selected.ready > 0 && <label className="mp-check"><input type="checkbox" checked={fromStock} onChange={e=>setFromStock(e.target.checked)}/> Use prepared stock ({portionLabel(selected.ready)} available)</label>}
          {error && <p role="alert">{error}</p>}
          <button className="mp-primary" disabled={busy || !qtyOk || (intent==="plan" && !when) || (intent==="eat" && fromStock && qty>selected.ready)}
            onClick={() => void save({action:intent,days:intent==="plan"?days:1,recipe_id:selected.id,servings:qty,date:intent==="plan"?when:date,meal:intent==="plan"?planMeal:meal,use_prepared:intent==="eat" && fromStock},
              intent==="eat" ? `Logged ${portionLabel(qty)} for ${mealLabel(meal)}.` : intent==="prep" ? `Added ${portionLabel(qty)} to Ready now.` : `Planned ${selected.title} for ${days} day(s) starting ${when}.`)}>
            {busy ? "Saving…" : intent==="eat" ? `Log to ${mealLabel(meal)}` : intent==="plan" ? "Save planned meal" : "Add prepared servings"}</button>
        </div>}
      </BottomSheetContent>
    </BottomSheet>
  </section>
}
