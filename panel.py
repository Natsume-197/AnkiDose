"""Plan status and progress panel (HTML + SVG). Has no Anki imports so it can be tested on its own."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape

# Anki theme variables, with a fallback color in case one is missing.
# Progress color: soft indigo, one shade for light mode and one for dark mode (ACCENT_CSS).
ACCENT = "var(--ankidose-accent, #5b5bd6)"
GREEN = "var(--state-review, #1baf7a)"
AMBER = "#d18f00"
SUBTLE = "var(--fg-subtle, #898781)"
GRID = "var(--border-subtle, rgba(128,128,128,.25))"
ACCENT_CSS = (
    ":root { --ankidose-accent: #5b5bd6; } "
    ".night-mode, .nightMode, .night_mode { --ankidose-accent: #8b8cf5; }"
)

MODES = [
    ("spread", "Spread across the remaining blocks"),
    ("carry", "Carry over to the next block"),
]


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_dur(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 1:
        return "<1 min"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" if m % 60 == 0 else f"{m // 60} h {m % 60:02d} min"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass
class Status:
    k: int  # current block (0-based)
    nblocks: int
    quota: int
    done_block: int
    done_total: int
    total: int  # effective goal (goes down if the deck runs out of reviews)
    start: float
    block_len: float  # seconds per block
    block_start: float
    block_end: float
    end: float
    now: float
    blocks: list[tuple[int, int]] = field(default_factory=list)  # (done, quota) for blocks 0..k
    times: list[float] = field(default_factory=list)  # first answer of each card, sorted
    planned_total: int = 0  # what you entered when creating the plan
    learning: int = 0  # cards in (re)learning: they don't count, but they'll come back
    spc: float = 10.0  # seconds per card (includes the time spent relearning failed cards)
    out_of_cards: bool = False  # the deck has no reviews left
    extra: bool = False  # the current block is extra time added after the plan ran out

    @property
    def finished(self) -> bool:
        return self.now >= self.end

    @property
    def block_complete(self) -> bool:
        return self.done_block >= self.quota

    @property
    def plan_complete(self) -> bool:
        return self.done_total >= self.total

    @property
    def goal_lowered(self) -> bool:
        return self.total < self.planned_total

    @property
    def expected(self) -> int:
        frac = (self.now - self.start) / max(1.0, self.end - self.start)
        return round(self.total * min(1.0, max(0.0, frac)))

    @property
    def delta(self) -> int:
        """Positive = ahead of the plan."""
        return self.done_total - self.expected

    @property
    def block_time_left(self) -> float:
        return max(0, self.quota - self.done_block) * self.spc

    @property
    def time_left(self) -> float:
        return max(0, self.total - self.done_total) * self.spc


# ---------------------------------------------------------------- text


def short_text(st: Status) -> str:
    if st.plan_complete:
        return f"Plan complete ✓ {st.done_total}/{st.total}"
    if st.finished:
        return f"Time's up · {st.done_total}/{st.total}"
    head = "Extra time" if st.extra else f"Block {st.k + 1}/{st.nblocks}"
    if st.block_complete:
        nxt = f" · next {hhmm(st.block_end)}" if st.k + 1 < st.nblocks else ""
        return f"{head} ✓ {st.done_block}/{st.quota}{nxt}"
    return (
        f"{head} · {st.done_block}/{st.quota} · ~{fmt_dur(st.block_time_left)} "
        f"· until {hhmm(st.block_end)}"
    )


def learning_note(st: Status) -> str:
    if not st.learning:
        return ""
    return f"{plural(st.learning, 'card')} still in learning (they don't count, but they'll come back)."


def message(st: Status, mode: str) -> str:
    if st.plan_complete:
        if st.out_of_cards and st.goal_lowered:
            return f"No reviews left in the deck! Plan complete with {st.done_total}. 🎉"
        return "Plan complete! You won today. 🎉"
    if st.finished:
        return f"Time's up: you did {st.done_total} of {st.total}. Tomorrow will be better. 💪"
    if st.block_complete:
        if st.k + 1 < st.nblocks:
            return (
                f"Block done ✓ The next one opens at {hhmm(st.block_end)}. "
                "Keep going now and the next blocks get lighter."
            )
        return "Last block done ✓"
    left = st.quota - st.done_block
    if st.done_total == 0:
        return f"Let's go! This block is {st.quota} cards, about {fmt_dur(st.block_time_left)}. 🚀"
    if left <= 15:
        return f"Only {left} left to close the block! Less than {fmt_dur(st.block_time_left + 30)}. 🔥"
    if st.delta >= 10:
        return f"You're {st.delta} ahead of the plan. 💪 Keep it up."
    if st.delta <= -10:
        how = (
            "they'll be added to this block"
            if mode == "carry"
            else "they'll be spread across the remaining blocks"
        )
        return f"You're {-st.delta} behind, no worries: {how}."
    return f"Right on pace. 👌 About {fmt_dur(st.block_time_left)} left in this block."


# ---------------------------------------------------------------- charts
# Colors are Anki theme variables (with fallbacks) so the panel matches the deck list in
# light and dark mode. In SVG they go inside style="", because presentation attributes
# don't understand var().


def pace_svg(st: Status) -> str:
    """Your actual progress line against the plan's ideal line."""
    W, H, L, R, T, B = 400, 130, 30, 8, 8, 20
    span = max(1.0, st.end - st.start)
    top = max(st.total, st.done_total, 1)

    def x(t: float) -> float:
        return L + (min(max(t, st.start), st.end) - st.start) / span * (W - L - R)

    def y(v: float) -> float:
        return T + (1 - v / top) * (H - T - B)

    parts = []
    # horizontal guides + block boundaries
    for v in (0, top / 2, top):
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" style="stroke:{GRID}"/>')
    if st.nblocks <= 24:
        for j in range(1, st.nblocks):
            bx = x(st.start + j * st.block_len)
            parts.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{T}" y2="{H - B}" style="stroke:{GRID}"/>')

    # ideal line
    parts.append(
        f'<line x1="{x(st.start):.1f}" y1="{y(0):.1f}" x2="{x(st.end):.1f}" y2="{y(st.total):.1f}" '
        f'style="stroke:{SUBTLE};stroke-width:1.5;stroke-dasharray:5 4"/>'
    )

    # your line (downsampled so it doesn't carry thousands of points)
    times = [t for t in st.times if t <= st.now]
    step = max(1, len(times) // 200)
    pts = [(x(st.start), y(0))]
    for i in range(0, len(times), step):
        pts.append((x(times[i]), y(i + 1)))
    now_x, now_y = x(st.now), y(st.done_total)
    pts.append((now_x, now_y))
    line = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    area = f"M{pts[0][0]:.1f},{y(0):.1f} L" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts) + f" L{now_x:.1f},{y(0):.1f} Z"
    parts.append(f'<path d="{area}" style="fill:{ACCENT};fill-opacity:.12"/>')
    parts.append(f'<polyline points="{line}" style="fill:none;stroke:{ACCENT};stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/>')
    parts.append(f'<circle cx="{now_x:.1f}" cy="{now_y:.1f}" r="4" style="fill:{ACCENT};stroke:var(--canvas, white);stroke-width:1.5"/>')

    # labels
    fs = f'style="font-size:11px;fill:{SUBTLE}"'
    parts.append(f'<text x="{L - 4}" y="{y(top) + 4:.1f}" text-anchor="end" {fs}>{top}</text>')
    parts.append(f'<text x="{L - 4}" y="{y(0) + 3:.1f}" text-anchor="end" {fs}>0</text>')
    if st.nblocks <= 8:
        ticks = [st.start + j * st.block_len for j in range(st.nblocks)] + [st.end]
        for j, t in enumerate(ticks):
            anchor = "start" if j == 0 else "end" if j == len(ticks) - 1 else "middle"
            parts.append(f'<text x="{x(t):.1f}" y="{H - 5}" text-anchor="{anchor}" {fs}>{hhmm(t)}</text>')
    else:
        parts.append(f'<text x="{L}" y="{H - 5}" {fs}>{hhmm(st.start)}</text>')
        parts.append(f'<text x="{W - R}" y="{H - 5}" text-anchor="end" {fs}>{hhmm(st.end)}</text>')

    return f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block">{"".join(parts)}</svg>'


def segments_html(st: Status) -> str:
    """Bar split into one segment per block: green = block done, accent = progress."""
    segs = []
    for j in range(st.nblocks):
        done, quota = st.blocks[j] if j < len(st.blocks) else (0, 0)
        reached = j <= st.k
        complete = st.plan_complete or (reached and done >= quota)
        if complete:
            pct, color = 100, GREEN
        elif reached and quota:
            pct, color = min(100, round(100 * done / quota)), ACCENT
        else:
            pct, color = 0, ACCENT
        name = "Extra time" if st.extra and j == st.nblocks - 1 else f"Block {j + 1}"
        title = f"{name}: {done}/{quota}" if reached else name
        segs.append(
            f'<div class="seg" title="{title}"><div style="width:{pct}%;background:{color}"></div></div>'
        )
    return f'<div class="segs">{"".join(segs)}</div>'


def progress(st: Status) -> tuple[int, bool]:
    """(% shown by the ring and the review progress line, is it done?)."""
    if st.plan_complete or (st.block_complete and not st.finished):
        return 100, True
    if st.finished:
        return min(100, round(100 * st.done_total / max(1, st.total))), False
    return min(100, round(100 * st.done_block / max(1, st.quota))), False


def ring_svg(st: Status) -> str:
    pct, done = progress(st)
    r = 26
    circ = 2 * 3.14159 * r
    color = GREEN if done else ACCENT
    arc = (
        f'<circle cx="32" cy="32" r="{r}" transform="rotate(-90 32 32)" style="fill:none;stroke:{color};'
        f'stroke-width:6;stroke-linecap:round;stroke-dasharray:{circ:.1f};stroke-dashoffset:{circ * (1 - pct / 100):.1f}"/>'
        if pct
        else ""
    )
    center = (
        f'<text x="32" y="38" text-anchor="middle" style="font-size:18px;fill:{GREEN}">✓</text>'
        if done
        else f'<text x="32" y="36.5" text-anchor="middle" style="font-size:12px;fill:var(--fg, currentColor)">{pct}%</text>'
    )
    return (
        f'<svg width="64" height="64" viewBox="0 0 64 64" style="flex:none">'
        f'<circle cx="32" cy="32" r="{r}" style="fill:none;stroke:{GRID};stroke-width:6"/>{arc}{center}</svg>'
    )


def overlay_state(st: Status) -> dict:
    """What the thin line at the top of the review screen shows."""
    pct, done = progress(st)
    if st.plan_complete:
        text = "Plan complete ✓"
    elif st.finished:
        text = f"Time's up · {st.done_total}/{st.total}"
    elif st.block_complete:
        nxt = f" · next {hhmm(st.block_end)}" if st.k + 1 < st.nblocks else ""
        text = f"Block done ✓{nxt}"
    else:
        text = f"{st.done_block}/{st.quota} · ~{fmt_dur(st.block_time_left)}"
    return {"text": text, "pct": pct, "color": GREEN if done else ACCENT}


# ---------------------------------------------------------------- full panel


def kpi(label: str, value: str) -> str:
    return f'<div class="kpi"><div class="s">{label}</div><div>{value}</div></div>'


def button(label: str, command: str, primary: bool = False) -> str:
    cls = ' class="pri"' if primary else ""
    return f"<button{cls} onclick=\"pycmd('ankidose:{command}')\">{label}</button>"


def link(label: str, command: str) -> str:
    return f"<a onclick=\"pycmd('ankidose:{command}')\">{label}</a>"


def panel_css() -> str:
    # 3-size type scale: 26px (main line), 14px (text), 12px (secondary).
    # Background, border, radius and shadow match Anki's deck list; buttons keep Anki's style.
    return f"""<style>
{ACCENT_CSS}
.ankidose {{ max-width:440px; margin:16px auto; padding:16px 18px; text-align:left; font-size:14px;
          line-height:1.4; color:var(--fg, inherit); font-variant-numeric:tabular-nums;
          background:var(--canvas-glass, transparent); border:1px solid {GRID};
          border-radius:var(--border-radius-medium, 12px);
          box-shadow:0 2px 1px -1px rgba(20,20,20,.12), 0 1px 1px 0 rgba(20,20,20,.06), 0 1px 3px 0 rgba(20,20,20,.04); }}
.ankidose .s {{ font-size:12px; color:{SUBTLE}; }}
.ankidose .row {{ display:flex; justify-content:space-between; align-items:baseline; gap:8px; }}
.ankidose .hero {{ display:flex; align-items:center; gap:14px; margin:12px 0; }}
.ankidose .main {{ display:flex; align-items:baseline; gap:6px; }}
.ankidose .main b {{ font-size:26px; font-weight:500; line-height:1.15; }}
.ankidose .main .s {{ font-size:14px; }}
.ankidose .sub {{ color:{SUBTLE}; margin-top:2px; }}
.ankidose .segs {{ display:flex; gap:4px; margin-bottom:8px; }}
.ankidose .seg {{ flex:1; min-width:2px; height:6px; border-radius:3px; background:{GRID}; overflow:hidden; }}
.ankidose .seg div {{ height:100%; }}
.ankidose details {{ margin-top:12px; padding-top:12px; border-top:1px solid {GRID}; }}
.ankidose summary {{ cursor:pointer; user-select:none; font-size:12px; color:{SUBTLE}; }}
.ankidose .kpis {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin:12px 0; }}
.ankidose .kpi {{ background:var(--canvas-inset, rgba(128,128,128,.1)); border:1px solid {GRID};
               border-radius:var(--border-radius, 6px); padding:8px 10px; white-space:nowrap; }}
.ankidose .kpi .s {{ margin-bottom:2px; }}
.ankidose .key {{ display:inline-flex; align-items:center; gap:4px; margin-left:10px; }}
.ankidose .actions {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-top:12px; }}
.ankidose .actions button {{ margin:0; font-size:12px; }}
.ankidose .actions .pri {{ background:{ACCENT}; color:#fff; border-color:transparent; }}
.ankidose .links {{ margin-left:auto; font-size:12px; }}
.ankidose .links a {{ color:{SUBTLE}; text-decoration:none; cursor:pointer; margin-left:10px; }}
.ankidose .links a:hover {{ color:var(--fg, inherit); text-decoration:underline; }}
.ankidose .x {{ background:none; border:none; box-shadow:none; padding:0 2px; margin:0;
               font-size:16px; line-height:1; color:{SUBTLE}; cursor:pointer; }}
</style>"""


def hero(st: Status) -> tuple[str, str, str]:
    """(main line, what goes next to it in gray, line below) depending on the plan's state."""
    if st.plan_complete:
        if st.done_total == 0:
            return "Nothing to review", "", "No reviews left in this deck."
        sub = "No reviews left in the deck." if st.out_of_cards and st.goal_lowered else "You won today."
        return "Plan complete", "", sub
    if st.finished:
        return str(st.done_total), f"/ {st.total}", f"Time's up · ~{fmt_dur(st.time_left)} to finish"
    if st.block_complete:
        if st.k + 1 < st.nblocks:
            return f"Block {st.k + 1} done", "", f"The next one opens at {hhmm(st.block_end)}."
        return "Last block done", "", ""
    return (
        str(st.done_block),
        f"/ {st.quota}",
        f"~{fmt_dur(st.block_time_left)} · until {hhmm(st.block_end)}",
    )


def unfinished_actions(st: Status) -> str:
    """What you can do when the plan's time runs out before you finish."""
    return f"""
<div class="actions">
  {button("+1 h", "extend:60", primary=True)}{button("+30 min", "extend:30")}{button("+2 h", "extend:120")}
  <span class="links">{link("New plan", "rest")}{link("Close", "close")}</span>
</div>"""


def render_panel(
    st: Status,
    deck_name: str,
    streak: int,
    accuracy: int | None,
    details_open: bool,
) -> str:
    main, beside, sub = hero(st)

    if st.plan_complete or st.finished:
        title, chip = escape(deck_name), ""
    else:
        where = "extra time" if st.extra else f"block {st.k + 1} of {st.nblocks}"
        title = f"{escape(deck_name)} · {where}"
        if st.extra:
            chip = ""  # "behind" vs. the original plan isn't useful once you chose to keep going
        elif st.delta > 0:
            chip = f'<span style="color:{GREEN}">+{st.delta} ahead</span>'
        elif st.delta < 0:
            chip = f'<span style="color:{AMBER}">{-st.delta} behind</span>'
        else:
            chip = '<span class="s">on pace</span>'

    total = f"{st.done_total} of {st.total} total"
    if st.goal_lowered and not st.plan_complete:
        total += f" (goal adjusted, was {st.planned_total})"
    learning = f"{st.learning} in learning" if st.learning else ""

    left = "none ✓" if st.plan_complete else f"~{fmt_dur(st.time_left)}"
    kpis = (
        kpi("Time left", left)
        + kpi("Accuracy", f"{accuracy}%" if accuracy is not None else "—")
        + kpi("Streak", plural(streak, "day"))
    )
    open_attr = " open" if details_open else ""
    beside_html = f'<span class="s">{beside}</span>' if beside else ""
    unfinished = st.finished and not st.plan_complete
    actions = unfinished_actions(st) if unfinished else ""
    footer = "" if unfinished else f'<div class="row s"><span>{total}</span><span>{learning}</span></div>'

    return f"""
<div class="ankidose">
{panel_css()}
<div class="row"><span class="s">{title}</span><span style="font-size:12px">{chip}</span></div>
<div class="hero">
  {ring_svg(st)}
  <div><div class="main"><b>{main}</b>{beside_html}</div><div class="sub">{sub}</div></div>
</div>
{segments_html(st)}
{footer}
{actions}
<details{open_attr} ontoggle="pycmd('ankidose:details:' + (this.open ? 1 : 0))">
  <summary>Details</summary>
  <div class="kpis">{kpis}</div>
  <div class="s" style="margin-bottom:4px">Your progress vs. the plan
    <span class="key"><span style="width:14px;height:3px;background:{ACCENT}"></span>you</span>
    <span class="key"><span style="width:14px;border-top:2px dashed {SUBTLE}"></span>plan</span></div>
  {pace_svg(st)}
</details>
</div>"""


# ---------------------------------------------------------------- summary of the last plan


def render_summary(last: dict, when: str, due: int, suggestion: tuple[int, float] | None) -> str:
    """Card shown after a plan from a previous day is closed: a goal for today and how it went."""
    if last.get("completed"):
        before = f"{when} you finished all {last['done']} 🎉"
    else:
        before = f"{when} you did {last['done']} of {last['total']}"
    if suggestion:
        cards, hours = suggestion
        main = f'<b>{cards}</b><span class="s">cards in {fmt_dur(hours * 3600)}</span>'
        sub = f"Suggested for today · {before}"
        actions = f"""<div class="actions">{button("Start plan", "suggest", primary=True)}
  <span class="links" style="margin-left:4px">{link("Edit", "custom")}</span></div>"""
    else:
        main = "<b>All caught up</b>"
        sub = f"No reviews due today · {before}"
        actions = ""

    return f"""
<div class="ankidose">
{panel_css()}
<div class="row"><span class="s">{escape(last.get('deck_name', ''))}</span>
  <button class="x" title="Dismiss" onclick="pycmd('ankidose:dismiss')">×</button></div>
<div class="main" style="margin-top:8px">{main}</div>
<div class="sub">{sub}</div>
{actions}
</div>"""
