// The whole dashboard reads /api in the browser and touches localStorage on init —
// there is no server to render against, so run client-side only.
export const ssr = false;
export const prerender = false;
