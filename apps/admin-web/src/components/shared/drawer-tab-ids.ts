// The ids that tie a drawer tab to its panel: the tab names the panel it controls (`aria-controls`) and the panel is
// labelled by its tab (`aria-labelledby`). DrawerTabs and DrawerPanel both build them from one prefix, so a view
// gives the same `idPrefix` to both and the links hold.
export const drawerTabId = (prefix: string, key: string): string => `${prefix}-tab-${key}`;
export const drawerPanelId = (prefix: string, key: string): string => `${prefix}-panel-${key}`;
