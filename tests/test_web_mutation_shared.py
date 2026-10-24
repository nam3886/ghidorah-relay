"""Mutation markers of the shared admin components (tone registry and clipboard): each breaks one rule in the TypeScript
source and demands that its Vitest spec turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
CLIPBOARD = "apps/admin-web/src/components/shared/clipboard.ts"
TONES = "apps/admin-web/src/components/shared/status-tones.ts"
TABLE = "apps/admin-web/src/components/shared/DataTable.vue"
NEVER_MIXED = "the select-all box is never mixed"
CLIPBOARD_SPEC = "tests/unit/shared/clipboard.spec.ts"
TONES_SPEC = "tests/unit/shared/status-tones.spec.ts"


@mutation(CLIPBOARD, "window.isSecureContext && navigator.clipboard?.writeText", "navigator.clipboard?.writeText",
          expect="on plain http copies through a hidden textarea")
@mutation(CLIPBOARD, "await navigator.clipboard.writeText(text);", "void text;",
          expect="uses the clipboard API on a secure context and nothing else")
@mutation(CLIPBOARD, 'if (execCopy(text)) return "exec"', 'if (false) return "exec"',
          expect="on plain http copies through a hidden textarea")
@mutation(CLIPBOARD, "previous?.focus?.()", "void previous", expect="gives the focus back")
@mutation(CLIPBOARD, "  if (signal?.aborted) return \"aborted\";\n  if (execCopy(text))",
          "  if (execCopy(text))", expect="does not start the fallback")
@mutation(CLIPBOARD, 'area.value = "";', "", expect="scrubs the fallback textarea")
@mutation(CLIPBOARD, "window.getSelection()?.removeAllRanges();\n    area.remove();", "area.remove();",
          expect="scrubs the fallback textarea")
@pytest.mark.web_unit
def test_the_clipboard_paths_keep_their_order_and_their_cleanup():
    vitest(CLIPBOARD_SPEC)


@mutation("apps/admin-web/src/components/shared/CopyButton.vue", "abort.abort();", "", expect="leaves no textarea")
@pytest.mark.web_unit
def test_a_copy_pending_at_unmount_does_nothing():
    vitest("tests/unit/shared/small-components.spec.ts")


@mutation(TABLE, "props.loading && props.rows.length > 0 && !props.error",
          "props.loading && props.rows.length > 5 && !props.error", expect="refreshing keeps the old rows")
@mutation(TABLE, "index >= 0", "index >= -1",
          expect="never renders the user's slots for Element Plus's empty probe row")
@mutation(TABLE, "maxHeight: undefined,", 'maxHeight: "calc(100vh - 16rem)",',
          expect="has no inner scroll unless the caller sets a max height")
@mutation(TABLE, "return value === null || value === undefined || value === \"\";",
          "return value === undefined || value === \"\";", expect="draws an empty value as a quiet dash")
@mutation(TABLE, "actionsWidth: 200,", "actionsWidth: 100,", expect="gives the Actions column room")
@mutation(TABLE, "? visibleColumns.value.filter((c) => props.skeletonColumns!.includes(c.key))",
          "? visibleColumns.value",
          expect="the skeleton draws the columns asked for")
# Request logs: a column names its skeleton bar
@mutation(TABLE, "if (c.skeleton) return c.skeleton;", "", expect="a column may name its skeleton bar")
@pytest.mark.web_unit
def test_the_table_keeps_old_rows_while_refreshing():
    vitest("tests/unit/shared/data-table.spec.ts")


PAGER = "apps/admin-web/src/components/shared/ListPager.vue"


@mutation(TABLE, "table.clearSelection();", "void table;",
          expect="setSelection ticks exactly the given ids and replaces an earlier selection")
@mutation(TABLE, "wanted.has(keyOf(r))", "true",
          expect="setSelection ticks exactly the given ids and replaces an earlier selection")
@pytest.mark.web_unit
def test_the_table_selection_column_and_setselection():
    vitest("tests/unit/shared/data-table-selection.spec.ts")


PAGE_LIST = "apps/admin-web/src/components/shared/page-list.ts"
PAGER_SPEC = "tests/unit/shared/list-pager.spec.ts"


@mutation(PAGER, 'emit("move", (page - 1) * props.limit)', 'emit("move", page * props.limit)',
          expect="clicking a page emits move with that page's offset")
@mutation(PAGER, "Math.floor(props.offset / props.limit) + 1", "Math.floor(props.offset / props.limit)",
          expect="the active page follows the offset")
@mutation(PAGER, 'if (currentPage.value > pageCount.value) emit("move"', 'if (false) emit("move"',
          expect="a list that shrank under the page on screen moves the pager to the last page")
@mutation(PAGER, "limit: 10,", "limit: 50,", expect="default size 10")
@mutation(PAGER, "props.sizes.includes(props.limit)", "true",
          expect="a limit that is not in the list is added to the sizes")
@mutation(PAGER, "Math.max(1, Math.ceil(props.total / props.limit))", "Math.ceil(props.total / props.limit)",
          expect="on a phone with no rows the count is 1 / 1")
@mutation(PAGER, "Math.min(props.offset + props.limit, props.total)", "props.offset + props.limit",
          expect="the last page is cut at the total")
@mutation(PAGER, '.toLocaleString("en-US")', ".toString()", expect="a thousand gets a separator")
@mutation(PAGER, 'if (props.total === 0) return "Showing 0 of 0";', "", expect="an empty list says so")
@mutation(PAGER, "if (page < 1 || ", "if (", expect="Previous is disabled and silent on page 1")
@mutation(PAGER, "page > pageCount.value ||", "", expect="Next is disabled and silent on the last page")
@mutation(PAGER, "|| page === currentPage.value", "", expect="the current page emits nothing")
@pytest.mark.web_unit
def test_the_pager_offsets_sizes_and_phone_layout():
    vitest(PAGER_SPEC)


@mutation(PAGE_LIST, "if (count <= 5)", "if (count <= 4)", expect="five pages or fewer show every number")
@mutation(PAGE_LIST, "if (current <= 3)", "if (current <= 2)", expect="near the start the first three pages show")
@mutation(PAGE_LIST, "if (current >= count - 2)", "if (current > count - 2)",
          expect="near the start the first three pages show, near the end the last three")
@mutation(PAGE_LIST, "current - 1, current, current + 1", "current - 2, current, current + 1",
          expect="in the middle the page and its two neighbours show")
@mutation(PAGE_LIST, '[1, "…", count - 2, count - 1, count]', '[1, count - 2, count - 1, count]',
          expect="near the start the first three pages show, near the end the last three")
@pytest.mark.web_unit
def test_the_page_list_follows_the_drawn_state():
    vitest(PAGER_SPEC)


@mutation("apps/admin-web/src/components/shared/confirm-dialog.ts",
          'if (err === "cancel" || err === "close") return false', 'if (err === "cancel") return false',
          expect="resolves false on close")
@mutation("apps/admin-web/src/components/shared/confirm-dialog.ts", 'customClass: "rl-confirm",',
          'customClass: "",', expect="every box carries the confirm class")
@pytest.mark.web_unit
def test_a_confirm_closed_with_escape_is_a_no():
    vitest("tests/unit/shared/confirm-dialog.spec.ts")


@mutation(f"{SRC}/lib/format.ts", '.replace(", ", `,${NO_BREAK_SPACE}`)', '.replace(", ", ", ")',
          expect="never offers a line break")
@pytest.mark.web_unit
def test_a_time_never_breaks_across_two_lines():
    vitest("tests/unit/format.spec.ts")


@mutation(f"{SRC}/lib/stat-cells.ts", '"Failed pre-job (24 h)"', '"Failed before a job was created (24 h)"',
          expect="keeps every label short for one line")
@pytest.mark.web_unit
def test_a_strip_label_stays_short():
    vitest("tests/unit/stat-cells.spec.ts")


@mutation(TONES, "return code < 400 ?", "return code <= 400 ?", expect="maps an HTTP code by its class")
@mutation(TONES, ".trim().toLowerCase()", ".trim()", expect="ignores case and surrounding spaces")
@mutation(TONES, 'if (status === null || status === undefined) return "neutral";',
          'if (false) return "neutral";', expect="tolerates a missing status")
@mutation(TONES, 'if (status === null || status === undefined) return "-";', 'if (false) return "-";',
          expect="tolerates a missing status")
@mutation(TONES, '?? "neutral"', '?? "ok"', expect="falls back to neutral")
@pytest.mark.web_unit
def test_the_tone_lookup_keeps_its_rules():
    vitest(TONES_SPEC)


BRAND = "apps/admin-web/src/components/layout/BrandLogo.vue"
BRAND_SPEC = "tests/unit/brand.spec.ts"


@mutation(BRAND, 'ui.theme === "dark" ? logoDark : logoLight', 'ui.theme === "dark" ? logoLight : logoDark',
          expect="shows the logo with light letters in the dark theme")
@pytest.mark.web_unit
def test_the_brand_logo_follows_the_theme_and_the_tokens_are_the_kits():
    vitest(BRAND_SPEC)


STATUS_CHIP = "apps/admin-web/src/components/shared/StatusChip.vue"


@mutation(STATUS_CHIP, "props.tone ?? toneOf(props.status)", "toneOf(props.status)",
          expect="a tone given by the caller wins over the lookup")
@pytest.mark.web_unit
def test_the_status_chip_tone_class_follows_the_tone():
    vitest(LEDGER_SPEC, "tests/unit/shared/small-components.spec.ts")


SHARED = "apps/admin-web/src/components/shared"
LEDGER = f"{SHARED}/LedgerList.vue"
MONEY = f"{SHARED}/MoneyCell.vue"
DAY_ROWS = f"{SRC}/lib/day-rows.ts"
NOTIFY = f"{SRC}/lib/notify.ts"
LIFT = "FAILED tests/unit/shared/notify.spec.ts > notify > a toast above a drawer footer > "
LEDGER_SPEC = "tests/unit/shared/ledger-money-chip.spec.ts"


@mutation(LEDGER, "columns: 1,", "columns: 2,", expect="one column by default")
@mutation(LEDGER, 'variant: "band",', 'variant: "drawer",', expect="the Jobs band is the default box")
@pytest.mark.web_unit
def test_the_ledger_list_columns_tones_and_leaders():
    vitest(LEDGER_SPEC)


@mutation(MONEY, "props.net < props.price", "props.net <= props.price",
          expect="no refund when the net equals the price")
@mutation(MONEY, 'typeof props.price === "number" && typeof props.net === "number" &&', "true &&",
          expect="a net that is not known yet is an em dash and never a refund")
@mutation(MONEY, 'String(props.net) : "—"', 'String(props.net) : "-"',
          expect="a net that is not known yet is an em dash")
@pytest.mark.web_unit
def test_the_money_cell_strikes_a_refund_and_only_a_refund():
    vitest(LEDGER_SPEC)


@mutation(DAY_ROWS, "if (columnIndex === leading) return [1, Math.max(1, columnCount)];",
          "if (columnIndex <= leading) return [1, Math.max(1, columnCount)];", expect="the selection cell is not drawn")
@mutation(DAY_ROWS, "Math.max(1, columnCount)]", "Math.max(1, columnCount - leading)]",
          expect="column 1 spans every column")
@mutation(DAY_ROWS, "if (!isDayRow(row)) return undefined;", "", expect="a data row is left alone")
@mutation(DAY_ROWS, '(value as Partial<DayRow>)[DAY_MARK] === true', '(value as Partial<DayRow>).kind === "day"',
          expect="only a header made here is a day row")
@mutation(DAY_ROWS, "if (days === 0) return", "if (days === 99) return", expect="Today and Yesterday carry the date")
@mutation(DAY_ROWS, "if (days === 1) return", "if (days === 99) return", expect="Today and Yesterday carry the date")
@mutation(DAY_ROWS, "d.getFullYear() === new Date(now).getFullYear()", "true", expect="another year adds the year")
@mutation(DAY_ROWS, "if (label !== current) {", "if (label !== current || true) {",
          expect="one header before each run of rows of the same day")
@mutation(DAY_ROWS, '(current ?? "Unknown date")', '"Unknown date"',
          expect="a row with no time joins the group above it")
@mutation(DAY_ROWS, "options.note(count)", "options.note(count + 1)",
          expect="the note is made from the number of data rows")
@mutation(DAY_ROWS, "id: `day:${items.length}:${label}`", "id: `day:${label}`", expect="header keys are unique")
@pytest.mark.web_unit
def test_day_rows_group_by_day_and_span_the_table():
    vitest("tests/unit/shared/day-rows.spec.ts")


OPT_IN_SPEC = "tests/unit/shared/data-table-opt-in.spec.ts"


@mutation(TABLE, 'box.setAttribute("aria-label", props.selectLabel?.(row) ?? "Select row")', "void box",
          expect="names every box as drawn")
@mutation(TABLE, "    childList: true,\n", "", expect="names every box as drawn")
@mutation(TABLE, "if (box.indeterminate) box.indeterminate = false;", "", expect=NEVER_MIXED)
@mutation(TABLE, 'if (el.classList.contains("is-indeterminate")) el.classList.remove("is-indeterminate");', "",
          expect=NEVER_MIXED)
@mutation(TABLE, 'if (el.getAttribute("aria-checked") !== "mixed") continue;', "continue;", expect=NEVER_MIXED)
@mutation(TABLE, '    attributes: true,\n    attributeFilter: ["class", ...MIXED],\n', "", expect=NEVER_MIXED)
@mutation(TABLE, "const picked = rows.filter((r): r is T => !isDayRow(r));", "const picked = rows as T[];",
          expect="a day row never leaves the component")
@mutation(TABLE, 'isDayRow(row) ? "is-day"', 'false ? "is-day"', expect="draws one header per day")
@mutation(TABLE, "!props.rowClickable || isDayRow(row)", "!props.rowClickable",
          expect="clicking a day row never opens anything")
@mutation(TABLE, "daySpanMethod(columnCount.value, props.selectable ? 1 : 0)", "daySpanMethod(columnCount.value, 1)",
          expect="without a selection column the header cell is the first one")
@mutation(TABLE, "(props.selectable ? 1 : 0) + visibleColumns.value.length", "0 + visibleColumns.value.length",
          expect="the header is one cell across every column")
@mutation(TABLE, "(slots.actions ? 1 : 0)", "0", expect="an Actions column counts")
@mutation(TABLE, "isTablet.value && props.tabletColumns", "false && props.tabletColumns",
          expect="on a tablet shows the priority columns only")
@mutation(TABLE, "props.tabletColumns!.includes(c.key)", "!props.tabletColumns!.includes(c.key)",
          expect="on a tablet shows the priority columns only")
@mutation(TABLE, 'selectedKeys.value.has(keyOf(row)) ? "is-selected" : ""', '""',
          expect="its row is marked selected")
@mutation(TABLE, "isPhone.value && !!slots.row", "isPhone.value",
          expect="the list and the table are never in the page together")
@mutation(TABLE, "if (!props.rowClickable) return;", "", expect="a row that does not open anything is not a button")
@mutation(TABLE, "if ((event.target as HTMLElement | null)?.closest(INTERACTIVE)) return;", "",
          expect="not by a button inside it")
@mutation(TABLE, "if (event.target !== event.currentTarget) return;", "",
          expect="Enter on a button inside a row does not open the row")
@mutation(TABLE, "keyOf(r) === target ? on : selectedKeys.value.has(keyOf(r))",
          "keyOf(r) === target ? true : selectedKeys.value.has(keyOf(r))",
          expect="ticking emits jobs only")
@mutation(TABLE, "if (present.length !== selectedKeys.value.size) publish(present);", "",
          expect="a reload drops rows that left the list")
@mutation(TABLE, "props.keepSelection || selectedKeys.value.size === 0", "selectedKeys.value.size === 0",
          expect="with keepSelection a reload reports nothing")
@mutation(TABLE, "if (selectedKeys.value.size) publish([]);", "", expect="starts a clean selection")
@pytest.mark.web_unit
def test_the_table_opt_ins_day_rows_tablet_and_phone_list():
    vitest(OPT_IN_SPEC)


@mutation(NOTIFY, "ERROR_DURATION_MS = 5000", "ERROR_DURATION_MS = 6000", expect="an error stays 5 s")
@mutation(NOTIFY, "DURATION_MS = 2500", "DURATION_MS = 2000", expect="closes by itself after 2.5 s")
@mutation(NOTIFY, "showClose: true", "showClose: false", expect="has a close button")
@mutation(NOTIFY, 'placement: "bottom-right", grouping: true', 'placement: "top-right", grouping: true',
          expect="toasts stack at the bottom right")
@mutation(NOTIFY, "offset: PHONE_OFFSET_PX, grouping: true", "offset: PHONE_OFFSET_PX, grouping: false",
          expect="on a phone they sit bottom centre")
@mutation(NOTIFY, 'placement: "bottom", offset', 'placement: "top", offset', expect="on a phone they sit bottom centre")
@mutation(NOTIFY, "Math.max(0, height + TOAST_GAP_PX - PHONE_OFFSET_PX) : height",
          "Math.max(0, height + TOAST_GAP_PX - PHONE_OFFSET_PX) : 0",
          expect=LIFT + "lifts by the footer's height when a drawer opens, and is back at 0 when it closes")
@mutation(NOTIFY, "if (!foot) return 0;", "if (false) return 0;",
          expect=LIFT + "keeps 0 when no footer is drawn, or one lies outside the window")
@mutation(NOTIFY, "height + TOAST_GAP_PX - PHONE_OFFSET_PX", "height - PHONE_OFFSET_PX",
          expect=LIFT + "on a phone only what the footer needs beyond the 72 px its toasts already sit at")
@mutation(NOTIFY, "Math.max(0, height", "Math.max(-99, height",
          expect=LIFT + "on a phone only what the footer needs beyond the 72 px its toasts already sit at")
@mutation(NOTIFY, "    syncToastLift();\n    const handle = message({", "    const handle = message({",
          expect=LIFT + "lifts a toast of every kind the same way and moves none of them: "
          + "only the page-wide lift changes")
@mutation(NOTIFY, "if (/-leave-(active|to)/.test(e.className)) return false;", "if (false) return false;",
          expect=LIFT + "ignores the footer of a drawer that is leaving")
@mutation(NOTIFY, "r.top < window.innerHeight &&", "r.top < Infinity &&",
          expect=LIFT + "keeps 0 when no footer is drawn, or one lies outside the window")
@mutation(NOTIFY, "if (foot.getClientRects().length === 0) return false;", "",
          expect=LIFT + "skips a hidden first footer and measures the visible second one")
@mutation(NOTIFY, 'style.display === "none" || style.visibility === "hidden"', "false",
          expect=LIFT + "skips a hidden first footer and measures the visible second one")
@mutation(NOTIFY, "PHONE_OFFSET_PX = 72", "PHONE_OFFSET_PX = 0", expect="lifted above the bulk bar")
@mutation(NOTIFY, "(max-width: 767.98px)", "(max-width: 10px)", expect="asks the phone edge")
@mutation(NOTIFY, "  message.closeAll();\n    },", "  void message;\n    },", expect="clear closes every toast")
@pytest.mark.web_unit
def test_toasts_stack_where_the_design_puts_them():
    vitest("tests/unit/shared/notify.spec.ts")


MENUS_SPEC = "tests/unit/shared/menus-and-chips.spec.ts"
FILTER = f"{SHARED}/FilterSelect.vue"
FOLLOWS = "the list sits 6 px under its trigger, follows it when the page moves"
ACTION_MENU = f"{SHARED}/ActionMenu.vue"
COMMAND_CHIP = f"{SHARED}/CommandChip.vue"
DRAWER_TABS = f"{SHARED}/DrawerTabs.vue"
DRAWER_IDS = f"{SHARED}/drawer-tab-ids.ts"
COPY = f"{SHARED}/CopyButton.vue"


@mutation(FILTER, "if (open.value) frame = requestAnimationFrame(follow);", "", expect=FOLLOWS)
@mutation(FILTER, "  measure();\n  frame = requestAnimationFrame(follow);", "  measure();", expect=FOLLOWS)
@mutation(FILTER, "let top = rect.bottom + GAP;", "let top = rect.bottom;", expect=FOLLOWS)
@mutation(FILTER, "top = rect.top - GAP - height;", "top = rect.top - height + GAP;", expect=FOLLOWS)
@mutation(FILTER, "ArrowDown: active.value + 1,", "ArrowDown: active.value,", expect="the arrows move")
@mutation(FILTER, "if (option && option.value !== props.modelValue) emit", "if (option) emit",
          expect="choosing the value it already has emits nothing")
@mutation(FILTER, "  close();\n  trigger.value?.focus();", "  close();", expect="keeps the focus on the trigger")
@mutation(FILTER, 'event.key === "Tab") {\n    close();', 'event.key === "Tab") {\n    void 0;',
          expect="a click elsewhere or Tab closes it")
@mutation(FILTER, "const repeat = [...typed].every((c) => c === typed[0]);", "const repeat = typed.length === 1;",
          expect="type-to-select")
@mutation(ACTION_MENU, 'triggerLabel: "More"', 'triggerLabel: "Menu"', expect="is a button named More")
@mutation(ACTION_MENU, 'menuLabel: "Actions"', 'menuLabel: "Menu"', expect="opens a menu named Actions")
@mutation(COMMAND_CHIP, '(props.message ? `error · ${props.message}` : "error")', '("error")',
          expect="error: a dot and `error · <message>`")
@mutation(DRAWER_IDS, "`${prefix}-panel-${key}`", "`${prefix}-tab-${key}`", expect="each tab has an id")
@mutation(DRAWER_TABS, "(from + step + props.tabs.length) % props.tabs.length", "(from + step) % props.tabs.length",
          expect="wrap round")
@mutation(DRAWER_TABS, "[props.tabs.indexOf(next)]?.focus();", "[props.tabs.indexOf(next)]?.id;",
          expect="the focus moves with the arrow")
@mutation(DRAWER_TABS, "[props.tabs.indexOf(next)]?.focus()", "[0]?.focus()",
          expect="the focus moves with the arrow")
@pytest.mark.web_unit
def test_the_select_menu_chip_and_tabs_keep_their_shape():
    vitest(MENUS_SPEC)


# ---- page-alerts: FilterSelect's field variant (the canvas's labelled select) and the ban on native selects ----------
FIELD_SPEC = "tests/unit/shared/filter-select-field.spec.ts"
FIELD = f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > "


@mutation(FILTER, "valueWidth.value = Math.floor(width);", "valueWidth.value = width;",
          expect=FIELD + "its value box is as wide as the longest option on a whole pixel")
# Request logs: Chrome takes the floor of a select's width, not the nearest pixel
@mutation(FILTER, "valueWidth.value = Math.floor(width);", "valueWidth.value = Math.round(width);",
          expect=FIELD + "takes the floor of the measured width, as Chrome sizes a select")
@mutation(FILTER, '"is-tall": isTablet.value', '"is-tall": false', expect=FIELD + "is 36 high on a tablet")
@mutation(FILTER, '"is-chip": chip.value', '"is-chip": false', expect=FIELD + "is 36 high on a tablet")
@mutation(FILTER, "disabled: props.disabled || undefined,", "disabled: undefined,",
          expect=FIELD + "disabled: the box is off and opens nothing")
@mutation(FILTER, " || box.value?.contains(target)))", "))",
          expect=FIELD + "a press on its own label or box while open does not close it from outside")
@mutation(FILTER, "(chip.value ? box.value : trigger.value)", "(trigger.value)",
          expect=FIELD + "on a phone the list hangs 6 px under the chip")
@pytest.mark.web_unit
def test_the_field_select_is_the_canvas_select_drawn_by_us():
    vitest(FIELD_SPEC)


SKIN_SPEC = "tests/unit/shared/skin.spec.ts"


@mutation(COPY, 'props.size === "small" ? "tiny" : "button"', 'props.size === "small" ? "button" : "tiny"',
          expect="the old size prop is an alias")
@mutation(COPY, "props.variant ??", "props.size ??", expect="variant wins")
@pytest.mark.web_unit
def test_the_copy_button_shape_follows_variant_and_the_old_size():
    vitest("tests/unit/shared/small-components.spec.ts")


CONFIRM_TS = f"{SHARED}/confirm-dialog.ts"


@mutation(CONFIRM_TS, "showClose: false,", "showClose: true,", expect="draws no close X")
# page-drawer: the confirm's 440 cap and its buttons (B-JobFailed, B-States), the picture viewer's skin
@pytest.mark.web_unit
def test_the_confirm_box_sits_where_the_canvas_puts_it_and_the_ledger_line_is_20_px():
    vitest("tests/unit/shared/confirm-dialog.spec.ts", SKIN_SPEC)


HINT = f"{SHARED}/HintTip.vue"


@mutation(HINT, "if (shown.value) e.stopPropagation();", "", expect="Escape of an open note goes no further")
@pytest.mark.web_unit
def test_the_hint_tip_is_a_focusable_note_on_the_menu_surface():
    vitest("tests/unit/shared/hint-tip.spec.ts", SKIN_SPEC)


# ---- page-frames-settings round: the toast drawn as B-States (its body, the ×N pill, the X, over Element Plus) ------
NOTIFY_SPEC = "tests/unit/shared/notify.spec.ts"
REPEAT = "a repeat while the toast is open is the same body"
DRAWN_BODY = "draws the body as B-States"
AFTER_CLEAR = "the same words right after clear are a new toast"
AFTER_X = "the same error right after a click on its X is a new toast"


@mutation(NOTIFY, "entry.count.value += 1;", "", expect=REPEAT)
@mutation(NOTIFY, "props.count > 1 ?", "props.count > 9 ?", expect=REPEAT)
@mutation(NOTIFY, "if (shown.get(key) === own) shown.delete(key);", "", expect=REPEAT)
@mutation(NOTIFY, "if (shown.get(key) === own) shown.delete(key);", "shown.delete(key);", expect=AFTER_CLEAR)
@mutation(NOTIFY, "      shown.clear();\n      message.closeAll();", "      message.closeAll();", expect=AFTER_CLEAR)
@mutation(NOTIFY, "            forget(made);\n            made.handle?.close?.();",
          "            made.handle?.close?.();", expect=AFTER_X)
@mutation(NOTIFY, '"aria-label": "Close message"', '"aria-label": "Close"', expect=DRAWN_BODY)
@mutation(NOTIFY, "h(IconClose, { size: 14, stroke: 2 })", "h(IconClose, { size: 16, stroke: 2 })", expect=DRAWN_BODY)
@pytest.mark.web_unit
def test_the_toast_body_is_drawn_as_b_states():
    vitest(NOTIFY_SPEC)


# ---- page-test-login: FilterSelect's field as a form's field (the Test form's selects) -------------------------------
FORM_FIELD = FIELD + "as a form's field (block) keeps its label above the box on a phone too, never the chip"


@mutation(FILTER, "!props.block && isPhone.value", "isPhone.value", expect=FORM_FIELD)
@mutation(FILTER, '"is-block": props.block,', '"is-block": false,', expect=FORM_FIELD)
@pytest.mark.web_unit
def test_the_field_select_can_be_a_form_field():
    vitest(FIELD_SPEC)


# ---- page-keys: the field inside a dialog (the key form's Mode): over the dialog, its own Escape ------------------
@mutation(FILTER, "  listZ.value = nextZIndex();\n", "", expect="the list sits above an open drawer")
@mutation(FILTER, "    event.stopPropagation();\n    close();", "    close();", expect="Escape closes only the list")
@pytest.mark.web_unit
def test_the_field_opens_over_a_dialog_and_keeps_its_escape():
    vitest(FIELD_SPEC)


# ---- the Ledger kit (D0): the arrow scroller, the icon button and its tooltip, the cap chip -------------------------
KIT_SPEC = "tests/unit/shared/ledger-kit.spec.ts"
SCROLLER = f"{SHARED}/ArrowScroller.vue"
ICON_BUTTON = f"{SHARED}/IconButton.vue"
CAP = f"{SHARED}/CapChip.vue"
THRESHOLDS = "shows an arrow once 8 px remain, hides it below 2 px, and keeps its state in between"


@mutation(SCROLLER, "  if (!seen.far.end) right.value = true;\n  else if (seen.near.end) right.value = false;",
          "  right.value = !seen.far.end;", expect=THRESHOLDS)
@mutation(SCROLLER, "else if (seen.near.start) left.value = false;", "else left.value = false;", expect=THRESHOLDS)
@mutation(SCROLLER, '["far", "0px 7px 0px 7px"],', '["far", "0px 1px 0px 1px"],',
          expect="watches two 1 px sentinels in the row")
@mutation(SCROLLER, "Math.round(el.clientWidth * 0.8)", "el.clientWidth",
          expect="scrolls 80% of the visible width per arrow click")
@mutation(SCROLLER, "props.count !== undefined && props.count <= 5", "props.count !== undefined && props.count <= 6",
          expect="wraps a phone set of 5 or fewer")
@mutation(SCROLLER, "watch(() => props.activeKey, revealActive);", "",
          expect="scrolls the active item into view on mount and when it changes")
@mutation(SCROLLER, "  for (const io of observers) io.disconnect();\n", "", expect="stops watching when it is gone")
@pytest.mark.web_unit
def test_the_arrow_scroller_keeps_its_thresholds_and_steps():
    vitest(KIT_SPEC)


@mutation(ICON_BUTTON, "export const SHOW_DELAY_MS = 400;", "export const SHOW_DELAY_MS = 0;",
          expect="shows the tooltip 400 ms after the pointer enters")
@mutation(ICON_BUTTON, "function hide(): void {\n  clearTimeout(timer);\n  open.value = false;",
          "function hide(): void {\n  open.value = false;", expect="shows the tooltip 400 ms after the pointer enters")
@mutation(ICON_BUTTON, "  if (pressed) {\n    pressed = false;\n    event.preventDefault();\n    return;\n  }\n", "",
          expect="a long press shows the tooltip and swallows the click")
@mutation(ICON_BUTTON, '`${props.label} · ${props.shortcut}`', '`${props.label} (${props.shortcut})`',
          expect="is named by its tooltip, word for word")
@pytest.mark.web_unit
def test_the_icon_button_tooltip_keeps_its_timing_and_name():
    vitest(KIT_SPEC)


@mutation(CAP, 'share.value >= 0.8 ? "near"', 'share.value > 0.8 ? "near"',
          expect="puts a static amber dot at 80% and a pulsing red one at 100%")
@mutation(CAP, 'share.value >= 1 ? "full"', 'share.value > 1 ? "full"',
          expect="puts a static amber dot at 80% and a pulsing red one at 100%")
@mutation(CAP, "Math.min(100, Math.round(share.value * 100))", "Math.round(share.value * 100)",
          expect="puts a static amber dot at 80% and a pulsing red one at 100%")
@pytest.mark.web_unit
def test_the_cap_chip_levels():
    vitest(KIT_SPEC)
