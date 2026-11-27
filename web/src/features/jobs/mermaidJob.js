// Mermaid job (lazy chunk): mermaid text -> editable shapes and arrows (the same converter as 'Flowchart from text'), tidied like a person's insert
// (straight lines, app body font), stamped customData.cera.by = the agent's name from its token. Files (diagrams that become an image) must upload;
// the lease is checked and the elements are saved on the server before they appear here. Returns the new element ids.
import { CaptureUpdateAction, convertToExcalidrawElements, getCommonBounds, FONT_FAMILY } from "@excalidraw/excalidraw";
import { restyleMermaid } from "../flow/mermaidStyle.js";
import { mermaidOrigin, applyChanged } from "./jobs.js";

export default async function mermaidJob(excal, args, ctx) {
  const source = typeof (args && args.source) === "string" ? args.source.slice(0, 20000) : "";
  if (!source.trim()) return { ok: false, error: "empty mermaid source" };
  const { parseMermaidToExcalidraw } = await import("@excalidraw/mermaid-to-excalidraw");
  let parsed;
  try { parsed = await parseMermaidToExcalidraw(source, { themeVariables: { fontSize: "16px" } }); } catch (e) {
    return { ok: false, error: String((e && e.message) || e).split("\n").slice(0, 3).join(" ").slice(0, 300) };
  }
  if (!ctx.alive()) return { ok: false, error: "lease lost" };
  const made = convertToExcalidrawElements(parsed.elements || [], { regenerateIds: true });
  if (!made.length) return { ok: false, error: "mermaid produced nothing" };
  const scene = excal.getSceneElements();
  const at = mermaidOrigin(args.at, scene.length ? getCommonBounds(scene) : null);
  const [mx, my] = getCommonBounds(made);
  const name = String(ctx.by || "AI").slice(0, 40);
  const stamp = (e) => ({ ...e, customData: { ...(e.customData || {}), cera: { ...((e.customData || {}).cera || {}), by: name } } });
  const moved = made.map((e) => stamp({ ...e, x: e.x + at.x - mx, y: e.y + at.y - my }));
  const files = Object.values(parsed.files || {});
  if (files.length) { excal.addFiles(files); await Promise.all(files.map((f) => ctx.upload(f))); }   // a failed upload fails the job (others would see no picture)
  const ids = new Set(moved.map((e) => e.id));
  const all = [...excal.getSceneElementsIncludingDeleted(), ...moved];
  const next = restyleMermaid(all, ids, FONT_FAMILY.Nunito, convertToExcalidrawElements) || all;
  const fresh = next.filter((e) => ids.has(e.id)).map((e) => (e.customData && e.customData.cera ? e : stamp(e)));
  await ctx.confirm();
  await ctx.persist(fresh);
  excal.updateScene({ elements: applyChanged(excal.getSceneElementsIncludingDeleted(), fresh), captureUpdate: CaptureUpdateAction.NEVER });
  return { ok: true, data: { ids: [...ids], count: ids.size, x: at.x, y: at.y } };
}
