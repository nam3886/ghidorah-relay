import { defineIcon } from "./define-icon";
import { ICON_SHAPES } from "./icon-shapes";

// Named exports, one per icon in specs/shared.md section 1: `import { IconMoon } from "@/components/shared/icons"`,
// then `<IconMoon :size="16" />` (stroke 1.8 by default, `:stroke="2"` where the canvas draws it so).
export { ICON_SHAPES } from "./icon-shapes";
export type { IconName } from "./icon-shapes";

export const IconClose = defineIcon("IconClose", ICON_SHAPES.close);
export const IconChevronDown = defineIcon("IconChevronDown", ICON_SHAPES.chevronDown);
export const IconChevronRight = defineIcon("IconChevronRight", ICON_SHAPES.chevronRight);
export const IconMoon = defineIcon("IconMoon", ICON_SHAPES.moon);
export const IconCheck = defineIcon("IconCheck", ICON_SHAPES.check);
export const IconRefresh = defineIcon("IconRefresh", ICON_SHAPES.refresh);
export const IconPlus = defineIcon("IconPlus", ICON_SHAPES.plus);
export const IconMoreVertical = defineIcon("IconMoreVertical", ICON_SHAPES.moreVertical);
export const IconMoreHorizontal = defineIcon("IconMoreHorizontal", ICON_SHAPES.moreHorizontal);
export const IconMoreCircles = defineIcon("IconMoreCircles", ICON_SHAPES.moreCircles);
export const IconCopy = defineIcon("IconCopy", ICON_SHAPES.copy);
export const IconImage = defineIcon("IconImage", ICON_SHAPES.image);
export const IconDownload = defineIcon("IconDownload", ICON_SHAPES.download);
export const IconAlertCircle = defineIcon("IconAlertCircle", ICON_SHAPES.alertCircle);
export const IconAlertCircleShort = defineIcon("IconAlertCircleShort", ICON_SHAPES.alertCircleShort);
export const IconAlertTriangle = defineIcon("IconAlertTriangle", ICON_SHAPES.alertTriangle);
export const IconUndo = defineIcon("IconUndo", ICON_SHAPES.undo);
export const IconCheckCircle = defineIcon("IconCheckCircle", ICON_SHAPES.checkCircle);
export const IconPencil = defineIcon("IconPencil", ICON_SHAPES.pencil);
export const IconPencilMenu = defineIcon("IconPencilMenu", ICON_SHAPES.pencilMenu);
export const IconTrash = defineIcon("IconTrash", ICON_SHAPES.trash);
export const IconTrashClear = defineIcon("IconTrashClear", ICON_SHAPES.trashClear);
export const IconTrashMenu = defineIcon("IconTrashMenu", ICON_SHAPES.trashMenu);
export const IconEye = defineIcon("IconEye", ICON_SHAPES.eye);
export const IconSearch = defineIcon("IconSearch", ICON_SHAPES.search);
export const IconCamera = defineIcon("IconCamera", ICON_SHAPES.camera);
export const IconPower = defineIcon("IconPower", ICON_SHAPES.power);
export const IconPlay = defineIcon("IconPlay", ICON_SHAPES.play);
export const IconPlayFlat = defineIcon("IconPlayFlat", ICON_SHAPES.playFlat);
export const IconUpload = defineIcon("IconUpload", ICON_SHAPES.upload);
export const IconFileText = defineIcon("IconFileText", ICON_SHAPES.fileText);
export const IconLogOut = defineIcon("IconLogOut", ICON_SHAPES.logOut);
export const IconArrowLeft = defineIcon("IconArrowLeft", ICON_SHAPES.arrowLeft);
