// A fetched file leaves the page as a blob URL: opened in a new tab without an opener, or saved as a download. The URL
// is revoked a little later (the tab or the download needs it to start).
const REVOKE_AFTER_MS = 60_000;

function later(url: string): void {
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}

export function openBlob(blob: Blob): void {
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  later(url);
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  later(url);
}
