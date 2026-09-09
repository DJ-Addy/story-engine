// The guided tour: the steps, in order, across the pages they live on.
//
// A step names a route and a target — an element carrying `data-tour="…"` on
// that page. The tour component spotlights the target (everything else is
// dimmed), shows the card beside it, and when the next step lives on another
// page it navigates there and carries on. Every route below opens on the demo
// project when given no parameters, so the tour needs none.
//
// Progress lives in sessionStorage so a navigation does not lose it; whether
// the tour has ever been seen lives in localStorage so a first visit to the
// pipeline starts it once and never again unprompted.

export interface TourStep {
  route: "/pipeline" | "/workspace" | "/casting" | "/dashboard";
  /** Value of the target's `data-tour` attribute. */
  target: string;
  title: string;
  body: string;
  /** Where the card sits relative to the target. */
  placement?: "top" | "bottom" | "left" | "right";
}

export const TOUR_STEPS: TourStep[] = [
  {
    route: "/pipeline",
    target: "pipeline-title",
    title: "One graph, every render",
    body: "A manuscript becomes a story graph — who speaks, where, in what order. Everything you are about to see renders from that one graph. This page walks the work in order; each step reads its real state from the API.",
    placement: "bottom",
  },
  {
    route: "/pipeline",
    target: "step-ingest",
    title: "1 · Manuscript in",
    body: "The sample is Book XII of the Odyssey — Ulysses and the Sirens — converted from Butler's prose by the novel ingest. Every line is attributed to a speaker. Bring your own script or book from New project.",
    placement: "bottom",
  },
  {
    route: "/pipeline",
    target: "step-cast",
    title: "2 · The judge casts it",
    body: "The judge reads the lines and decides a voice AND a delivery tone for every part, with the evidence named. Where the text gives no signal it says so instead of guessing. The renderer obeys this decision.",
    placement: "top",
  },
  {
    route: "/pipeline",
    target: "step-agent",
    title: "3 · The agent network shoots it",
    body: "A Gemini coordinator delegates to a shot designer and a previz critic whose tools are the real pipeline. It writes the shot list and scores it — watch the delegations appear.",
    placement: "top",
  },
  {
    route: "/pipeline",
    target: "step-audio",
    title: "4 · Voices, tone, ambience",
    body: "Every line is synthesized in the cast voice with the cast tone, placed on a speech bus, and mixed over an ambience bed with sound effects cut to the words that describe them.",
    placement: "top",
  },
  {
    route: "/pipeline",
    target: "step-boards",
    title: "5 · Animatic, for cents",
    body: "Gemini draws one storyboard frame per shot. Cut to the audio, that is the animatic. Any shot can then be upgraded to Veo video, which animates from its board.",
    placement: "top",
  },
  {
    route: "/workspace",
    target: "play",
    title: "Press play",
    body: "This is the rendered Odyssey — Ulysses, the Sirens, the narrator — in the voices and tones the judge chose. The picture, the lanes and the sound all follow one clock.",
    placement: "top",
  },
  {
    route: "/workspace",
    target: "monitor-mode",
    title: "Animatic or Video",
    body: "The monitor shows the shot under the playhead. Animatic holds its drawn frame; Video plays its Veo render. Switch any time — the same choice the pipeline made at scene level, here per shot.",
    placement: "bottom",
  },
  {
    route: "/workspace",
    target: "monitor",
    title: "The program monitor",
    body: "With no picture the monitor shows the shot's slate rather than a broken player. Draw a board here for a few cents, or render the shot with Veo.",
    placement: "left",
  },
  {
    route: "/dashboard",
    target: "dashboard",
    title: "Every decision, in ClickHouse",
    body: "Judge scores, render events and cost decisions are written through the official ClickHouse MCP server as they happen and read back here — leaderboards, spend, and the refusals the cost governor made.",
    placement: "bottom",
  },
];

export const TOUR_PROGRESS_KEY = "story-engine.tour.progress";
export const TOUR_SEEN_KEY = "story-engine.tour.seen";
