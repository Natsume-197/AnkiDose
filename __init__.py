"""AnkiDose: spread N cards from a deck into blocks over X hours.

Progress is counted from the review log (revlog), so reviews done in AnkiDroid /
AnkiMobile also count as soon as you sync on desktop.
"""

from __future__ import annotations

import json
import math
import os
import time
from bisect import bisect_left
from datetime import date, datetime, timedelta

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSystemTrayIcon,
    QTimer,
    QVBoxLayout,
)
from aqt.utils import askUser, qconnect, showInfo, tooltip

from .panel import (
    ACCENT_CSS,
    MODES,
    Status,
    fmt_dur,
    hhmm,
    learning_note,
    message,
    overlay_state,
    plural,
    render_panel,
    short_text,
)

ADDON = __name__
TITLE = "AnkiDose"
TICK_MS = 20_000


# ---------------------------------------------------------------- config / plan / history


def cfg() -> dict:
    return mw.addonManager.getConfig(ADDON) or {}


def plan_mode(plan: dict) -> str:
    return plan.get("mode", "spread")


# The plan and history live in the add-on's own file (user_files/), NOT in the collection
# config: when syncing, Anki replaces the whole collection config with AnkiWeb's copy and
# the plan would disappear. user_files/ also survives add-on updates.


def store_path() -> str:
    folder = os.path.join(mw.addonManager.addonsFolder(ADDON), "user_files")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"state_{mw.pm.name}.json")


def read_store() -> dict:
    try:
        with open(store_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return data


def write_store(data: dict) -> None:
    path = store_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_plan() -> dict | None:
    if not mw.col:
        return None
    return read_store().get("plan") or None


def save_plan(plan: dict) -> None:
    data = read_store()
    data["plan"] = plan
    write_store(data)


def clear_plan() -> None:
    data = read_store()
    data.pop("plan", None)
    write_store(data)


def load_history() -> list[dict]:
    return read_store().get("history") or []


def save_history(history: list[dict]) -> None:
    data = read_store()
    data["history"] = history
    write_store(data)


def day_key(ts: float) -> str:
    """Anki day (respects the hour the next day starts at, 4:00 by default)."""
    try:
        rollover = int(mw.col.get_config("rollover", 4))
    except Exception:
        rollover = 4
    return datetime.fromtimestamp(ts - rollover * 3600).date().isoformat()


def log_plan(plan: dict, st: Status) -> None:
    """Save (or update) the plan's result in the history.

    Can be called more than once: if time runs out and you finish anyway, the plan's
    entry becomes "completed".
    """
    history = [h for h in load_history() if h.get("start") != plan["start"]]
    history.append(
        {
            "start": plan["start"],
            "day": day_key(plan["start"]),
            "deck": plan.get("deck_name", ""),
            "total": st.total,
            "done": st.done_total,
            "completed": st.plan_complete,
        }
    )
    save_history(history[-365:])


def streak() -> int:
    completed = {h["day"] for h in load_history() if h.get("completed")}
    d = date.fromisoformat(day_key(time.time()))
    if d.isoformat() not in completed:
        d -= timedelta(days=1)  # today doesn't break the streak yet
    n = 0
    while d.isoformat() in completed:
        n += 1
        d -= timedelta(days=1)
    return n


# ---------------------------------------------------------------- review log queries


def deck_ids(plan: dict) -> list[int]:
    try:
        return [int(i) for i in mw.col.decks.deck_and_child_ids(plan["did"])]
    except Exception:
        return [int(plan["did"])]


def counted_types(plan: dict) -> str:
    # revlog.type: 0 = learning (new cards), 1 = review, 2 = relearning, 3 = filtered deck.
    # Relearning (2) doesn't count: a failed card already counted when you failed it (just
    # like Anki's Due column), and cards failed before the plan aren't part of the goal.
    return "0,1,3" if plan.get("include_new") else "1,3"


def revlog_filter(plan: dict, types: str | None = None) -> str:
    ids = "(" + ",".join(str(i) for i in deck_ids(plan)) + ")"
    return (
        f"r.ease > 0 and r.type in ({types or counted_types(plan)}) "
        f"and (c.did in {ids} or c.odid in {ids})"
    )


def first_times(plan: dict) -> list[float]:
    """Time (s) of each card's first answer since the plan started."""
    sql = (
        "select min(r.id) from revlog r join cards c on c.id = r.cid "
        f"where r.id >= ? and {revlog_filter(plan)} group by r.cid"
    )
    return sorted(ms / 1000 for ms in mw.col.db.list(sql, int(plan["start"] * 1000)))


def accuracy(plan: dict) -> int | None:
    sql = (
        "select count(), sum(r.ease > 1) from revlog r join cards c on c.id = r.cid "
        f"where r.id >= ? and r.type in (1,3) and {revlog_filter(plan)}"
    )
    total, good = mw.col.db.first(sql, int(plan["start"] * 1000)) or (0, 0)
    return round(100 * (good or 0) / total) if total else None


def deck_counts(did: int, include_new: bool) -> tuple[int | None, int]:
    """(reviews still left today, cards in learning). None if it couldn't be read."""
    try:
        node = mw.col.sched.deck_due_tree(did)
        n = node.review_count + (node.new_count if include_new else 0)
        return n, node.learn_count
    except Exception:
        return None, 0


def due_count(did: int, include_new: bool) -> int:
    return deck_counts(did, include_new)[0] or 0


def seconds_per_card(plan: dict, since: float, min_cards: int) -> float | None:
    """Real time per counted card, including the time spent relearning failed cards."""
    all_types = "0,1,2,3" if plan.get("include_new") else "1,2,3"
    ms = mw.col.db.scalar(
        "select sum(r.time) from revlog r join cards c on c.id = r.cid "
        f"where r.id >= ? and {revlog_filter(plan, all_types)}",
        int(since * 1000),
    )
    # each card counts once per day
    n = mw.col.db.scalar(
        "select count(distinct r.cid || ':' || (r.id / 86400000)) from revlog r "
        f"join cards c on c.id = r.cid where r.id >= ? and {revlog_filter(plan)}",
        int(since * 1000),
    )
    if not ms or not n or n < min_cards:
        return None
    return min(120.0, max(1.0, ms / 1000 / n))


def estimate_spc(plan: dict) -> float:
    """Use your pace in this plan if there's enough data; otherwise the last 30 days; otherwise 10 s."""
    try:
        return (
            seconds_per_card(plan, plan["start"], 20)
            or seconds_per_card(plan, time.time() - 30 * 86400, 10)
            or 10.0
        )
    except Exception:
        return 10.0


# ---------------------------------------------------------------- status


def plan_shape(hours: float, block_min: int) -> int:
    return max(1, math.ceil(hours * 60 / block_min - 1e-9))


def block_quota(plan: dict, j: int, nblocks: int, done_before: int, total: int) -> int:
    if plan_mode(plan) == "carry":
        return max(0, math.ceil(total * (j + 1) / nblocks) - done_before)
    return math.ceil(max(0, total - done_before) / (nblocks - j))


def compute(plan: dict) -> Status:
    now = time.time()
    start = plan["start"]
    block_s = plan["block_min"] * 60
    end = start + plan["hours"] * 3600
    nblocks = plan_shape(plan["hours"], plan["block_min"])
    k = min(max(int((now - start) // block_s), 0), nblocks - 1)

    times = first_times(plan)
    done_total = len(times)

    # If the deck runs out of reviews before reaching the goal, the goal drops to what's left.
    # The due count Anki reports wobbles a little from card to card (buried siblings, daily
    # limits, studying from the parent deck), so the goal and each block's quota are frozen
    # and can only go down: otherwise the progress bar moves backwards.
    remaining, learning = deck_counts(plan["did"], plan.get("include_new", False))
    planned = plan["total"]
    total = planned if remaining is None else min(planned, done_total + remaining)
    dirty = False
    if total < plan.get("eff_total", planned + 1):
        plan["eff_total"] = total
        dirty = True
    total = plan.get("eff_total", total)

    def done_until(ts: float) -> int:
        return bisect_left(times, ts)

    frozen = plan.setdefault("quotas", {})  # each block's quota, frozen when the block starts
    blocks = []
    for j in range(k + 1):
        before = done_until(start + j * block_s)
        after = done_total if j == k else done_until(start + (j + 1) * block_s)
        if str(j) not in frozen:
            frozen[str(j)] = block_quota(plan, j, nblocks, before, total)
            dirty = True
        quota = frozen[str(j)]
        if j == k and remaining is not None:
            quota = min(quota, after - before + remaining)  # don't ask for more than what's left
        blocks.append((after - before, quota))
    if dirty:
        save_plan(plan)

    bstart = start + k * block_s
    return Status(
        k=k,
        nblocks=nblocks,
        quota=blocks[k][1],
        done_block=blocks[k][0],
        done_total=done_total,
        total=total,
        planned_total=planned,
        learning=learning,
        spc=estimate_spc(plan),
        out_of_cards=remaining == 0,
        start=start,
        block_len=block_s,
        block_start=bstart,
        block_end=min(bstart + block_s, end),
        end=end,
        now=now,
        blocks=blocks,
        times=times,
    )


def panel_for(plan: dict) -> str:
    st = compute(plan)
    return render_panel(
        st,
        plan.get("deck_name", ""),
        streak(),
        accuracy(plan),
        bool(read_store().get("ui", {}).get("details_open")),
    )


def on_js_message(handled, message, context):
    """The panel sends pycmd('ankidose:details:1/0') when you open or close "Details"."""
    if not isinstance(message, str) or not message.startswith("ankidose:details:"):
        return handled
    data = read_store()
    data.setdefault("ui", {})["details_open"] = message.endswith(":1")
    write_store(data)
    return (True, None)


# ---------------------------------------------------------------- notifications

_tray: QSystemTrayIcon | None = None


def system_notify(msg: str) -> None:
    global _tray
    if not cfg().get("system_notifications", True):
        return
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    if _tray is None:
        _tray = QSystemTrayIcon(mw.windowIcon(), mw)
        _tray.setToolTip(TITLE)
        qconnect(_tray.messageClicked, lambda: (mw.showNormal(), mw.activateWindow()))
    _tray.show()
    _tray.showMessage(TITLE, msg, QSystemTrayIcon.MessageIcon.Information, 10_000)


def beep() -> None:
    if cfg().get("sound", True):
        QApplication.beep()


def block_done_dialog(msg: str) -> None:
    box = QMessageBox(mw)
    box.setWindowTitle(TITLE)
    box.setText(msg)
    keep_going = box.addButton("Keep reviewing", QMessageBox.ButtonRole.AcceptRole)
    take_break = box.addButton("Take a break", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep_going)
    box.exec()
    if box.clickedButton() is take_break and mw.state == "review":
        mw.moveToState("deckBrowser")


def show_notice(msg: str, interactive: bool) -> None:
    beep()
    if interactive and mw.state == "review" and cfg().get("block_end_notice", "dialog") == "dialog":
        QTimer.singleShot(150, lambda: block_done_dialog(msg))
    else:
        tooltip(msg.replace("\n", "<br>"), period=6000)
        if not mw.isActiveWindow():
            system_notify(msg)


def notify_plan_complete(st: Status, interactive: bool) -> None:
    if st.out_of_cards and st.goal_lowered:
        msg = f"No reviews left in the deck! Plan complete with {plural(st.done_total, 'card')}. 🎉"
    else:
        msg = f"Plan complete! {st.done_total}/{st.total} cards. You won today. 🎉"
    s = streak()
    if s > 1:
        msg += f"\nStreak: {s} days in a row. 🔥"
    if learning_note(st):
        msg += f"\n{learning_note(st)}"
    show_notice(msg, interactive)


def notify_block_done(st: Status, interactive: bool) -> None:
    msg = f"Block {st.k + 1}/{st.nblocks} complete! ({st.done_block}/{st.quota})"
    if st.k + 1 < st.nblocks:
        msg += f"\nThe next block starts at {hhmm(st.block_end)}."
    else:
        msg += "\nThat was the last block."
    if st.delta >= 10:
        msg += f"\nYou're {st.delta} ahead of the plan. 💪"
    if learning_note(st):
        msg += f"\n{learning_note(st)}"
    show_notice(msg, interactive)


def notify_block_start(st: Status, mode: str) -> None:
    if st.quota == 0:
        msg = f"Block {st.k + 1}/{st.nblocks} is open, but you're ahead: nothing to do. 😎"
    else:
        msg = (
            f"Block {st.k + 1}/{st.nblocks} is open: {plural(st.quota, 'card')} "
            f"(~{fmt_dur(st.block_time_left)}) until {hhmm(st.block_end)}."
        )
        if st.delta <= -10:
            msg += f" {message(st, mode)}"
    beep()
    tooltip(msg, period=6000)
    system_notify(msg)


def notify_finished(st: Status) -> None:
    msg = f"The plan's time is up: you did {st.done_total}/{st.total} cards. Tomorrow will be better. 💪"
    tooltip(msg, period=6000)
    system_notify(msg)


# ---------------------------------------------------------------- core logic

_last_key: str | None = None


def check(interactive: bool = False) -> None:
    """Check the status and fire notifications. `interactive` = triggered by answering a card."""
    global _last_key
    plan = load_plan()
    if not plan:
        return
    try:
        st = compute(plan)
    except Exception:
        return

    changed = False
    if st.plan_complete:
        # goal reached, or the deck ran out of reviews
        if not plan.get("complete_notified"):
            plan["complete_notified"] = True
            changed = True
            log_plan(plan, st)  # before the notice so the streak already includes it
            notify_plan_complete(st, interactive)
    elif st.finished:
        if not plan.get("finished_notified"):
            plan["finished_notified"] = True
            changed = True
            log_plan(plan, st)
            notify_finished(st)
    else:
        if st.k > plan.get("announced", -1):
            plan["announced"] = st.k
            changed = True
            notify_block_start(st, plan_mode(plan))
        if st.quota > 0 and st.block_complete and plan.get("notified_done", -1) < st.k:
            plan["notified_done"] = st.k
            changed = True
            notify_block_done(st, interactive)

    if changed:
        save_plan(plan)

    update_reviewer_overlay(plan, st)
    key = f"{short_text(st)}|{st.delta}"
    if key != _last_key:
        _last_key = key
        if mw.state == "deckBrowser":
            mw.deckBrowser.refresh()
        elif mw.state == "overview":
            mw.overview.refresh()


def finalize_plan(plan: dict) -> None:
    """When stopping or replacing a plan, record its result in the history."""
    try:
        log_plan(plan, compute(plan))
    except Exception:
        pass


# ---------------------------------------------------------------- on-screen UI


def card_in_plan(plan: dict, card) -> bool:
    ids = deck_ids(plan)
    return card is not None and (card.did in ids or getattr(card, "odid", 0) in ids)


def update_reviewer_overlay(plan: dict | None = None, st: Status | None = None) -> None:
    if mw.state != "review" or not getattr(mw.reviewer, "web", None):
        return
    plan = plan or load_plan()
    state = None
    if plan and card_in_plan(plan, mw.reviewer.card):
        try:
            state = overlay_state(st or compute(plan))
        except Exception:
            state = None
    # Thin 3px line pinned to the top that fills up, with small text on the right.
    # Fixed 100vw width (includes the scrollbar): long answers show a scrollbar, and with
    # right:0 the line shrank and looked like it moved backwards when flipping the card.
    js = """(function(s){
  var bar = document.getElementById('ankidose-bar'), lab = document.getElementById('ankidose-lab');
  if (!bar) {
    var css = document.createElement('style'); css.textContent = __ACCENT__;
    document.head.appendChild(css);
    bar = document.createElement('div'); bar.id = 'ankidose-bar';
    bar.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:3px;z-index:9999;pointer-events:none;'
      + 'background:var(--border-subtle, rgba(128,128,128,.2));';
    // no animation when created: if Anki redraws the screen, the line must not start from 0
    bar.innerHTML = '<div style="height:100%;width:' + (s ? s.pct : 0) + '%"></div>';
    requestAnimationFrame(function(){ bar.firstChild.style.transition = 'width .3s ease'; });
    lab = document.createElement('div'); lab.id = 'ankidose-lab';
    lab.style.cssText = 'position:fixed;top:7px;left:0;width:100vw;box-sizing:border-box;padding-right:24px;'
      + 'text-align:right;z-index:9999;pointer-events:none;'
      + 'font:11px sans-serif;font-variant-numeric:tabular-nums;color:var(--fg-subtle, #888);';
    document.body.appendChild(bar); document.body.appendChild(lab);
  }
  bar.style.display = lab.style.display = s ? 'block' : 'none';
  if (!s) return;
  bar.firstChild.style.width = s.pct + '%';
  bar.firstChild.style.background = s.color;
  lab.textContent = s.text;
})(__STATE__);""".replace("__STATE__", json.dumps(state)).replace("__ACCENT__", json.dumps(ACCENT_CSS))
    mw.reviewer.web.eval(js)


def on_show_card(card) -> None:
    update_reviewer_overlay()


def on_answer(reviewer, card, ease) -> None:
    plan = load_plan()
    if plan and card_in_plan(plan, card):
        check(interactive=True)


def on_deck_browser(deck_browser, content) -> None:
    plan = load_plan()
    if plan:
        try:
            content.stats += panel_for(plan)
        except Exception:
            pass


def on_overview(overview, content) -> None:
    plan = load_plan()
    if plan and mw.col.decks.current()["id"] in deck_ids(plan):
        try:
            content.table += panel_for(plan)
        except Exception:
            pass


# ---------------------------------------------------------------- plan dialog


class PlanDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle(f"{TITLE}: new plan")
        c = cfg()

        self.deck = QComboBox()
        decks = sorted(mw.col.decks.all_names_and_ids(), key=lambda d: d.name.lower())
        current = mw.col.decks.current()["id"]
        for d in decks:
            self.deck.addItem(d.name, int(d.id))
            if d.id == current:
                self.deck.setCurrentIndex(self.deck.count() - 1)

        self.include_new = QCheckBox("Also count new cards")
        self.include_new.setChecked(bool(c.get("count_new", False)))

        self.total = QSpinBox()
        self.total.setRange(1, 100_000)

        self.hours = QDoubleSpinBox()
        self.hours.setRange(0.25, 24)
        self.hours.setSingleStep(0.5)
        self.hours.setSuffix(" h")
        self.hours.setValue(float(c.get("hours", 5)))

        self.block = QSpinBox()
        self.block.setRange(5, 600)
        self.block.setSingleStep(5)
        self.block.setSuffix(" min")
        self.block.setValue(int(c.get("block_minutes", 60)))

        self.mode = QComboBox()
        for key, label in MODES:
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(c.get("behind_mode", "spread"))))

        self.preview = QLabel()
        self.preview.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Deck:", self.deck)
        form.addRow("", self.include_new)
        form.addRow("Cards:", self.total)
        form.addRow("Within:", self.hours)
        form.addRow("One block every:", self.block)
        form.addRow("If I fall behind:", self.mode)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start")
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.preview)
        lay.addWidget(buttons)

        qconnect(self.deck.currentIndexChanged, self.refill_total)
        qconnect(self.include_new.toggled, self.refill_total)
        for w in (self.total, self.hours, self.block):
            qconnect(w.valueChanged, self.update_preview)
        self.refill_total()

    def refill_total(self, *_) -> None:
        did, include_new = self.deck.currentData(), self.include_new.isChecked()
        self.spc = estimate_spc({"did": did, "include_new": include_new, "start": time.time()})
        self.total.setValue(max(1, due_count(did, include_new)))
        self.update_preview()

    def update_preview(self, *_) -> None:
        n = plan_shape(self.hours.value(), self.block.value())
        per = math.ceil(self.total.value() / n)
        end = time.time() + self.hours.value() * 3600
        per_time = per * self.spc
        text = (
            f"<b>{plural(n, 'block')}</b> of ~<b>{per}</b> cards "
            f"(~<b>{fmt_dur(per_time)}</b> each), one every {self.block.value()} min.<br>"
            f"About {fmt_dur(self.total.value() * self.spc)} of reviewing in total. "
            f"You'll finish at <b>{hhmm(end)}</b>.<br>"
            f"<span style='color:gray'>Based on your pace: ~{round(self.spc)} s per card.</span>"
        )
        if per_time > self.block.value() * 60:
            text += "<br><b>Heads up:</b> each block would take longer than the block itself."
        self.preview.setText(text)

    def accept(self) -> None:
        old = load_plan()
        if old:
            finalize_plan(old)

        plan = {
            "did": self.deck.currentData(),
            "deck_name": self.deck.currentText(),
            "total": self.total.value(),
            "start": time.time(),
            "hours": self.hours.value(),
            "block_min": self.block.value(),
            "mode": self.mode.currentData(),
            "include_new": self.include_new.isChecked(),
            "announced": 0,
            "notified_done": -1,
        }
        save_plan(plan)

        c = cfg()
        c.update(
            hours=plan["hours"],
            block_minutes=plan["block_min"],
            behind_mode=plan["mode"],
            count_new=plan["include_new"],
        )
        mw.addonManager.writeConfig(ADDON, c)

        super().accept()
        st = compute(plan)
        tooltip(
            f"Plan started. Block 1/{st.nblocks}: {plural(st.quota, 'card')} "
            f"(~{fmt_dur(st.block_time_left)}) until {hhmm(st.block_end)}. 🚀",
            period=6000,
        )
        check()


# ---------------------------------------------------------------- menu


def new_plan() -> None:
    if not mw.col:
        return
    if load_plan() and not askUser("You already have an active plan. Replace it?"):
        return
    PlanDialog().exec()


def show_status() -> None:
    plan = load_plan()
    if not plan:
        showInfo("There's no active plan.", title=TITLE)
        return
    st = compute(plan)
    showInfo(
        f"<b>{plan['deck_name']}</b><br><br>"
        f"{short_text(st)}<br>"
        f"Total: {st.done_total}/{st.total}<br>"
        f"Started at {hhmm(plan['start'])} · ends at {hhmm(st.end)}<br><br>"
        f"{message(st, plan_mode(plan))}",
        title=TITLE,
        textFormat="rich",
    )


def stop_plan() -> None:
    plan = load_plan()
    if not plan:
        showInfo("There's no active plan.", title=TITLE)
        return
    if askUser("Stop the current plan?"):
        finalize_plan(plan)
        clear_plan()
        if _tray:
            _tray.hide()
        tooltip("Plan stopped.")
        if mw.state == "deckBrowser":
            mw.deckBrowser.refresh()
        elif mw.state == "overview":
            mw.overview.refresh()


def setup_menu() -> None:
    menu = QMenu(TITLE, mw)
    for label, fn in (
        ("New plan…", new_plan),
        ("Show status", show_status),
        ("Stop plan", stop_plan),
    ):
        act = QAction(label, mw)
        qconnect(act.triggered, fn)
        menu.addAction(act)
    mw.form.menuTools.addMenu(menu)


# ---------------------------------------------------------------- startup

setup_menu()

_timer = QTimer(mw)
qconnect(_timer.timeout, lambda: check())
_timer.start(TICK_MS)

gui_hooks.reviewer_did_show_question.append(on_show_card)
gui_hooks.reviewer_did_show_answer.append(on_show_card)
gui_hooks.reviewer_did_answer_card.append(on_answer)
gui_hooks.deck_browser_will_render_content.append(on_deck_browser)
gui_hooks.overview_will_render_content.append(on_overview)
gui_hooks.sync_did_finish.append(lambda: check())
gui_hooks.webview_did_receive_js_message.append(on_js_message)
