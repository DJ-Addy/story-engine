// One string, one scene.
//
// The UI addresses a scene with a single opaque reference ("demo", "proj_1/4",
// "proj_1:4") because that is what lets ONE control open both halves of the
// workspace. The API addresses it as (project_id, scene_ordinal). Splitting
// that reference used to live only inside `httpApi.ts`; the mock now needs the
// same rule to serve more than one scene, so it lives here and both use it.
//
// A bare id means scene 1: `normalize.py` numbers scenes 1-based and reserves
// ordinal 0 for the pre-slugline preamble, so scene 1 is always the first real
// scene.

export interface SplitSceneRef {
  /** The project half — still a UI reference, NOT a resolved project id. */
  projectRef: string;
  ordinal: number;
}

export function splitSceneRef(ref: string): SplitSceneRef {
  const match = /^(.*?)[/:](\d+)$/.exec(ref.trim());
  if (match) return { projectRef: match[1], ordinal: Number(match[2]) };
  return { projectRef: ref.trim(), ordinal: 1 };
}

/** The inverse: the reference that opens scene `ordinal` of `projectRef`. */
export const joinSceneRef = (projectRef: string, ordinal: number): string =>
  `${projectRef}/${ordinal}`;
