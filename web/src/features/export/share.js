// Image export paths: download, clipboard copy, and the OS share sheet.
// Ask first whether a path is supported (canCopyImage, canShareFiles) and hide the button if not. On failure, fall back to download.

export function downloadBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

// Writing an image to the clipboard needs a secure origin (HTTPS, localhost) and ClipboardItem. Not available in older Firefox (before 127) or over http.
export const canCopyImage = () => typeof window !== "undefined" && !!(window.ClipboardItem && navigator.clipboard && navigator.clipboard.write);

// Safari requires the clipboard write to start at the moment of the click, so we pass a Promise of the Blob rather than the Blob and
// create the ClipboardItem synchronously (Chrome and Safari both accept Promise values). If that is rejected, we await the Blob and write again.
export async function copyImage(blobPromise) {
  if (!canCopyImage()) return "unsupported";
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise })]);
    return "copied";
  } catch (err) {
    try {
      const blob = await blobPromise;
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      return "copied";
    } catch (err2) {
      console.warn("그림 복사 실패", err, err2);
      return "failed";
    }
  }
}

// OS share sheet (the phone's 'Save image', KakaoTalk, etc.). Only browsers that accept files: iOS 15+ Safari, Android Chrome, some desktops.
// type: PDF is queried separately as "application/pdf" (some browsers can share images but not PDFs).
export function canShareFiles(type = "image/png") {
  try {
    if (typeof navigator === "undefined" || !navigator.share || !navigator.canShare || typeof File === "undefined") return false;
    return navigator.canShare({ files: [new File([new Uint8Array(8)], type === "application/pdf" ? "x.pdf" : "x.png", { type })] });
  } catch { return false; }
}

// Sharing can only open right after a user gesture (a tap). The Blob must be ready beforehand so it can be called without waiting.
// Returns: shared, cancelled (user dismissed), blocked (the tap was too old; pressing once more works), or failed
export async function shareBlob(blob, name, title) {
  try {
    await navigator.share({ files: [new File([blob], name, { type: blob.type || "image/png" })], title });
    return "shared";
  } catch (err) {
    if (err && err.name === "AbortError") return "cancelled";
    if (err && err.name === "NotAllowedError") return "blocked";
    console.warn("공유 실패", err);
    return "failed";
  }
}
