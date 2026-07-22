export const CLAUDE_DESIGN_ARTIFACT_CONTRACT = `
## Claude Design Artifact Contract

Build this like a Claude Design canvas artifact, not a static comp.

Required behavior:
- The artifact must be immediately usable in the right-side live preview.
- Prefer real HTML/CSS/JS components over images. Images may support the design, but must never be the whole output.
- ALL TEXT IS REAL HTML/CSS — every headline, subtitle, price, badge, label, button, and caption is live text you write in the markup, never letters baked into an image. (AI-generated images render garbled, misspelled text — so text on top of imagery goes in a CSS layer over the image, never inside it.)
- NEVER INVENT FACTS — do not fabricate prices, statistics, follower/member counts, percentages, ratings, awards, or testimonials. Use only real data given in the brief/brand context. If a real figure is unknown, omit it or use qualitative copy (e.g. "creadores de verdad", never "2.500 creadores" or "$300.000 COP" unless that exact figure was provided).
- App/phone mockups show a REAL screen built in HTML/CSS (nav, cards, lists, content) — never a blank, solid-color, or placeholder screen, and never an AI image of a phone with an empty screen.
- If the brief asks for a prototype, include real interactions: buttons respond, tabs switch, controls update state, forms show useful feedback.
- If the brief asks for a chatbot or AI-powered prototype, call \`window.fydesign.ai(prompt)\` from client-side JavaScript and render loading, success, and error states.
- If the brief asks for voice, use \`window.fydesign.listen()\` and \`window.fydesign.speak(text)\` with graceful fallback copy when the browser does not support speech APIs.
- If the brief asks for video, 3D, shaders, charts, or animation, implement the real browser-native version with HTML video/CSS 3D/canvas/WebGL/SVG. Do not fake it with a poster image.
- The first rendered frame must already be polished for export to PNG/PDF/PPTX.
- Add compact comments only around complex JS or canvas/WebGL sections.

Interaction quality:
- Every control needs visible affordance, hover/focus state, and a deterministic state change.
- Preserve state in localStorage only when it improves the prototype.
- Do not depend on external scripts or CDNs for functionality. Inline JS is allowed for the artifact.

Visual quality:
- Use the provided design system as source of truth.
- Keep one dominant focal point, one memorable visual move, and exact spacing rhythm.
- Text must never overlap controls, mockups, or export edges.
- Mobile/desktop responsiveness matters for web prototypes; fixed-format ad/mockup canvases must stay pixel exact.
`;

export const CLAUDE_DESIGN_REFINE_CONTRACT = `
## Refinement Contract

You are editing an existing Claude Design-style artifact.
- Apply the requested change and preserve the rest.
- Use inline comments, selected element selectors, and session context as binding context.
- Keep the artifact functional and complete.
- Preserve design-system tokens, brand colors, typography, spacing, and canvas size.
- If the user asks for a new tweak/control, add the smallest working control and wire it to the preview behavior.
- Return only complete HTML.
`;
