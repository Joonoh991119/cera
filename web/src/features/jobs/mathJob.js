// Math job (lazy chunk): typesets the LaTeX of a placeholder formula image with MathJax, uploads the file FIRST, checks the lease, saves the changed
// element on the server, and only then shows it here (late-file rule, features/math/hold.js: other screens never see a gray box).
// The change stays out of this person's undo history. If the lease was lost meanwhile (another tab took the job), nothing is changed.
import { CaptureUpdateAction } from "@excalidraw/excalidraw";
import { texToSvg, svgDataURL, sha1 } from "../../math.js";
import { replaceMath } from "../math/replace.js";
import { isHex } from "../math/insert.js";
import { mathPlan, mathCustom, applyChanged } from "./jobs.js";

const bumpCustom = (e, customData) => ({ ...e, customData, version: e.version + 1, versionNonce: Math.floor(Math.random() * 2 ** 31) });
const find = (excal, id) => excal.getSceneElementsIncludingDeleted().find((e) => e.id === id);

async function commit(excal, ctx, changed) {
  await ctx.confirm();          // throws LeaseLost: nothing is written
  await ctx.persist(changed);   // throws if the server did not take it: nothing is shown here either
  excal.updateScene({ elements: applyChanged(excal.getSceneElementsIncludingDeleted(), changed), captureUpdate: CaptureUpdateAction.NEVER });
}

export default async function mathJob(excal, args, ctx) {
  const el = find(excal, args && args.id);
  const plan = mathPlan(el, args || {});
  if (plan === "missing") return { ok: false, error: "formula element not found" };
  if (plan === "changed") return { ok: true, data: { id: el.id, skipped: "changed" } };
  const cd = el.customData || {};
  const size = Number.isFinite(args.size) && args.size > 4 && args.size < 400 ? args.size : Number.isFinite(cd.size) ? cd.size : 32;
  const color = isHex(args.color) ? args.color : isHex(cd.color) ? cd.color : "#1e1e1e";
  const r = await texToSvg(args.latex, { size, color });
  const now = find(excal, el.id);
  if (mathPlan(now, args) !== "run") return { ok: true, data: { id: el.id, skipped: "changed" } };
  if (r.error) {
    await commit(excal, ctx, [bumpCustom(now, mathCustom(now.customData, "error", r.error))]);
    return { ok: false, error: String(r.error).slice(0, 300) };
  }
  const fid = (await sha1(r.svg)).slice(0, 40);
  const file = { id: fid, mimeType: "image/svg+xml", dataURL: svgDataURL(r.svg), created: Date.now() };
  excal.addFiles([file]);
  await ctx.upload(file);   // throws on failure or after UPLOAD_MS: the job fails with the reason
  const cur = find(excal, el.id);
  if (mathPlan(cur, args) !== "run") return { ok: true, data: { id: el.id, skipped: "changed" } };
  const els = replaceMath(excal.getSceneElementsIncludingDeleted(), cur.id, { fileId: fid, latex: args.latex, size, color, w: r.width, h: r.height });
  if (!els) return { ok: false, error: "formula element not found" };
  const before = new Map(excal.getSceneElementsIncludingDeleted().map((e) => [e.id, e.version]));
  const changed = els.filter((e) => before.get(e.id) !== e.version).map((e) => (e.id === cur.id ? { ...e, customData: mathCustom(e.customData, "done") } : e));
  await commit(excal, ctx, changed);
  excal.addFiles([file]);
  return { ok: true, data: { id: cur.id, fileId: fid, width: r.width, height: r.height } };
}
