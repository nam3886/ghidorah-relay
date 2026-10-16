"""Mutation markers of the shared admin components (tone registry and clipboard): each breaks one rule in the TypeScript
source and demands that its Vitest spec turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
CLIPBOARD = "apps/admin-web/src/components/shared/clipboard.ts"
TONES = "apps/admin-web/src/components/shared/status-tones.ts"
TABLE = "apps/admin-web/src/components/shared/DataTable.vue"
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
@mutation(TABLE, ':max-height="props.maxHeight"', ':max-height="undefined"',
          expect="has no inner scroll unless the caller sets a max height")
@mutation(TABLE, "return value === null || value === undefined || value === \"\";",
          "return value === undefined || value === \"\";", expect="draws an empty value as a quiet dash")
@mutation(TABLE, "actionsWidth: 200,", "actionsWidth: 100,", expect="gives the Actions column room")
@pytest.mark.web_unit
def test_the_table_keeps_old_rows_while_refreshing():
    vitest("tests/unit/shared/data-table.spec.ts")


PAGER = "apps/admin-web/src/components/shared/ListPager.vue"


@mutation(TABLE, 'v-if="props.selectable"\n        type="selection"',
          'v-if="!props.selectable"\n        type="selection"',
          expect="has no checkbox column unless selectable")
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
@mutation(PAGER, '<span v-if="!isPhone" class="pager__text">', '<span v-if="isPhone" class="pager__text">',
          expect="on a phone it is Previous, a count and Next")
@mutation(PAGER, 'v-if="!isPhone"\n        class="pager__size"', 'v-if="isPhone"\n        class="pager__size"',
          expect="on a phone it is Previous, a count and Next")
@mutation(PAGER, "props.sizes.includes(props.limit)", "true",
          expect="a limit that is not in the list is added to the sizes")
@mutation(PAGER, "Math.max(1, Math.ceil(props.total / props.limit))", "Math.ceil(props.total / props.limit)",
          expect="on a phone with no rows the count is 1 / 1")
@mutation(PAGER, "Math.min(props.offset + props.limit, props.total)", "props.offset + props.limit",
          expect="the last page is cut at the total")
@mutation(PAGER, '.toLocaleString("en-US")', ".toString()", expect="a thousand gets a separator")
@mutation(PAGER, 'if (props.total === 0) return "Showing 0 of 0";', "", expect="an empty list says so")
@mutation(PAGER, ':aria-disabled="!hasPrev"', ':aria-disabled="false"',
          expect="Previous is disabled and silent on page 1")
@mutation(PAGER, "if (page < 1 || ", "if (", expect="Previous is disabled and silent on page 1")
@mutation(PAGER, "page > pageCount.value ||", "", expect="Next is disabled and silent on the last page")
@mutation(PAGER, "|| page === currentPage.value", "", expect="the current page emits nothing")
@mutation(PAGER, "slot === pageCount && String(slot)", "String(slot)",
          expect="only the last page is wide")
@mutation(PAGER, "String(slot).length >= 3", "String(slot).length >= 4",
          expect="draws the Jobs state exactly: 1 2 3 … 133")
@mutation(PAGER, "'is-active': slot === currentPage", "'is-active': false",
          expect="shows 'Showing a-b of total' and one numbered page per limit")
@mutation(PAGER, "Number(v)", "v", expect="a size change emits size with the number and never a move")
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
THEME_CSS = "apps/admin-web/src/styles/theme.css"
BRAND_SPEC = "tests/unit/brand.spec.ts"


@mutation(BRAND, 'ui.theme === "dark" ? logoDark : logoLight', 'ui.theme === "dark" ? logoLight : logoDark',
          expect="shows the logo with light letters in the dark theme")
@mutation(THEME_CSS, "--el-border-radius-base: 10px;", "--el-border-radius-base: 6px;",
          expect="the brand radii are 10 px for controls")
@pytest.mark.web_unit
def test_the_brand_logo_follows_the_theme_and_the_tokens_are_the_kits():
    vitest(BRAND_SPEC)


STATUS_CHIP = "apps/admin-web/src/components/shared/StatusChip.vue"


@mutation(STATUS_CHIP, ":class=\"`tone-${resolved}`\"", ":class=\"'tone-ok'\"",
          expect="each tone picks its own class")
@mutation(STATUS_CHIP, "props.tone ?? toneOf(props.status)", "toneOf(props.status)",
          expect="a tone given by the caller wins over the lookup")
@pytest.mark.web_unit
def test_the_status_chip_tone_class_follows_the_tone():
    vitest(LEDGER_SPEC, "tests/unit/shared/small-components.spec.ts")


SHARED = "apps/admin-web/src/components/shared"
LEDGER = f"{SHARED}/LedgerList.vue"
MONEY = f"{SHARED}/MoneyCell.vue"
DRAWER = f"{SHARED}/DetailDrawer.vue"
DAY_ROWS = f"{SRC}/lib/day-rows.ts"
NOTIFY = f"{SRC}/lib/notify.ts"
LEDGER_SPEC = "tests/unit/shared/ledger-money-chip.spec.ts"


@mutation(LEDGER, 'v-if="item.was !== undefined" class="ledger__was"', 'v-if="item.was" class="ledger__was"',
          expect="a was of 0 is still shown")
@mutation(LEDGER, 'v-if="item.note"', 'v-if="!item.note"', expect="a struck old value, an arrow and the value")
@mutation(LEDGER, ':index="i"', ':index="0"', expect="the value slot replaces the value text")
@mutation(LEDGER, '<slot name="value"', '<slot name="values"', expect="the value slot replaces the value text")
@mutation(LEDGER, "columns: 1,", "columns: 2,", expect="one column by default")
@mutation(LEDGER, 'variant: "band",', 'variant: "drawer",', expect="the Jobs band is the default box")
@mutation(LEDGER, "item.tone !== 'neutral'", "true", expect="a tone colours the value; neutral and none do not")
@mutation(LEDGER, "item.mono ? 'is-mono' : ''", "'is-mono'", expect="mono is for the value only")
@mutation(LEDGER, 'class="ledger__leader" aria-hidden="true"', 'class="ledger__leader"',
          expect="is a dl with one label and one value per item")
@pytest.mark.web_unit
def test_the_ledger_list_columns_tones_and_leaders():
    vitest(LEDGER_SPEC)


@mutation(MONEY, "props.net < props.price", "props.net <= props.price",
          expect="no refund when the net equals the price")
@mutation(MONEY, 'typeof props.price === "number" && typeof props.net === "number" &&', "true &&",
          expect="a net that is not known yet is an em dash and never a refund")
@mutation(MONEY, 'String(props.net) : "—"', 'String(props.net) : "-"',
          expect="a net that is not known yet is an em dash")
@mutation(MONEY, ':size="13"', ':size="12"', expect="the refund reads struck charge")
@pytest.mark.web_unit
def test_the_money_cell_strikes_a_refund_and_only_a_refund():
    vitest(LEDGER_SPEC)


@mutation(STATUS_CHIP, 'v-if="props.step"', 'v-if="!props.step"', expect="a step replaces the dot")
@pytest.mark.web_unit
def test_the_status_chip_shows_its_step_and_is_a_pill():
    vitest(LEDGER_SPEC)


@mutation(DRAWER, "destroy-on-close", ':destroy-on-close="false"', expect="destroys its content on close")
@mutation(DRAWER, "'100%' : '620px'", "'620px' : '620px'", expect="a full-screen sheet on a phone")
@mutation(DRAWER, 'direction="rtl"', 'direction="ltr"', expect="slides in from the right")
@mutation(DRAWER, ':title="props.title"', 'title=""', expect="is a dialog named by its title")
@mutation(DRAWER, 'aria-label="Close"', 'aria-label="Shut"', expect="the close button is named Close")
@mutation(DRAWER, 'v-if="$slots.chips"', 'v-if="true"', expect="none of those regions exist")
@mutation(DRAWER, 'v-if="$slots.tabs"', 'v-if="true"', expect="none of those regions exist")
@mutation(DRAWER, 'v-if="$slots.footer"', 'v-if="true"', expect="none of those regions exist")
@mutation(DRAWER, ':data-testid="props.testid"', 'data-testid="x"', expect="the fixed test id")
@mutation(DRAWER, "props.heading ?? props.title", "props.title", expect="heading is the visible h2 text")
@mutation(DRAWER, ':aria-label="props.backLabel"', 'aria-label="Back"', expect="a phone sheet has a Back button")
@mutation(DRAWER, 'aria-label="Theme"', 'aria-label="Mode"', expect="a phone sheet has a Back button")
@mutation(DRAWER, "isPhone ? 'h1' : 'h2'", "isPhone ? 'h2' : 'h1'", expect="a phone sheet has a Back button")
@mutation(DRAWER, ":class=\"{ 'is-sheet': isPhone }\"", ":class=\"{ 'is-sheet': false }\"",
          expect="a phone sheet has a Back button")
@pytest.mark.web_unit
def test_the_detail_drawer_is_a_named_destroyed_sheet():
    vitest("tests/unit/shared/detail-drawer.spec.ts")


@mutation(DAY_ROWS, "if (columnIndex < leading) return [1, 1];", "", expect="column 0 stays one empty cell")
@mutation(DAY_ROWS, "Math.max(1, columnCount - leading)", "columnCount", expect="column 1 spans the rest")
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


@mutation(TABLE, "(row: T | DayRow) => !isDayRow(row)", "(row: T | DayRow) => true",
          expect="every day row has a disabled checkbox")
@mutation(TABLE, "const picked = rows.filter((r): r is T => !isDayRow(r));", "const picked = rows as T[];",
          expect="a day row never leaves the component")
@mutation(TABLE, 'isDayRow(row) ? "is-day"', 'false ? "is-day"', expect="draws one header per day")
@mutation(TABLE, "!props.rowClickable || isDayRow(row)", "!props.rowClickable",
          expect="clicking a day row never opens anything")
@mutation(TABLE, "daySpanMethod(columnCount.value, props.selectable ? 1 : 0)", "daySpanMethod(columnCount.value, 1)",
          expect="without a selection column the header cell is the first one")
@mutation(TABLE, "(props.selectable ? 1 : 0) + visibleColumns.value.length", "0 + visibleColumns.value.length",
          expect="the header cell spans every column but the first")
@mutation(TABLE, "(slots.actions ? 1 : 0)", "0", expect="an Actions column counts")
@mutation(TABLE, "isTablet.value && props.tabletColumns", "false && props.tabletColumns",
          expect="on a tablet shows the priority columns only")
@mutation(TABLE, "props.tabletColumns!.includes(c.key)", "!props.tabletColumns!.includes(c.key)",
          expect="on a tablet shows the priority columns only")
@mutation(TABLE, 'props.toneFill ? " row-tone-fill" : ""', '""', expect="toneFill marks the toned rows")
@mutation(TABLE, 'if (!tone || tone === "neutral") return "";', 'if (!tone) return "";',
          expect="toneFill marks the toned rows")
@mutation(TABLE, 'selectedKeys.value.has(keyOf(row)) ? "is-selected" : ""', '""',
          expect="its row is marked selected")
@mutation(TABLE, "isPhone.value && !!slots.row", "isPhone.value",
          expect="the list and the table are never in the page together")
@mutation(TABLE, "props.rowClickable ? 'button' : undefined", "'button'",
          expect="a row that does not open anything is not a button")
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
@mutation(TABLE, "c.className ?? ''", "''", expect="a column className lands on its header and body cells")
@mutation(TABLE, ':class="props.mainClass"', ':class="undefined"', expect="mainClass lands on the open area")
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
@mutation(NOTIFY, "PHONE_OFFSET_PX = 72", "PHONE_OFFSET_PX = 0", expect="lifted above the bulk bar")
@mutation(NOTIFY, "(max-width: 767.98px)", "(max-width: 10px)", expect="asks the phone edge")
@mutation(NOTIFY, "clear: () => message.closeAll()", "clear: () => void message", expect="clear closes every toast")
@pytest.mark.web_unit
def test_toasts_stack_where_the_design_puts_them():
    vitest("tests/unit/shared/notify.spec.ts")


MENUS_SPEC = "tests/unit/shared/menus-and-chips.spec.ts"
FILTER = f"{SHARED}/FilterSelect.vue"
ACTION_MENU = f"{SHARED}/ActionMenu.vue"
COMMAND_CHIP = f"{SHARED}/CommandChip.vue"
DRAWER_TABS = f"{SHARED}/DrawerTabs.vue"
DRAWER_PANEL = f"{SHARED}/DrawerPanel.vue"
DRAWER_IDS = f"{SHARED}/drawer-tab-ids.ts"
COPY = f"{SHARED}/CopyButton.vue"


@mutation(FILTER, 'v-if="props.chevron"', 'v-if="!props.chevron"',
          expect="the height follows the size and the chevron can be left out")
@mutation(FILTER, 'v-if="props.label"', 'v-if="!props.label"', expect="is a listbox button")
@mutation(FILTER, 'role="listbox"', 'role="menu"', expect="is a listbox button")
@mutation(FILTER, 'current?.label ?? ""', 'current?.label ?? "?"', expect="shows the label of the current value")
@mutation(FILTER, ":class=\"`is-${props.size}`\"", ":class=\"'is-toolbar'\"",
          expect="the height follows the size and the chevron can be left out")
@mutation(ACTION_MENU, ':stroke="2.4"', ':stroke="2"', expect="is a 32 px button named More")
@mutation(COMMAND_CHIP, 'v-if="props.time"', 'v-if="!props.time"', expect="ok: a dot, `ok ·` and the time in mono")
@mutation(COMMAND_CHIP, 'v-if="props.message"', 'v-if="!props.message"', expect="error: a dot and `error · <message>`")
@mutation(COMMAND_CHIP, ":class=\"`is-${props.state}`\"", ":class=\"'is-ok'\"", expect="running: a spinner")
@mutation(DRAWER_TABS, ":aria-controls=\"drawerPanelId(props.idPrefix, t.key)\"",
          ":aria-controls=\"drawerTabId(props.idPrefix, t.key)\"",
          expect="each tab has an id and names the panel it controls")
@mutation(DRAWER_PANEL, ':aria-labelledby="drawerTabId(props.idPrefix, props.name)"', ':aria-labelledby="props.name"',
          expect="is a tabpanel whose id is the one its tab controls")
@mutation(DRAWER_PANEL, 'role="tabpanel"', 'role="region"', expect="is a tabpanel whose id is the one its tab controls")
@mutation(DRAWER_PANEL, 'v-show="props.name === props.selected"', 'v-show="true"',
          expect="only the selected tab's panel is shown")
@mutation(DRAWER_IDS, "`${prefix}-panel-${key}`", "`${prefix}-tab-${key}`", expect="each tab has an id")
@mutation(DRAWER_TABS, "t.key === props.modelValue ? 0 : -1", "t.key === props.modelValue ? 0 : 0",
          expect="the current one selected and the only one in the tab order")
@mutation(DRAWER_TABS, ':aria-selected="t.key === props.modelValue"', ':aria-selected="true"',
          expect="the current one selected and the only one in the tab order")
@mutation(DRAWER_TABS, "(from + step + props.tabs.length) % props.tabs.length", "(from + step) % props.tabs.length",
          expect="wrap round")
@mutation(DRAWER_TABS, "[props.tabs.indexOf(next)]?.focus();", "[props.tabs.indexOf(next)]?.id;",
          expect="the focus moves with the arrow")
@mutation(DRAWER_TABS, "[props.tabs.indexOf(next)]?.focus()", "[0]?.focus()",
          expect="the focus moves with the arrow")
@mutation(DRAWER_TABS, '@keydown.left.prevent="move(i, -1, $event)"', '@keydown.left.prevent="move(i, 1, $event)"',
          expect="the focus moves with the arrow")
@pytest.mark.web_unit
def test_the_select_menu_chip_and_tabs_keep_their_shape():
    vitest(MENUS_SPEC)


SKIN = f"{SHARED}/skin.css"
CONFIRM_CSS = f"{SHARED}/confirm-dialog.css"
NOTIFY_CSS = f"{SRC}/lib/notify.css"
PAGER_CSS_SPEC = "tests/unit/shared/table-pager-style.spec.ts"
SKIN_SPEC = "tests/unit/shared/skin.spec.ts"


@mutation(SKIN, "opacity: 0.45;", "opacity: 0.5;", expect="a disabled primary is the purple at 45 %")
@mutation(SKIN, "--el-button-hover-bg-color: transparent;", "--el-button-hover-bg-color: var(--rl-raised);",
          expect="the danger outline draws no hover")
@mutation(SKIN, "--el-button-text-color: #ffffff;\n  padding: 0 16px;",
          "--el-button-text-color: #ffffff;\n  padding: 0 14px;",
          expect="every button is 13 px / 400")
@mutation(SKIN, "  width: 15px;\n  height: 15px;\n  border-radius: 2px;",
          "  width: 16px;\n  height: 16px;\n  border-radius: 2px;",
          expect="is the native-look box")
@mutation(SKIN, "min-width: 200px;", "min-width: 180px;", expect="the menu is a 12 px radius surface")
@mutation(SKIN, "background: var(--rl-fg);\n  color: var(--rl-surface);",
          "background: var(--rl-surface);\n  color: var(--rl-fg);",
          expect="the tooltip is inverted")
@mutation(CONFIRM_CSS, "width: min(440px, 100%);", "width: min(420px, 100%);", expect="the confirm box is 440 wide")
@mutation(CONFIRM_CSS, "padding: 20px 20px 16px;", "padding: 20px 24px;", expect="the confirm box is 440 wide")
@mutation(NOTIFY_CSS, "  background-color: var(--rl-surface);\n}\n.el-message--success",
          "  background-color: var(--rl-ok-bg);\n}\n.el-message--success",
          expect="a toast is 260 wide")
@mutation(NOTIFY_CSS, "min-width: 260px;", "min-width: 240px;", expect="a toast is 260 wide")
@mutation(NOTIFY_CSS, "padding: 12px 8px 12px 16px;", "padding: 12px 16px;", expect="a toast is 260 wide")
@mutation(SKIN, "  padding: 6px 10px;\n  border: 0;", "  padding: 6px 10px;\n  border: 1px solid var(--rl-line);",
          expect="the tooltip is exactly the inverted pill")
@mutation(SKIN, "  overflow: hidden;\n  border: 1px solid var(--rl-line);\n  border-radius: 16px;",
          "  overflow: hidden;\n  border: 1px solid var(--rl-line);\n  border-radius: 12px;",
          expect="the card is the surface with a hairline and radius 16")
@mutation(SKIN, "--el-button-hover-text-color: var(--rl-bad);\n  --el-button-active-bg-color: transparent;",
          "--el-button-hover-text-color: var(--rl-fg);\n  --el-button-active-bg-color: transparent;",
          expect="no hover change is drawn")
@pytest.mark.web_unit
def test_the_shared_skin_keeps_the_drawn_values():
    vitest(SKIN_SPEC)


@mutation(TABLE, "height: 38px;\n  border-bottom: 0;", "height: 40px;\n  border-bottom: 0;",
          expect="a header cell is 38 high")
@mutation(TABLE, "height: 56px;\n  border-bottom: 1px solid var(--rl-line);",
          "height: 52px;\n  border-bottom: 1px solid var(--rl-line);",
          expect="a header cell is 38 high")
@mutation(TABLE, "--row-tone-color: var(--rl-warn);\n  --row-tone-fill: transparent;",
          "--row-tone-color: var(--rl-warn);\n  --row-tone-fill: var(--rl-warn-bg);",
          expect="only a failed row is tinted")
@mutation(TABLE, "padding: 44px 16px 52px;", "padding: 40px 16px 48px;", expect="the empty state is padded 44 16 52")
@mutation(PAGER, "min-width: 40px;", "min-width: 32px;", expect="a page button is 32 high")
@mutation(PAGER, "border-radius: 10px;\n  background: transparent;\n  color: var(--rl-fg);",
          "border-radius: 8px;\n  background: transparent;\n  color: var(--rl-fg);", expect="a page button is 32 high")
@pytest.mark.web_unit
def test_the_table_and_pager_keep_the_drawn_values():
    vitest(PAGER_CSS_SPEC)


@mutation(COPY, 'props.size === "small" ? "tiny" : "button"', 'props.size === "small" ? "button" : "tiny"',
          expect="the old size prop is an alias")
@mutation(COPY, "props.variant ??", "props.size ??", expect="variant wins")
@pytest.mark.web_unit
def test_the_copy_button_shape_follows_variant_and_the_old_size():
    vitest("tests/unit/shared/small-components.spec.ts")
