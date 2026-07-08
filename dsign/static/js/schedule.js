/**
 * Schedule grid UI (D2.3) — week view, drag/resize, panel, API wiring.
 */
import { fetchAPI } from './utils/api.js';
import { showAlert, showError } from './utils/alerts.js';
import { t, getUiLang, applyI18n } from './i18n.js';

const HOUR_START = 0;
const HOUR_END = 23;
const SLOT_HEIGHT = 36;
const MIN_STEP = 15;
const MAGNET_THRESHOLD = 8;
const OVERLAP_THRESHOLD = 4;

const DAY_KEYS = ['schedule_day_mon', 'schedule_day_tue', 'schedule_day_wed', 'schedule_day_thu', 'schedule_day_fri', 'schedule_day_sat', 'schedule_day_sun'];

let weekMonday = null;
let monthAnchor = null;
let viewMode = 'week';
let instances = [];
let dragState = null;
let resizeState = null;
let contextInstance = null;
let panelMode = 'edit';
let panelRuleId = null;
let panelInstanceDate = null;
let pendingDayMove = null;
let progressTimerId = null;
let isVisible = false;
let getPlaylists = () => [];

const els = {};

function popcount(n) {
    let v = Number(n) >>> 0;
    let c = 0;
    while (v) {
        c += v & 1;
        v >>= 1;
    }
    return c;
}

function firstOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
}

function mondayOf(date) {
    const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const dow = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - dow);
    d.setHours(0, 0, 0, 0);
    return d;
}

function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

function snapToGrid(px) {
    const stepPx = (MIN_STEP / 60) * SLOT_HEIGHT;
    return Math.round(px / stepPx) * stepPx;
}

function timeFromPosition(px) {
    const minutes = (px / SLOT_HEIGHT) * 60;
    const totalMinutes = minutes + HOUR_START * 60;
    const h = Math.min(HOUR_END, Math.floor(totalMinutes / 60));
    const m = Math.round((totalMinutes % 60) / MIN_STEP) * MIN_STEP;
    const adjH = h + Math.floor(m / 60);
    const adjM = m % 60;
    const clampH = Math.min(adjH, 23);
    const clampM = clampH === 23 ? Math.min(adjM, 45) : adjM;
    return {
        h: clampH,
        m: clampM,
        str: `${String(clampH).padStart(2, '0')}:${String(clampM).padStart(2, '0')}`,
    };
}

function positionFromTime(timeStr) {
    const [h, m] = String(timeStr || '00:00').split(':').map(Number);
    return ((h - HOUR_START) * 60 + (m || 0)) / 60 * SLOT_HEIGHT;
}

function gridHeightPx() {
    return (HOUR_END - HOUR_START + 1) * SLOT_HEIGHT;
}

function parseTimeToMinutes(timeStr) {
    const [h, m] = String(timeStr || '00:00').split(':').map(Number);
    return h * 60 + (m || 0);
}

function minutesToTimeStr(total) {
    const h = Math.floor(total / 60);
    const m = total % 60;
    return `${String(Math.min(h, 23)).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function collectDaysMask() {
    let mask = 0;
    els.dayToggles?.querySelectorAll('.schedule-day-btn.is-active').forEach((btn) => {
        mask |= parseInt(btn.dataset.day, 10) || 0;
    });
    return mask;
}

function setDaysMask(mask) {
    els.dayToggles?.querySelectorAll('.schedule-day-btn').forEach((btn) => {
        const bit = parseInt(btn.dataset.day, 10) || 0;
        btn.classList.toggle('is-active', (mask & bit) !== 0);
    });
}

function updateDayButtonLabels() {
    const lang = getUiLang();
    els.dayToggles?.querySelectorAll('.schedule-day-btn').forEach((btn, idx) => {
        btn.textContent = t(DAY_KEYS[idx], lang);
    });
}

function formatMonthLabel(anchor) {
    const lang = getUiLang();
    const locale = lang === 'en' ? 'en-US' : 'ru-RU';
    return new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(anchor);
}

function updatePeriodLabel() {
    if (!els.periodLabel) return;
    if (viewMode === 'month' && monthAnchor) {
        els.periodLabel.textContent = formatMonthLabel(monthAnchor);
    } else if (weekMonday) {
        els.periodLabel.textContent = formatWeekLabel(weekMonday);
    }
}

function formatWeekLabel(monday) {
    const lang = getUiLang();
    const locale = lang === 'en' ? 'en-US' : 'ru-RU';
    const sunday = new Date(monday);
    sunday.setDate(sunday.getDate() + 6);
    const fmt = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' });
    const fmtYear = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', year: 'numeric' });
    if (monday.getMonth() === sunday.getMonth() && monday.getFullYear() === sunday.getFullYear()) {
        return `${monday.getDate()}–${fmtYear.format(sunday)}`;
    }
    return `${fmt.format(monday)} – ${fmtYear.format(sunday)}`;
}

function priorityLabel(val) {
    const lang = getUiLang();
    const n = Number(val) || 5;
    if (n <= 3) return t('schedule_priority_tier_high', lang);
    if (n <= 7) return t('schedule_priority_tier_mid', lang);
    return t('schedule_priority_tier_low', lang);
}

async function reloadCurrentView() {
    if (viewMode === 'month') {
        await loadMonth();
    } else {
        await loadWeek();
    }
}

async function loadWeek() {
    if (!weekMonday) weekMonday = mondayOf(new Date());
    const anchor = isoDate(weekMonday);
    const resp = await fetchAPI('schedule/week', { query: { date: anchor } });
    if (!resp?.success) {
        throw new Error(resp?.error || 'Failed to load schedule week');
    }
    instances = Array.isArray(resp.instances) ? resp.instances : [];
    if (viewMode === 'week') {
        renderGrid();
        updatePeriodLabel();
    }
}

async function loadMonth() {
    if (!monthAnchor) monthAnchor = firstOfMonth(new Date());
    const anchor = isoDate(monthAnchor);
    const resp = await fetchAPI('schedule/month', { query: { date: anchor } });
    if (!resp?.success) {
        throw new Error(resp?.error || 'Failed to load schedule month');
    }
    instances = Array.isArray(resp.instances) ? resp.instances : [];
    if (viewMode === 'month') {
        renderMonth();
        updatePeriodLabel();
    }
}

function renderHours() {
    if (!els.timelineHours) return;
    els.timelineHours.innerHTML = '';
    for (let h = HOUR_START; h <= HOUR_END; h += 1) {
        const hour = document.createElement('div');
        hour.className = 'timeline-hour';
        hour.textContent = `${String(h).padStart(2, '0')}:00`;
        hour.style.height = `${SLOT_HEIGHT}px`;
        els.timelineHours.appendChild(hour);
    }
}

function renderDayHeaders() {
    if (!els.timelineDays || !weekMonday) return;
    const lang = getUiLang();
    const locale = lang === 'en' ? 'en-US' : 'ru-RU';
    const todayIso = isoDate(new Date());
    els.timelineDays.innerHTML = '';

    for (let i = 0; i < 7; i += 1) {
        const date = new Date(weekMonday);
        date.setDate(date.getDate() + i);
        const dayEl = document.createElement('div');
        dayEl.className = 'timeline-day';
        if (isoDate(date) === todayIso) dayEl.classList.add('is-today');

        const name = document.createElement('div');
        name.className = 'timeline-day-name';
        name.textContent = t(DAY_KEYS[i], lang);

        const dateEl = document.createElement('div');
        dateEl.className = 'timeline-day-date';
        dateEl.textContent = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(date);

        dayEl.append(name, dateEl);
        els.timelineDays.appendChild(dayEl);
    }

    updatePeriodLabel();
}

function slotClasses(inst) {
    const classes = ['schedule-item'];
    if (inst.is_playing_now) classes.push('is-live');
    else if (inst.is_expired) classes.push('is-expired');
    else classes.push('is-planned');
    if (!inst.is_active) classes.push('is-disabled');
    if (inst.has_conflict) classes.push('is-conflicted');
    return classes.join(' ');
}

function renderSlots() {
    if (!els.timelineSlots || !weekMonday) return;
    els.timelineSlots.innerHTML = '';
    const height = gridHeightPx();
    const todayIso = isoDate(new Date());
    const now = new Date();
    const nowMinutes = now.getHours() * 60 + now.getMinutes();

    for (let col = 0; col < 7; col += 1) {
        const date = new Date(weekMonday);
        date.setDate(date.getDate() + col);
        const dateIso = isoDate(date);

        const colEl = document.createElement('div');
        colEl.className = 'timeline-day-col';
        colEl.dataset.day = String(col);
        colEl.dataset.date = dateIso;
        colEl.style.height = `${height}px`;

        for (let h = HOUR_START; h <= HOUR_END; h += 1) {
            const line = document.createElement('div');
            line.className = 'timeline-slot-line';
            line.style.height = `${SLOT_HEIGHT}px`;
            colEl.appendChild(line);
        }

        const dayInstances = instances.filter((s) => s.date === dateIso);
        dayInstances.forEach((inst) => {
            const slotEl = document.createElement('div');
            slotEl.className = slotClasses(inst);
            slotEl.dataset.instanceId = inst.id;
            slotEl.dataset.ruleId = String(inst.rule_id);

            const top = positionFromTime(inst.start_time);
            const bottom = positionFromTime(inst.end_time);
            slotEl.style.top = `${top}px`;
            slotEl.style.height = `${Math.max(snapToGrid((MIN_STEP / 60) * SLOT_HEIGHT), bottom - top)}px`;

            const title = document.createElement('div');
            title.className = 'schedule-item-title';
            title.textContent = inst.playlist_name || `#${inst.playlist_id}`;

            const meta = document.createElement('div');
            meta.className = 'schedule-item-meta';
            meta.textContent = `${inst.start_time}–${inst.end_time}`;

            slotEl.append(title, meta);

            if (inst.is_expired) {
                const badge = document.createElement('div');
                badge.className = 'schedule-item-badge';
                badge.textContent = t('schedule_badge_archived', getUiLang());
                slotEl.appendChild(badge);
            }

            if (inst.is_playing_now && Number(inst.progress_percent) > 0) {
                const prog = document.createElement('div');
                prog.className = 'schedule-item-progress';
                prog.style.width = `${Math.min(100, Math.max(0, inst.progress_percent))}%`;
                slotEl.appendChild(prog);
            }

            const handle = document.createElement('div');
            handle.className = 'schedule-item-resize-handle';
            handle.dataset.edge = 'bottom';
            slotEl.appendChild(handle);

            if (!inst.is_expired) {
                slotEl.addEventListener('mousedown', (e) => onSlotMouseDown(e, slotEl, inst));
                handle.addEventListener('mousedown', (e) => onResizeStart(e, slotEl, inst));
                slotEl.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    showContextMenu(e, inst);
                });
            }

            colEl.appendChild(slotEl);
        });

        colEl.addEventListener('click', (e) => {
            if (e.target === colEl || e.target.classList.contains('timeline-slot-line')) {
                onEmptyClick(colEl, e);
            }
        });

        if (dateIso === todayIso) {
            const startMin = HOUR_START * 60;
            const endMin = (HOUR_END + 1) * 60;
            if (nowMinutes >= startMin && nowMinutes <= endMin) {
                const nowLine = document.createElement('div');
                nowLine.className = 'now-line';
                const px = ((nowMinutes - startMin) / 60) * SLOT_HEIGHT;
                nowLine.style.top = `${px}px`;
                colEl.appendChild(nowLine);
            }
        }

        els.timelineSlots.appendChild(colEl);
    }
}

function renderGrid() {
    renderHours();
    renderDayHeaders();
    renderSlots();
}

function monthChipClass(inst) {
    const classes = ['schedule-month-chip'];
    if (inst.is_playing_now) classes.push('is-live');
    else classes.push('is-planned');
    if (!inst.is_active) classes.push('is-disabled');
    if (inst.has_conflict) classes.push('is-conflicted');
    return classes.join(' ');
}

function renderMonth() {
    if (!els.monthWeekdays || !els.monthGrid || !monthAnchor) return;
    const lang = getUiLang();
    const todayIso = isoDate(new Date());
    const year = monthAnchor.getFullYear();
    const month = monthAnchor.getMonth();
    const firstDay = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const leadPad = (firstDay.getDay() + 6) % 7;

    els.monthWeekdays.innerHTML = '';
    for (let i = 0; i < 7; i += 1) {
        const el = document.createElement('div');
        el.className = 'schedule-month-weekday';
        el.textContent = t(DAY_KEYS[i], lang);
        els.monthWeekdays.appendChild(el);
    }

    els.monthGrid.innerHTML = '';
    const totalCells = leadPad + daysInMonth;
    const trailing = (7 - (totalCells % 7)) % 7;
    const cellCount = totalCells + trailing;

    for (let cell = 0; cell < cellCount; cell += 1) {
        const cellEl = document.createElement('div');
        cellEl.className = 'schedule-month-cell';

        if (cell < leadPad || cell >= leadPad + daysInMonth) {
            cellEl.classList.add('is-outside');
            els.monthGrid.appendChild(cellEl);
            continue;
        }

        const dom = cell - leadPad + 1;
        const date = new Date(year, month, dom);
        const dateIso = isoDate(date);
        if (dateIso === todayIso) cellEl.classList.add('is-today');

        const dayNum = document.createElement('div');
        dayNum.className = 'schedule-month-daynum';
        dayNum.textContent = String(dom);
        cellEl.appendChild(dayNum);

        const slotsWrap = document.createElement('div');
        slotsWrap.className = 'schedule-month-slots';
        const dayInstances = instances
            .filter((s) => s.date === dateIso && !s.is_expired)
            .sort((a, b) => a.start_time.localeCompare(b.start_time));
        const maxChips = 3;
        dayInstances.slice(0, maxChips).forEach((inst) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = monthChipClass(inst);
            chip.title = `${inst.start_time}–${inst.end_time} ${inst.playlist_name || ''}`;
            chip.textContent = `${inst.start_time} ${inst.playlist_name || `#${inst.playlist_id}`}`;
            chip.addEventListener('click', (e) => {
                e.stopPropagation();
                openEditPanel(inst);
            });
            chip.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                e.stopPropagation();
                showContextMenu(e, inst);
            });
            slotsWrap.appendChild(chip);
        });
        if (dayInstances.length > maxChips) {
            const more = document.createElement('div');
            more.className = 'schedule-month-more';
            more.textContent = `+${dayInstances.length - maxChips} ${t('schedule_month_more', lang)}`;
            slotsWrap.appendChild(more);
        }
        cellEl.appendChild(slotsWrap);

        cellEl.addEventListener('click', () => {
            weekMonday = mondayOf(date);
            setViewMode('week');
        });

        els.monthGrid.appendChild(cellEl);
    }
}

function setViewMode(mode) {
    viewMode = mode === 'month' ? 'month' : 'week';
    els.viewWeekTab?.classList.toggle('active', viewMode === 'week');
    els.viewMonthTab?.classList.toggle('active', viewMode === 'month');
    els.viewWeekTab?.setAttribute('aria-selected', viewMode === 'week' ? 'true' : 'false');
    els.viewMonthTab?.setAttribute('aria-selected', viewMode === 'month' ? 'true' : 'false');

    if (els.weekView) {
        els.weekView.hidden = viewMode !== 'week';
    }
    if (els.monthView) {
        els.monthView.hidden = viewMode !== 'month';
    }

    if (viewMode === 'month') {
        if (!monthAnchor) monthAnchor = firstOfMonth(weekMonday || new Date());
        loadMonth().catch((e) => showError(e.message));
    } else {
        if (!weekMonday) weekMonday = mondayOf(new Date());
        loadWeek().catch((e) => showError(e.message));
    }
}

function findInstance(id) {
    return instances.find((s) => s.id === id) || null;
}

function onSlotMouseDown(e, slotEl, inst) {
    if (e.button !== 0 || inst.is_expired) return;
    if (e.target.classList.contains('schedule-item-resize-handle')) return;
    e.preventDefault();
    e.stopPropagation();

    const startY = e.clientY;
    const startX = e.clientX;
    let dragging = false;

    const moveHandler = (ev) => {
        const dy = Math.abs(ev.clientY - startY);
        const dx = Math.abs(ev.clientX - startX);
        if (!dragging && (dy > MAGNET_THRESHOLD || dx > MAGNET_THRESHOLD)) {
            dragging = true;
            dragState = {
                instanceId: inst.id,
                ruleId: inst.rule_id,
                slotEl,
                startY,
                startX,
                originalTop: parseFloat(slotEl.style.top),
                originalDay: inst.day_of_week,
                originalDate: inst.date,
                daysOfWeek: inst.days_of_week,
                repeatType: inst.repeat_type,
                startTime: inst.start_time,
                endTime: inst.end_time,
                playlistId: inst.playlist_id,
                priority: inst.priority,
                targetDay: inst.day_of_week,
                targetDate: inst.date,
            };
            slotEl.classList.add('is-dragging');
            document.body.style.cursor = 'grabbing';
        }
        if (dragging) onDragMove(ev);
    };

    const upHandler = async (ev) => {
        document.removeEventListener('mousemove', moveHandler);
        document.removeEventListener('mouseup', upHandler);
        if (!dragging) {
            openEditPanel(inst);
            return;
        }
        await onDragEnd(ev);
    };

    document.addEventListener('mousemove', moveHandler);
    document.addEventListener('mouseup', upHandler);
}

function onDragMove(e) {
    if (!dragState) return;
    const { slotEl, startY, originalTop } = dragState;
    const deltaY = e.clientY - startY;
    const rawTop = originalTop + deltaY;
    const maxTop = gridHeightPx() - parseFloat(slotEl.style.height);
    const snappedTop = snapToGrid(Math.max(0, Math.min(rawTop, maxTop)));
    slotEl.style.top = `${snappedTop}px`;

    const gridRect = els.timelineSlots.getBoundingClientRect();
    const colWidth = gridRect.width / 7;
    const relativeX = e.clientX - gridRect.left;
    let newCol = Math.floor(relativeX / colWidth);
    newCol = Math.max(0, Math.min(6, newCol));

    if (newCol !== dragState.originalDay) {
        const targetCol = els.timelineSlots.children[newCol];
        if (targetCol && slotEl.parentElement !== targetCol) {
            const overlap = Math.abs(relativeX - (newCol + 0.5) * colWidth);
            if (overlap > OVERLAP_THRESHOLD || Math.abs(newCol - dragState.originalDay) >= 1) {
                targetCol.appendChild(slotEl);
                dragState.targetDay = newCol;
                dragState.targetDate = targetCol.dataset.date;
            }
        }
    } else {
        dragState.targetDay = dragState.originalDay;
        dragState.targetDate = dragState.originalDate;
    }
}

async function onDragEnd() {
    if (!dragState) return;
    const state = dragState;
    dragState = null;

    state.slotEl.classList.remove('is-dragging');
    document.body.style.cursor = '';

    const finalTop = parseFloat(state.slotEl.style.top);
    const height = parseFloat(state.slotEl.style.height);
    const startTime = timeFromPosition(finalTop);
    const endMinutes = (finalTop + height) / SLOT_HEIGHT * 60 + HOUR_START * 60;
    const endTimeStr = minutesToTimeStr(Math.round(endMinutes / MIN_STEP) * MIN_STEP);

    const dayChanged = state.targetDay !== state.originalDay;

    if (dayChanged) {
        pendingDayMove = {
            ...state,
            start_time: startTime.str,
            end_time: endTimeStr,
        };
        if (!isVisible) {
            dismissScheduleOverlays();
            return;
        }
        const canTransfer = popcount(state.daysOfWeek) === 1;
        if (els.dayTransferBtn) els.dayTransferBtn.hidden = !canTransfer;
        if (els.dayDialog) els.dayDialog.hidden = false;
        return;
    }

    try {
        await updateRuleTimes(state.ruleId, startTime.str, endTimeStr);
        showAlert(t('schedule_saved', getUiLang()), 'success');
        await reloadCurrentView();
    } catch (err) {
        showError(err.message || 'Save failed');
        await reloadCurrentView();
    }
}

function onResizeStart(e, slotEl, inst) {
    e.preventDefault();
    e.stopPropagation();
    resizeState = {
        ruleId: inst.rule_id,
        slotEl,
        startY: e.clientY,
        originalHeight: parseFloat(slotEl.style.height),
        top: parseFloat(slotEl.style.top),
    };
    slotEl.classList.add('is-resizing');
    document.body.style.cursor = 'ns-resize';

    const moveHandler = (ev) => onResizeMove(ev);
    const upHandler = async () => {
        document.removeEventListener('mousemove', moveHandler);
        document.removeEventListener('mouseup', upHandler);
        await onResizeEnd();
    };
    document.addEventListener('mousemove', moveHandler);
    document.addEventListener('mouseup', upHandler);
}

function onResizeMove(e) {
    if (!resizeState) return;
    const { slotEl, startY, originalHeight, top } = resizeState;
    const deltaY = e.clientY - startY;
    const minHeight = (MIN_STEP / 60) * SLOT_HEIGHT;
    const maxHeight = gridHeightPx() - top;
    const newHeight = snapToGrid(originalHeight + deltaY);
    slotEl.style.height = `${Math.max(minHeight, Math.min(newHeight, maxHeight))}px`;
}

async function onResizeEnd() {
    if (!resizeState) return;
    const { ruleId, slotEl, top } = resizeState;
    resizeState = null;
    slotEl.classList.remove('is-resizing');
    document.body.style.cursor = '';

    const height = parseFloat(slotEl.style.height);
    const endMinutes = (top + height) / SLOT_HEIGHT * 60 + HOUR_START * 60;
    const endTimeStr = minutesToTimeStr(Math.round(endMinutes / MIN_STEP) * MIN_STEP);
    const startTime = timeFromPosition(top).str;

    try {
        await updateRuleTimes(ruleId, startTime, endTimeStr);
        showAlert(t('schedule_saved', getUiLang()), 'success');
        await reloadCurrentView();
    } catch (err) {
        showError(err.message || 'Save failed');
        await reloadCurrentView();
    }
}

async function updateRuleTimes(ruleId, startTime, endTime) {
    const resp = await fetchAPI(`schedule/rules/${ruleId}`, {
        method: 'PUT',
        body: { start_time: startTime, end_time: endTime },
    });
    if (!resp?.success) throw new Error(resp?.error || 'Update failed');
}

function onEmptyClick(col, e) {
    const rect = col.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const snappedY = snapToGrid(y);
    const startTime = timeFromPosition(snappedY);
    let endMin = parseTimeToMinutes(startTime.str) + MIN_STEP;
    if (endMin > 24 * 60) endMin = 24 * 60 - MIN_STEP;
    openCreatePanel({
        date: col.dataset.date,
        day: parseInt(col.dataset.day, 10),
        start_time: startTime.str,
        end_time: minutesToTimeStr(endMin),
    });
}

function populatePlaylistSelect() {
    if (!els.slotPlaylist) return;
    const playlists = getPlaylists() || [];
    const lang = getUiLang();
    els.slotPlaylist.innerHTML = playlists.map((p) => {
        const label = `${p.name || t('unnamed', lang)} (${p.customer || '—'})`;
        return `<option value="${p.id}">${label}</option>`;
    }).join('');
    if (!playlists.length) {
        els.slotPlaylist.innerHTML = `<option value="">${t('schedule_no_playlists', lang)}</option>`;
    }
}

function detectConflicts(draft) {
    return instances.filter((other) => {
        if (panelMode === 'edit' && other.rule_id === panelRuleId && other.date === draft.date) return false;
        if (other.date !== draft.date || other.is_expired) return false;
        const s = parseTimeToMinutes(draft.start_time);
        const e = parseTimeToMinutes(draft.end_time);
        const os = parseTimeToMinutes(other.start_time);
        const oe = parseTimeToMinutes(other.end_time);
        return s < oe && os < e;
    });
}

function updateConflictWarning(draft) {
    const conflicts = detectConflicts(draft);
    if (!els.conflictWarning) return;
    if (conflicts.length) {
        els.conflictName.textContent = conflicts[0].playlist_name || '';
        els.conflictWarning.hidden = false;
    } else {
        els.conflictWarning.hidden = true;
    }
}

function openEditPanel(inst) {
    panelMode = 'edit';
    panelRuleId = inst.rule_id;
    panelInstanceDate = inst.date;
    populatePlaylistSelect();

    if (els.panelTitle) els.panelTitle.textContent = t('schedule_panel_edit', getUiLang());
    if (els.slotPlaylist) els.slotPlaylist.value = String(inst.playlist_id);
    if (els.slotStart) els.slotStart.value = inst.start_time;
    if (els.slotEnd) els.slotEnd.value = inst.end_time;
    if (els.slotRepeat) els.slotRepeat.value = inst.repeat_type || 'weekly';
    if (els.slotValidFrom) els.slotValidFrom.value = inst.valid_from || inst.date || '';
    if (els.slotValidUntil) els.slotValidUntil.value = inst.valid_until || '';
    if (els.slotPriority) els.slotPriority.value = String(inst.priority || 5);
    if (els.priorityValue) els.priorityValue.textContent = priorityLabel(inst.priority);
    if (els.archiveBtn) els.archiveBtn.hidden = false;

    setDaysMask(inst.days_of_week || (1 << inst.day_of_week));
    updateConflictWarning({
        date: inst.date,
        start_time: inst.start_time,
        end_time: inst.end_time,
    });
    openPanel();
}

function openCreatePanel(initial) {
    panelMode = 'create';
    panelRuleId = null;
    panelInstanceDate = initial.date;
    populatePlaylistSelect();

    const playlists = getPlaylists() || [];
    if (els.panelTitle) els.panelTitle.textContent = t('schedule_panel_create', getUiLang());
    if (els.slotPlaylist && playlists.length) els.slotPlaylist.value = String(playlists[0].id);
    if (els.slotStart) els.slotStart.value = initial.start_time;
    if (els.slotEnd) els.slotEnd.value = initial.end_time;
    if (els.slotRepeat) els.slotRepeat.value = 'once';
    if (els.slotValidFrom) els.slotValidFrom.value = initial.date;
    if (els.slotValidUntil) els.slotValidUntil.value = '';
    if (els.slotPriority) els.slotPriority.value = '5';
    if (els.priorityValue) els.priorityValue.textContent = priorityLabel(5);
    if (els.archiveBtn) els.archiveBtn.hidden = true;
    if (els.conflictWarning) els.conflictWarning.hidden = true;

    setDaysMask(1 << initial.day);
    openPanel();
}

function openPanel() {
    if (els.slideOverlay) {
        els.slideOverlay.hidden = false;
        els.slideOverlay.classList.add('is-open');
    }
    if (els.slidePanel) {
        els.slidePanel.hidden = false;
        requestAnimationFrame(() => els.slidePanel.classList.add('is-open'));
    }
}

function closePanel() {
    if (els.slideOverlay) {
        els.slideOverlay.classList.remove('is-open');
        els.slideOverlay.hidden = true;
    }
    if (els.slidePanel) {
        els.slidePanel.classList.remove('is-open');
        els.slidePanel.hidden = true;
    }
}

function readPanelPayload() {
    const mask = collectDaysMask();
    if (!mask) throw new Error(t('schedule_err_days', getUiLang()));
    const start = els.slotStart?.value;
    const end = els.slotEnd?.value;
    if (!start || !end || start >= end) throw new Error(t('schedule_err_time', getUiLang()));

    const repeat = els.slotRepeat?.value || 'weekly';
    const payload = {
        playlist_id: parseInt(els.slotPlaylist?.value, 10),
        days_of_week: mask,
        start_time: start,
        end_time: end,
        repeat_type: repeat,
        priority: parseInt(els.slotPriority?.value, 10) || 5,
        enabled: true,
        valid_from: els.slotValidFrom?.value || null,
        valid_until: els.slotValidUntil?.value || null,
    };
    if (!payload.playlist_id) throw new Error(t('schedule_err_playlist', getUiLang()));
    if (repeat === 'once' && !payload.valid_from) {
        payload.valid_from = panelInstanceDate;
    }
    if (repeat === 'monthly' && !payload.valid_from) {
        payload.valid_from = panelInstanceDate;
    }
    return payload;
}

async function saveSlot() {
    try {
        const payload = readPanelPayload();
        const conflicts = detectConflicts({
            date: panelInstanceDate,
            start_time: payload.start_time,
            end_time: payload.end_time,
        });
        if (conflicts.length) {
            updateConflictWarning({
                date: panelInstanceDate,
                start_time: payload.start_time,
                end_time: payload.end_time,
            });
        }

        let resp;
        if (panelMode === 'edit' && panelRuleId) {
            resp = await fetchAPI(`schedule/rules/${panelRuleId}`, { method: 'PUT', body: payload });
        } else {
            resp = await fetchAPI('schedule/rules', { method: 'POST', body: payload });
        }
        if (!resp?.success) throw new Error(resp?.error || 'Save failed');
        closePanel();
        showAlert(t('schedule_saved', getUiLang()), 'success');
        await reloadCurrentView();
    } catch (err) {
        showError(err.message || 'Save failed');
    }
}

async function archiveRule(ruleId) {
    const resp = await fetchAPI(`schedule/rules/${ruleId}/archive`, { method: 'PATCH' });
    if (!resp?.success) throw new Error(resp?.error || 'Archive failed');
}

async function toggleRule(ruleId) {
    const resp = await fetchAPI(`schedule/rules/${ruleId}/toggle`, { method: 'PATCH' });
    if (!resp?.success) throw new Error(resp?.error || 'Toggle failed');
}

async function skipInstanceDay(inst) {
    const resp = await fetchAPI('schedule/exceptions', {
        method: 'POST',
        body: { rule_id: inst.rule_id, date: inst.date },
    });
    if (!resp?.success) throw new Error(resp?.error || 'Skip failed');
}

function canSkipInstance(inst) {
    const repeat = inst?.repeat_type || 'weekly';
    return repeat === 'weekly' || repeat === 'monthly';
}

function showContextMenu(e, inst) {
    contextInstance = inst;
    if (!els.contextMenu) return;
    if (els.ctxSkipDay) {
        els.ctxSkipDay.hidden = !canSkipInstance(inst);
    }
    els.contextMenu.hidden = false;
    els.contextMenu.style.left = `${e.clientX}px`;
    els.contextMenu.style.top = `${e.clientY}px`;
}

function hideContextMenu() {
    if (els.contextMenu) els.contextMenu.hidden = true;
    contextInstance = null;
}

async function commitDayTransfer(mode) {
    if (!pendingDayMove) return;
    const move = pendingDayMove;
    pendingDayMove = null;
    if (els.dayDialog) els.dayDialog.hidden = true;

    try {
        if (mode === 'transfer') {
            const oldBit = 1 << move.originalDay;
            const newBit = 1 << move.targetDay;
            const newMask = (move.daysOfWeek ^ oldBit) | newBit;
            const resp = await fetchAPI(`schedule/rules/${move.ruleId}`, {
                method: 'PUT',
                body: {
                    days_of_week: newMask,
                    start_time: move.start_time,
                    end_time: move.end_time,
                },
            });
            if (!resp?.success) throw new Error(resp?.error || 'Transfer failed');
        } else if (mode === 'duplicate') {
            const resp = await fetchAPI('schedule/rules', {
                method: 'POST',
                body: {
                    playlist_id: move.playlistId,
                    days_of_week: 1 << move.targetDay,
                    start_time: move.start_time,
                    end_time: move.end_time,
                    repeat_type: 'once',
                    valid_from: move.targetDate,
                    priority: move.priority,
                    enabled: true,
                },
            });
            if (!resp?.success) throw new Error(resp?.error || 'Duplicate failed');
        } else {
            await reloadCurrentView();
            return;
        }
        showAlert(t('schedule_saved', getUiLang()), 'success');
        await reloadCurrentView();
    } catch (err) {
        showError(err.message || 'Operation failed');
        await reloadCurrentView();
    }
}

function dismissDayDialog({ reload = true } = {}) {
    pendingDayMove = null;
    if (els.dayDialog) els.dayDialog.hidden = true;
    if (reload && isVisible) {
        reloadCurrentView().catch(() => {});
    }
}

function dismissScheduleOverlays() {
    dragState = null;
    resizeState = null;
    dismissDayDialog({ reload: false });
    closePanel();
    hideContextMenu();
}

function cancelDayDialog() {
    dismissDayDialog({ reload: true });
}

function bindPanelInputs() {
    els.slotPriority?.addEventListener('input', () => {
        if (els.priorityValue) {
            els.priorityValue.textContent = priorityLabel(els.slotPriority.value);
        }
    });
    const refreshConflict = () => {
        if (!panelInstanceDate) return;
        updateConflictWarning({
            date: panelInstanceDate,
            start_time: els.slotStart?.value,
            end_time: els.slotEnd?.value,
        });
    };
    els.slotStart?.addEventListener('change', refreshConflict);
    els.slotEnd?.addEventListener('change', refreshConflict);
    els.dayToggles?.querySelectorAll('.schedule-day-btn').forEach((btn) => {
        btn.addEventListener('click', () => btn.classList.toggle('is-active'));
    });
}

function shiftPeriod(delta) {
    if (viewMode === 'month') {
        if (!monthAnchor) monthAnchor = firstOfMonth(new Date());
        monthAnchor = new Date(monthAnchor.getFullYear(), monthAnchor.getMonth() + delta, 1);
        loadMonth().catch((e) => showError(e.message));
    } else {
        if (!weekMonday) weekMonday = mondayOf(new Date());
        weekMonday.setDate(weekMonday.getDate() + delta * 7);
        loadWeek().catch((e) => showError(e.message));
    }
}

function goToToday() {
    if (viewMode === 'month') {
        monthAnchor = firstOfMonth(new Date());
        loadMonth().catch((e) => showError(e.message));
    } else {
        weekMonday = mondayOf(new Date());
        loadWeek().catch((e) => showError(e.message));
    }
}

function bindChrome() {
    els.prevPeriod?.addEventListener('click', () => shiftPeriod(-1));
    els.nextPeriod?.addEventListener('click', () => shiftPeriod(1));
    els.goToday?.addEventListener('click', goToToday);

    els.viewWeekTab?.addEventListener('click', () => setViewMode('week'));
    els.viewMonthTab?.addEventListener('click', () => setViewMode('month'));

    els.panelClose?.addEventListener('click', closePanel);
    els.panelCancel?.addEventListener('click', closePanel);
    els.slideOverlay?.addEventListener('click', closePanel);
    els.panelSave?.addEventListener('click', () => saveSlot());
    els.panelArchive?.addEventListener('click', async () => {
        if (!panelRuleId) return;
        try {
            await archiveRule(panelRuleId);
            closePanel();
            showAlert(t('schedule_archived', getUiLang()), 'success');
            await reloadCurrentView();
        } catch (err) {
            showError(err.message);
        }
    });

    els.contextMenu?.querySelectorAll('[data-action]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const inst = contextInstance;
            hideContextMenu();
            if (!inst) return;
            const action = btn.dataset.action;
            try {
                if (action === 'edit') openEditPanel(inst);
                else if (action === 'toggle') {
                    await toggleRule(inst.rule_id);
                    showAlert(t('schedule_toggled', getUiLang()), 'success');
                    await reloadCurrentView();
                } else if (action === 'skip-day') {
                    await skipInstanceDay(inst);
                    showAlert(t('schedule_skip_day_ok', getUiLang()), 'success');
                    await reloadCurrentView();
                } else if (action === 'archive') {
                    await archiveRule(inst.rule_id);
                    showAlert(t('schedule_archived', getUiLang()), 'success');
                    await reloadCurrentView();
                }
            } catch (err) {
                showError(err.message);
            }
        });
    });

    els.dayCancel?.addEventListener('click', cancelDayDialog);
    els.dayDuplicate?.addEventListener('click', () => commitDayTransfer('duplicate'));
    els.dayTransferBtn?.addEventListener('click', () => commitDayTransfer('transfer'));
    els.dayDialog?.querySelector('[data-close]')?.addEventListener('click', cancelDayDialog);

    document.addEventListener('click', (e) => {
        if (!e.target.closest('#schedule-context-menu')
            && !e.target.closest('.schedule-item')
            && !e.target.closest('.schedule-month-chip')) {
            hideContextMenu();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (!isVisible) return;
        const tag = e.target?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

        if (e.key === 'Escape') {
            hideContextMenu();
            if (!els.dayDialog?.hidden) cancelDayDialog();
            else closePanel();
            return;
        }
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            shiftPeriod(-1);
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            shiftPeriod(1);
        } else if (e.key === 't' || e.key === 'T') {
            e.preventDefault();
            goToToday();
        }
    });
}

function startProgressTimer() {
    stopProgressTimer();
    progressTimerId = setInterval(() => {
        if (!isVisible) return;
        reloadCurrentView().catch(() => {});
    }, 5000);
}

function stopProgressTimer() {
    if (progressTimerId) {
        clearInterval(progressTimerId);
        progressTimerId = null;
    }
}

function cacheElements() {
    els.periodLabel = document.getElementById('schedule-week-label');
    els.timelineDays = document.getElementById('timeline-days');
    els.timelineHours = document.getElementById('timeline-hours');
    els.timelineSlots = document.getElementById('timeline-slots');
    els.prevPeriod = document.getElementById('schedule-prev-period');
    els.nextPeriod = document.getElementById('schedule-next-period');
    els.goToday = document.getElementById('schedule-go-today');
    els.viewWeekTab = document.getElementById('schedule-view-week');
    els.viewMonthTab = document.getElementById('schedule-view-month');
    els.weekView = document.getElementById('schedule-week-view');
    els.monthView = document.getElementById('schedule-month-view');
    els.monthWeekdays = document.getElementById('schedule-month-weekdays');
    els.monthGrid = document.getElementById('schedule-month-grid');
    els.slideOverlay = document.getElementById('schedule-slide-overlay');
    els.slidePanel = document.getElementById('schedule-slide-panel');
    els.panelTitle = document.getElementById('schedule-panel-title');
    els.panelClose = document.getElementById('schedule-panel-close');
    els.panelCancel = document.getElementById('schedule-panel-cancel');
    els.panelSave = document.getElementById('schedule-panel-save');
    els.panelArchive = document.getElementById('schedule-panel-archive');
    els.archiveBtn = els.panelArchive;
    els.slotPlaylist = document.getElementById('slot-playlist');
    els.slotStart = document.getElementById('slot-start');
    els.slotEnd = document.getElementById('slot-end');
    els.slotRepeat = document.getElementById('slot-repeat');
    els.slotValidFrom = document.getElementById('slot-valid-from');
    els.slotValidUntil = document.getElementById('slot-valid-until');
    els.slotPriority = document.getElementById('slot-priority');
    els.priorityValue = document.getElementById('slot-priority-value');
    els.dayToggles = document.getElementById('slot-day-toggles');
    els.conflictWarning = document.getElementById('schedule-conflict-warning');
    els.conflictName = document.getElementById('schedule-conflict-name');
    els.contextMenu = document.getElementById('schedule-context-menu');
    els.ctxSkipDay = els.contextMenu?.querySelector('[data-action="skip-day"]');
    els.dayDialog = document.getElementById('schedule-day-dialog');
    els.dayCancel = document.getElementById('schedule-day-cancel');
    els.dayDuplicate = document.getElementById('schedule-day-duplicate');
    els.dayTransferBtn = document.getElementById('schedule-day-transfer');
}

/**
 * @param {{ getPlaylists?: () => Array }} options
 */
export function initSchedule(options = {}) {
    getPlaylists = options.getPlaylists || (() => []);
    cacheElements();
    bindPanelInputs();
    bindChrome();
    updateDayButtonLabels();
    weekMonday = mondayOf(new Date());

    window.addEventListener('dsign:language-changed', () => {
        updateDayButtonLabels();
        applyI18n();
        if (!weekMonday) return;
        if (viewMode === 'month') renderMonth();
        else renderGrid();
    });
}

export async function showScheduleView() {
    isVisible = true;
    try {
        await reloadCurrentView();
        startProgressTimer();
    } catch (err) {
        showError(err.message || 'Failed to load schedule');
    }
}

export function hideScheduleView() {
    isVisible = false;
    stopProgressTimer();
    dismissScheduleOverlays();
}

export function refreshScheduleIfVisible() {
    if (!isVisible) return;
    reloadCurrentView().catch(() => {});
}

export { loadWeek, loadMonth, reloadCurrentView };
