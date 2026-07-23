/**
 * FyDesign Taste Library — catalog.ts
 * Pure data + helpers; zero external dependencies.
 * Ported from docs/higgsfield-blueprint.md (Higgsfield June 2026 feature set).
 *
 * Exports (contract):
 *   IMAGE_STYLES, CAMERA_MOTIONS, CAMERA_BODIES, APERTURES, GENRES,
 *   COLOR_GRADES, SPEED_RAMPS, TREND_PACKS, VIRAL_PRESETS,
 *   AD_FORMATS, HOOK_TYPES
 *
 *   find<X>(catalog, key) case-insensitive
 *   stylesMenu() / motionsMenu() / cinemaMenu() / trendsMenu() / adFormatsMenu()
 *   applyStyle(prompt, key) / applyMotion(prompt, key)
 *
 * Legacy backward-compat:
 *   default export applyMotion, motionCatalogKeys()
 */

// ─── IMAGE STYLES ─────────────────────────────────────────────────────────────

export interface ImageStyle {
  key: string;
  label: string;
  /** English style clause appended to an image prompt */
  prompt: string;
}

export const IMAGE_STYLES: ImageStyle[] = [
  // Soul 2.0 named presets
  { key: "mystique-city",          label: "Mystique City",          prompt: "mysterious urban night atmosphere, deep shadows, cinematic color grading, moody city streets" },
  { key: "warm-ambient",           label: "Warm Ambient",           prompt: "soft warm golden ambient lighting, intimate natural glow, cozy atmosphere, warm tones" },
  { key: "editorial-street-style", label: "Editorial Street Style", prompt: "high-fashion street editorial photography, bold styling, urban backdrop, magazine-quality composition" },
  { key: "subtle-flash",           label: "Subtle Flash",           prompt: "subtle direct flash photography, balanced ambient fill, fashion editorial feel, slightly washed highlights" },
  { key: "old-smartphone",         label: "Old Smartphone",         prompt: "low-resolution smartphone aesthetic, 2010s digital camera grain, lens distortion, compressed JPEG look" },
  { key: "frutiger-aero",          label: "Frutiger Aero",          prompt: "early 2000s Frutiger Aero design aesthetic, glossy surfaces, blue skies, nature-and-tech fusion, optimistic clean visual language" },
  { key: "swag-era",               label: "Swag Era",               prompt: "2010s swag era aesthetic, bold fashion, high-contrast streetwear photography, hip-hop influenced styling" },
  { key: "y2k-outside",            label: "Y2K Outside",            prompt: "Y2K outdoor photography, early 2000s fashion aesthetic, overexposed digital, low-saturation pastels, glossy surfaces" },
  { key: "nature-light",           label: "Nature Light",           prompt: "pure natural lighting, golden hour or diffused daylight, organic textures, minimal artificial augmentation" },
  { key: "y2k-studio",             label: "Y2K Studio",             prompt: "Y2K studio photography, metallic backdrops, early 2000s fashion editorial, high-key lighting, futuristic millennium aesthetic" },
  { key: "theatrical-light",       label: "Theatrical Light",       prompt: "dramatic theatrical stage lighting, strong motivated key light, deep shadows, performative composition" },
  { key: "siren",                  label: "Siren",                  prompt: "alluring ocean siren aesthetic, deep blues and aqua tones, ethereal beauty, mythological femme power" },
  { key: "flash-editorial",        label: "Flash Editorial",        prompt: "direct on-camera flash editorial photography, high-fashion magazine look, bold shadows, paparazzi-meets-Vogue energy" },
  { key: "candy-pop",              label: "Candy Pop",              prompt: "vibrant pastel candy-pop aesthetic, playful oversaturated colors, bubblegum tones, kawaii-adjacent commercial sweetness" },

  // Classic film era and camera looks
  { key: "general",    label: "General",    prompt: "versatile natural photographic quality, clean neutral aesthetic, professionally lit" },
  { key: "realistic",  label: "Realistic",  prompt: "photorealistic documentary quality, true-to-life color science, no stylization" },
  { key: "iphone",     label: "iPhone",     prompt: "modern iPhone photography aesthetic, computational imaging portrait mode bokeh, true-tone color science" },
  { key: "digital-cam",label: "DigitalCam", prompt: "early 2000s digital camera aesthetic, high saturation, slight noise, compact camera lens distortion" },
  { key: "2000s-cam",  label: "2000s Cam",  prompt: "early 2000s point-and-shoot digital camera look, slightly blown highlights, oversaturated greens, authentic consumer camera color" },
  { key: "360-cam",    label: "360 Cam",    prompt: "360-degree action camera aesthetic, ultra-wide equirectangular look, GoPro energy, immersive panoramic perspective" },
  { key: "vintage-photobooth", label: "Vintage PhotoBooth", prompt: "vintage photo booth strip aesthetic, high-contrast flash, black-and-white or faded color, intimate lo-fi portrait" },
  { key: "cctv",       label: "CCTV",       prompt: "security CCTV camera aesthetic, timestamp overlay, high-angle surveillance perspective, grainy low-resolution infrared-adjacent" },
  { key: "y2k",        label: "Y2K",        prompt: "authentic Y2K aesthetic, early 2000s digital camera quality, silver metallic fashion, butterfly clips, low-fi futurism" },
  { key: "y2k-posters",label: "Y2K Posters",prompt: "Y2K graphic poster style, glossy chrome type, iridescent gradients, millennium futurism graphic design" },
  { key: "90s-editorial", label: "90's Editorial", prompt: "1990s fashion editorial photography, film grain, muted tones, grunge-adjacent styling, Helmut Newton influence" },
  { key: "90s-grain",  label: "90s Grain",  prompt: "heavy 35mm film grain, 1990s color science, slight color shift, analog photography warmth" },
  { key: "2000s-fashion", label: "2000s Fashion", prompt: "early 2000s fashion photography, digital sharpness, glossy highlights, bold color saturation, Y2K-to-aughts transition look" },
  { key: "2049",       label: "2049",       prompt: "Blade Runner 2049 cinematic aesthetic, desolate landscapes, amber and teal palette, epic cinematography, Denis Villeneuve visual language" },

  // Subculture aesthetics
  { key: "bimbocore",   label: "Bimbocore",      prompt: "bimbocore aesthetic, hot pink and bubblegum tones, ultra-glam, Paris Hilton 2000s influence, rhinestones and excess" },
  { key: "coquette",    label: "Coquette Core",   prompt: "coquette aesthetic, soft pinks and lace, ballet ribbons, feminine romanticism, dark academia crossover, innocent yet knowing mood" },
  { key: "fairycore",   label: "Fairycore",       prompt: "ethereal fairycore aesthetic, soft natural light, flowers and mushrooms, gauzy fabrics, pastel forest magic, whimsical otherworldly beauty" },
  { key: "gorpcore",    label: "Gorpcore",        prompt: "gorpcore outdoor fashion aesthetic, technical fabrics, utilitarian layering, muddy trail colors, high-end outdoorsy editorial" },
  { key: "grunge",      label: "Grunge",          prompt: "1990s grunge aesthetic, desaturated film look, torn clothing, raw gritty texture, Seattle underground energy" },
  { key: "indie-sleaze",label: "Indie Sleaze",    prompt: "indie sleaze aesthetic, 2008-2012 alt photography, American Apparel grain, flash photography, cigarettes and dive bars energy" },
  { key: "quiet-luxury",label: "Quiet Luxury",    prompt: "quiet luxury aesthetic, old-money understated fashion, neutral palette, cashmere and linen, minimalist sophistication" },
  { key: "avant-garde", label: "Avant-Garde",     prompt: "avant-garde fashion editorial, experimental composition, conceptual styling, provocative visual ideas, high-art photography" },

  // Makeup and beauty
  { key: "babydoll-makeup",   label: "Babydoll MakeUp",       prompt: "babydoll makeup aesthetic, exaggerated doll-like eyes, flushed cheeks, oversized lashes, porcelain skin, hyper-feminine" },
  { key: "glazed-doll-skin",  label: "Glazed Doll Skin",      prompt: "glazed doughnut skin aesthetic, ultra-dewy glassy complexion, luminous highlight, Hailey Bieber-inspired skin finish" },
  { key: "bleached-brows",    label: "Bleached Brows",        prompt: "bleached brow high-fashion editorial look, bold unconventional beauty, avant-garde model aesthetic" },
  { key: "grillz-selfie",     label: "Grillz Selfie",         prompt: "grillz selfie aesthetic, hip-hop glamour, street fashion, gold grill close-up, bold street culture energy" },
  { key: "object-makeup",     label: "Object Makeup",         prompt: "object makeup art aesthetic, surreal beauty, non-traditional materials on face, experimental editorial makeup" },

  // Mood and lighting
  { key: "clouded-dream",  label: "Clouded Dream",  prompt: "hazy dream-like softness, dreamy blur, overcast diffusion, ethereal mood, soft pastel dreamscape" },
  { key: "foggy-morning",  label: "Foggy Morning",  prompt: "early morning fog atmosphere, soft diffused light, muted desaturated palette, serene misty quiet" },
  { key: "nicotine-glow",  label: "Nicotine Glow",  prompt: "warm amber nicotine-tone glow, cigarette-smoke haze, late-night bar atmosphere, yellow-orange cast" },
  { key: "static-glow",    label: "Static Glow",    prompt: "electric static glow effect, CRT scanline texture, neon static haze, analog broadcast feel" },
  { key: "spotlight",      label: "Spotlight",      prompt: "dramatic single spotlight, theatrical stage isolation, deep black background, performance energy" },
  { key: "overexposed",    label: "Overexposed",    prompt: "intentionally overexposed photography, blown-out highlights, ethereal white haze, dreamy washed brightness" },

  // Location-influenced
  { key: "amalfi-summer",   label: "Amalfi Summer",   prompt: "Amalfi Coast summer aesthetic, Mediterranean blue water, golden sun, lemon groves, Italian coastal luxury" },
  { key: "night-beach",     label: "Night Beach",     prompt: "night beach photography, bioluminescent waves or beach bonfire, moonlit sand, after-dark coastal mood" },
  { key: "office-beach",    label: "Office Beach",    prompt: "corporate meets coastal aesthetic, business casual on the beach, ironic juxtaposition, satire of laptop-on-the-beach work culture" },
  { key: "subway",          label: "Subway",          prompt: "underground subway station photography, fluorescent lighting, urban grit, commuter crowds, city transit atmosphere" },
  { key: "street-view",     label: "Street View",     prompt: "documentary street photography, candid urban moments, 35mm film grain, humanistic composition" },
  { key: "tokyo-streetstyle",label: "Tokyo Streetstyle", prompt: "Harajuku and Shibuya street fashion photography, eclectic layering, Japanese subculture styling, vibrant urban backdrop" },
  { key: "japandi",         label: "Japandi",         prompt: "Japandi aesthetic fusion, Japanese-Scandinavian minimalism, warm wood tones, wabi-sabi imperfection, natural material beauty" },

  // VFX / digital styles
  { key: "glitch",         label: "Glitch",         prompt: "digital glitch art aesthetic, pixel corruption, chromatic aberration, data-error visual artifacts, tech-decay beauty" },
  { key: "invertethereal", label: "Invertethereal", prompt: "inverted color ethereal aesthetic, surreal color-inverted dreamscape, celestial distortion, otherworldly beauty" },
  { key: "mixed-media",    label: "Mixed Media",    prompt: "mixed media collage aesthetic, layered textures and imagery, analog and digital fusion, experimental art composition" },
  { key: "paper-face",     label: "Paper Face",     prompt: "paper cut-out collage face aesthetic, torn magazine texture, zine culture DIY, lo-fi art composition" },
  { key: "fashion-show",   label: "FashionShow",    prompt: "runway fashion show photography, catwalk editorial, fashion week atmosphere, dramatic model stride" },
  { key: "graffiti",       label: "Graffiti",       prompt: "street graffiti backdrop urban art photography, spray-paint walls, hip-hop culture, raw urban energy" },
  { key: "movie",          label: "Movie",          prompt: "cinematic film still aesthetic, widescreen aspect ratio, dramatic scene composition, narrative tension, anamorphic lens" },
  { key: "tumblr",         label: "Tumblr",         prompt: "early 2010s Tumblr aesthetic, indie teen photography, soft focus nature, vintage filters, melancholy nostalgia" },
  { key: "rhyme-and-blues",label: "Rhyme & Blues",  prompt: "R&B music aesthetic, moody blue palette, soul and rhythm, atmospheric depth, hip-hop and soul fusion" },
  { key: "green-editorial",label: "Green Editorial",prompt: "lush green editorial photography, botanical maximalism, verdant color palette, nature-saturated fashion" },

  // Specific activity scenes
  { key: "eating-food",      label: "Eating Food",     prompt: "mukbang-adjacent eating close-up, food content photography, indulgent close-up of subject eating, warm sensory composition" },
  { key: "sunbathing",       label: "Sunbathing",      prompt: "golden hour sunbathing photography, sun-kissed skin, beach or rooftop leisure, warm bronzed summer glow" },
  { key: "selfcare",         label: "Selfcare",        prompt: "self-care aesthetic photography, skincare routine, cozy bathroom or bedroom, clean girl aesthetic, soft wellness ambiance" },
  { key: "crossing-street",  label: "Crossing the Street", prompt: "dynamic street crossing candid, urban motion, pedestrian crossing energy, city life in motion" },
  { key: "sitting-on-street",label: "Sitting on the Street", prompt: "editorial subject sitting on urban sidewalk, casual yet styled, street fashion grounded in city concrete" },
  { key: "fisheye-style",    label: "Fisheye",         prompt: "extreme fisheye lens distortion, ultra-wide circular perspective, skate-video and rap-video energy, POV lens barrel effect" },
  { key: "medieval",         label: "Medieval",        prompt: "medieval illuminated manuscript aesthetic, ornate borders, aged parchment texture, heraldic color palette, pre-Renaissance art style" },
  { key: "street-photography", label: "Street Photography", prompt: "decisive-moment street photography, Henri Cartier-Bresson influence, 35mm candid, black-and-white or muted film, urban human interest" },

  // SOUL named location presets
  { key: "escalator",    label: "Escalator",    prompt: "urban escalator photography, motion blur of moving stairs, glass and steel mall or metro aesthetic" },
  { key: "library",      label: "Library",      prompt: "classic library setting photography, warm reading lamp light, book-lined walls, intellectual ambiance, golden wood shelving" },
  { key: "gallery",      label: "Gallery",      prompt: "white-cube art gallery setting, museum natural light, clean minimal backdrop, contemporary art world atmosphere" },
  { key: "mt-fuji",      label: "Mt. Fuji",     prompt: "iconic Mt. Fuji backdrop, Japanese landscape beauty, seasonal sakura or snow, serene spiritual scale" },
  { key: "sunset-beach", label: "Sunset Beach", prompt: "warm sunset beach photography, orange and purple sky gradient, silhouette against ocean horizon, golden hour magic" },
  { key: "flight-mode",  label: "Flight Mode",  prompt: "airplane window seat photography, aerial cloud views, travel aesthetic, soft diffused window light, 36000 feet above earth" },
  { key: "angel-wings",  label: "Angel Wings",  prompt: "ethereal angel wings concept, heavenly white feathers, divine light, celestial composition, spiritual and transcendent beauty" },
  { key: "geominimal",   label: "Geominimal",   prompt: "geometric minimalism aesthetic, clean geometric shapes, limited color palette, mathematical composition, modern design sensibility" },
  { key: "seven",        label: "7\\",           prompt: "7\\ SOUL preset style, stylized portrait angle, signature Higgsfield SOUL aesthetic" },
];

// ─── CAMERA MOTIONS ───────────────────────────────────────────────────────────

export interface CameraMotion {
  key: string;
  label: string;
  /** Motion clause for image to video models */
  prompt: string;
  category: "orbital" | "dolly" | "crane" | "zoom" | "pan-tilt" | "specialty" | "fpv" | "tracking" | "timelapse" | "action" | "vfx" | "general";
}

export const CAMERA_MOTIONS: CameraMotion[] = [
  // Bullet Time
  { key: "bullet-time",        label: "Bullet Time",             prompt: "Matrix-style bullet time freeze with rotating camera array, subject slowed to ultra-slow motion as camera orbits in 360 degrees", category: "specialty" },
  // Crash Zoom
  { key: "crash-zoom-in",      label: "Crash Zoom In",           prompt: "ultra-fast crash zoom rushing in on subject, explosive impact emphasis", category: "zoom" },
  { key: "crash-zoom-out",     label: "Crash Zoom Out",          prompt: "ultra-fast crash zoom rushing out from subject, dramatic reveal", category: "zoom" },
  // Orbit / Rotation
  { key: "360-orbit",          label: "360 Orbit",               prompt: "smooth 360-degree orbital camera rotation around the subject, continuous parallax", category: "orbital" },
  { key: "3d-rotation",        label: "3D Rotation",             prompt: "dynamic 3D camera rotation through three-dimensional space around the subject", category: "orbital" },
  // Dolly
  { key: "dolly-in",           label: "Dolly In",                prompt: "smooth dolly push toward the subject on a track, building intimacy", category: "dolly" },
  { key: "dolly-out",          label: "Dolly Out",               prompt: "smooth dolly pull away from the subject on a track, expanding the world", category: "dolly" },
  { key: "dolly-left",         label: "Dolly Left",              prompt: "lateral dolly movement tracking left parallel to the subject", category: "dolly" },
  { key: "dolly-right",        label: "Dolly Right",             prompt: "lateral dolly movement tracking right parallel to the subject", category: "dolly" },
  { key: "super-dolly-in",     label: "Super Dolly In",          prompt: "fast aggressive dolly rush toward subject, high energy dramatic push", category: "dolly" },
  { key: "super-dolly-out",    label: "Super Dolly Out",         prompt: "fast aggressive dolly pull from subject, sweeping dramatic pull-back", category: "dolly" },
  { key: "double-dolly",       label: "Double Dolly",            prompt: "compound dolly combining forward push with lateral tracking simultaneously", category: "dolly" },
  { key: "dolly-zoom-in",      label: "Dolly Zoom In",           prompt: "Vertigo effect: simultaneous dolly backward and zoom in, subject stays same size while background expands", category: "dolly" },
  { key: "dolly-zoom-out",     label: "Dolly Zoom Out",          prompt: "reverse Vertigo effect: simultaneous dolly forward and zoom out, background compresses", category: "dolly" },
  // Crane / Jib
  { key: "crane-up",           label: "Crane Up",                prompt: "smooth crane or jib arm lifting the camera upward, majestic rising shot", category: "crane" },
  { key: "crane-down",         label: "Crane Down",              prompt: "smooth crane or jib arm lowering the camera downward, deliberate descending shot", category: "crane" },
  { key: "crane-over-the-head",label: "Crane Over The Head",     prompt: "dramatic crane sweep arcing over and above the subject, overhead reveal", category: "crane" },
  { key: "jib-up",             label: "Jib Up",                  prompt: "jib arm rising shot from low angle to high angle, lifting perspective", category: "crane" },
  { key: "jib-down",           label: "Jib Down",                prompt: "jib arm descending shot from high angle to low angle, grounding perspective", category: "crane" },
  // Zoom
  { key: "zoom-in",            label: "Zoom In",                 prompt: "optical zoom in toward subject, telephoto compression increasing", category: "zoom" },
  { key: "zoom-out",           label: "Zoom Out",                prompt: "optical zoom out from subject, widening the frame", category: "zoom" },
  { key: "rapid-zoom-in",      label: "Rapid Zoom In",           prompt: "quick punchy zoom in on subject, high-energy music video style", category: "zoom" },
  { key: "rapid-zoom-out",     label: "Rapid Zoom Out",          prompt: "quick punchy zoom out from subject, revealing the wider scene", category: "zoom" },
  { key: "yoyo-zoom",          label: "YoYo Zoom",               prompt: "oscillating zoom in and out repeatedly, disorienting tension-building pulsing effect", category: "zoom" },
  { key: "eating-zoom",        label: "Eating Zoom",             prompt: "slow intimate zoom in during eating close-up, mukbang ASMR approach", category: "zoom" },
  // Focus
  { key: "focus-change",       label: "Focus Change",            prompt: "rack focus shift from foreground to background or vice versa, selective focal pull", category: "specialty" },
  // Arc
  { key: "arc-left",           label: "Arc Left",                prompt: "smooth arcing camera movement curving left around the subject at medium radius", category: "orbital" },
  { key: "arc-right",          label: "Arc Right",               prompt: "smooth arcing camera movement curving right around the subject at medium radius", category: "orbital" },
  // Pan / Tilt / Truck
  { key: "pan-left",           label: "Pan Left",                prompt: "smooth camera pan left, pivoting horizontally on tripod axis", category: "pan-tilt" },
  { key: "pan-right",          label: "Pan Right",               prompt: "smooth camera pan right, pivoting horizontally on tripod axis", category: "pan-tilt" },
  { key: "tilt-up",            label: "Tilt Up",                 prompt: "smooth camera tilt upward, pivoting vertically to reveal height and scale", category: "pan-tilt" },
  { key: "tilt-down",          label: "Tilt Down",               prompt: "smooth camera tilt downward, pivoting vertically to reveal ground", category: "pan-tilt" },
  { key: "whip-pan",           label: "Whip Pan",                prompt: "ultra-fast whip pan rotation creating motion blur transition, jump-cut energy", category: "pan-tilt" },
  { key: "dutch-angle",        label: "Dutch Angle",             prompt: "canted Dutch angle tilt, psychological unease, tension and disorientation", category: "pan-tilt" },
  { key: "truck-left",         label: "Truck Left",              prompt: "lateral camera truck movement sliding left while maintaining framing direction", category: "pan-tilt" },
  { key: "truck-right",        label: "Truck Right",             prompt: "lateral camera truck movement sliding right while maintaining framing direction", category: "pan-tilt" },
  // FPV / Aerial
  { key: "fpv-drone",          label: "FPV Drone",               prompt: "FPV racing drone perspective, aggressive first-person aerobatic flight through environment", category: "fpv" },
  { key: "flying",             label: "Flying",                  prompt: "aerial flying camera, smooth overhead drone glide over landscape", category: "fpv" },
  { key: "flying-cam-transition",label:"Flying Cam Transition",  prompt: "drone flying cam transitional shot, aerial move linking two scenes", category: "fpv" },
  { key: "overhead",           label: "Overhead",                prompt: "directly overhead top-down bird's-eye camera angle, flat lay perspective", category: "fpv" },
  { key: "hyperlapse",         label: "Hyperlapse",              prompt: "moving hyperlapse, time-compressed journey through space with camera locomotion", category: "timelapse" },
  // Specialty
  { key: "snorricam",          label: "Snorricam",               prompt: "body-mounted Snorricam shot, camera rigidly attached to actor, world moves around stationary subject face", category: "specialty" },
  { key: "push-to-glass",      label: "Push to Glass",           prompt: "camera pushes through transparent glass or window surface, crossing barrier from exterior to interior", category: "specialty" },
  { key: "through-object-in",  label: "Through Object In",       prompt: "camera passes through a solid object moving into the scene beyond", category: "specialty" },
  { key: "through-object-out", label: "Through Object Out",      prompt: "camera passes through a solid object moving out of the scene", category: "specialty" },
  { key: "anamorphic-flares",  label: "Anamorphic Flares",       prompt: "dramatic horizontal anamorphic lens flares, cinematic oval bokeh, blue streak light artifacts", category: "specialty" },
  { key: "film-stock",         label: "Film Stock",              prompt: "authentic analog film stock texture, celluloid grain, organic color response", category: "specialty" },
  { key: "dirty-lens",         label: "Dirty Lens",              prompt: "dirty smeared lens flare and bokeh artifacts, grungy weathered camera optics", category: "specialty" },
  { key: "low-shutter",        label: "Low Shutter",             prompt: "low shutter speed motion blur, 180-degree rule violated for dreamy smear, slow shutter trails", category: "specialty" },
  { key: "depth-of-field-control", label: "Depth of Field Control", prompt: "precise depth of field manipulation, selective focus breathing, optical bokeh control", category: "specialty" },
  { key: "incline",            label: "Incline",                 prompt: "camera moves along an incline or slope, tilting trajectory through hilly or ramp environment", category: "specialty" },
  { key: "robo-arm",           label: "Robo Arm",                prompt: "precision robotic arm camera movement, hyper-controlled mechanical motion path", category: "specialty" },
  { key: "wiggle",             label: "Wiggle",                  prompt: "subtle handheld-style oscillating wiggle, nervous energy micro-movement", category: "specialty" },
  { key: "fisheye",            label: "Fisheye",                 prompt: "extreme fisheye lens barrel distortion, ultra-wide circular perspective", category: "specialty" },
  // Tracking
  { key: "head-tracking",      label: "Head Tracking",           prompt: "camera locked to and tracking subject's head movement, intimate close-follow", category: "tracking" },
  { key: "hero-cam",           label: "Hero Cam",                prompt: "action-hero POV or follow-cam, dynamic low tracking behind protagonist against epic sky", category: "tracking" },
  { key: "object-pov",         label: "Object POV",              prompt: "first-person perspective from an object's point of view, unusual inanimate POV shot", category: "tracking" },
  { key: "eyes-in",            label: "Eyes In",                 prompt: "slow intimate push toward subject's eyes, extreme close-up eye contact", category: "tracking" },
  { key: "mouth-in",           label: "Mouth In",                prompt: "camera slowly pushing in toward open mouth, intense visceral zoom", category: "tracking" },
  { key: "car-chasing",        label: "Car Chasing",             prompt: "dynamic vehicle chase camera, high-speed parallel tracking of speeding car", category: "tracking" },
  { key: "car-grip",           label: "Car Grip",                prompt: "car-mounted grip camera shot, vehicle surface attachment looking outward", category: "tracking" },
  { key: "road-rush",          label: "Road Rush",               prompt: "road-level rushing forward camera, ultra-fast low ground-level race through environment", category: "tracking" },
  // Timelapse
  { key: "timelapse-glam",     label: "Timelapse Glam",          prompt: "glamorous high-fashion timelapse, beauty and style unfolding over compressed time", category: "timelapse" },
  { key: "timelapse-human",    label: "Timelapse Human",         prompt: "human activity timelapse, compressed documentary of people moving through space", category: "timelapse" },
  { key: "timelapse-landscape",label: "Timelapse Landscape",     prompt: "epic landscape timelapse, clouds racing, sun tracking, nature in compressed motion", category: "timelapse" },
  // Action
  { key: "action-run",         label: "Action Run",              prompt: "handheld action-follow running shot, kinetic chase-cam energy", category: "action" },
  { key: "basketball-dunks",   label: "Basketball Dunks",        prompt: "low-angle upward tracking of basketball dunk, athletic explosive action", category: "action" },
  { key: "bts",                label: "BTS",                     prompt: "behind-the-scenes documentary camera style, candid on-set footage energy", category: "action" },
  { key: "buckle-up",          label: "Buckle Up",               prompt: "fast-paced buckle-up seatbelt framing, car interior action preparation energy", category: "action" },
  { key: "glam",               label: "Glam",                    prompt: "glamour-shot camera move, slow orbiting beauty rotation, spotlight-adjacent", category: "action" },
  { key: "handheld",           label: "Handheld",                prompt: "naturalistic handheld camera, organic micro-shake, documentary and narrative authenticity", category: "action" },
  { key: "kiss",               label: "Kiss",                    prompt: "slow romantic push in during kiss, intimate close-up approach", category: "action" },
  { key: "levitation",         label: "Levitation",              prompt: "camera framing subject floating in levitation, low angle magic-trick perspective", category: "action" },
  { key: "rap-flex",           label: "Rap Flex",                prompt: "music video rap flex camera energy, low-angle flex shots, bold aggressive framing", category: "action" },
  { key: "lazy-susan",         label: "Lazy Susan",              prompt: "slow rotating platform-style rotation of the subject in place, camera static", category: "action" },
  // VFX category
  { key: "tentacles",          label: "Tentacles",               prompt: "surreal tentacle-like multiple camera arm extensions, simultaneous multi-angle approach", category: "vfx" },
  // Agent / AI directed
  { key: "agent-reveal",       label: "Agent Reveal",            prompt: "cinematic reveal shot emerging from concealment, protagonist entrance, spy-thriller pacing", category: "action" },
  { key: "abstract",           label: "Abstract",                prompt: "abstract camera motion, experimental non-representational movement, art-cinema aesthetics", category: "vfx" },
  // General / baseline
  { key: "static",             label: "Static",                  prompt: "completely static locked-off camera, tripod-mounted, no movement", category: "general" },
  { key: "general",            label: "General",                 prompt: "general camera movement, natural cinematic camera behavior", category: "general" },
];

// ─── CAMERA BODIES ────────────────────────────────────────────────────────────

/** Prompt clause per camera body */
export const CAMERA_BODIES: Record<string, string> = {
  "red-v-raptor":           "shot on RED V-Raptor, high dynamic range cinema camera, vivid detailed 8K RAW color science",
  "sony-venice":            "shot on Sony Venice, pastel skin tones, wide dynamic range, anamorphic full-frame cinema color science",
  "imax-film":              "shot on IMAX film camera, enormous 15/70mm film frame, maximum resolution film grain, epic documentary scale",
  "arri-alexa-35":          "shot on ARRI Alexa 35, warm organic cinema color science, natural skin tones, 4.6K photoreceptive image quality",
  "arriflex-16sr":          "shot on ARRIFLEX 16SR, 16mm film grain, high-contrast indie cinema aesthetic, gritty authentic analog texture",
  "panavision-millennium-dxl2": "shot on Panavision Millennium DXL2, anamorphic oval bokeh, rich saturated cinematic palette, epic scope framing",
  "phone":                  "shot on a smartphone, mobile photography aesthetic, computational imaging, Instagram-ready natural quality",
};

// ─── LENSES ───────────────────────────────────────────────────────────────────

/** Prompt clause per cinema lens */
export const LENSES: Record<string, string> = {
  "lensbaby":            "Lensbaby selective focus lens, tilted focal plane, swirling bokeh blur, dreamy selective sharpness",
  "hawk-v-lite":         "Hawk V-Lite anamorphic lens, flared oval bokeh, vintage anamorphic character, cinemascope elegance",
  "laowa-macro":         "Laowa macro lens, extreme close-up magnification, ultra-fine detail rendering, insect-eye proximity",
  "canon-k35":           "Canon K-35 vintage cinema prime, warm characterful rendering, organic softness, 1970s Hollywood color science",
  "panavision-c-series": "Panavision C-Series anamorphic, classic Hollywood oval bokeh, breathable wide-open feel, cinematic heritage",
  "arri-signature-prime":"ARRI Signature Prime, modern clinical sharpness, clean corner-to-corner rendering, precision optical science",
  "cooke-s4":            "Cooke S4 lens, the Cooke Look, warm organic rendering, beautiful softness wide open, beloved skin tones",
  "petzval-swirl":       "Petzval lens, iconic swirling bokeh vortex, vintage brass barrel, 19th century optical character",
  "soviet-vintage":      "Soviet vintage cinema lens, Helios swirl, warm organic rendering, Eastern European film aesthetic",
  "jdc-xtal-xpress":     "JDC Xtal Xpress anamorphic, large-format squeeze, modern anamorphic flares, sharp center with character falloff",
  "zeiss-ultra-prime":   "Zeiss Ultra Prime, clinical sharp neutral rendering, T-stop consistency, broadcast and cinema precision",
  "compact-anamorphic":  "compact anamorphic lens, cost-efficient anamorphic squeeze, horizontal streak flares, oval bokeh",
  "classic-anamorphic":  "classic anamorphic lens, 2.39:1 cinematic scope, breathable wide-open beauty, era-appropriate flares",
};

// ─── APERTURES ────────────────────────────────────────────────────────────────

/** Prompt clause per aperture setting */
export const APERTURES: Record<string, string> = {
  "f14": "shot at f/1.4, extremely shallow depth of field, creamy cinematic bokeh, subject isolated from environment",
  "f4":  "shot at f/4, moderate depth of field, sharp subject with softly blurred background, versatile cinema aperture",
  "f11": "shot at f/11, deep depth of field, foreground and background both in crisp focus, landscape and architecture aperture",
};

// ─── GENRES ───────────────────────────────────────────────────────────────────

/** Prompt clause per cinema genre */
export const GENRES: Record<string, string> = {
  "general":       "general cinematic pacing and visual language, versatile film grammar",
  "action":        "action genre, high-energy kinetic movement, fast cuts, explosive motion, adrenaline-fueled choreography",
  "spectacle":     "spectacle genre, massive scale epic visuals, awe-inspiring production design, crowd-pleasing grand scope",
  "intimate":      "intimate genre, close personal framing, quiet emotional beats, slow deliberate movement, human connection",
  "horror":        "horror genre, dread-building tension, unsettling angles, dark shadows, psychological unease, jump-scare timing",
  "comedy":        "comedy genre, playful framing, physical gag timing, bright warm lighting, accessible approachable energy",
  "noir":          "noir genre, high-contrast shadows, rain-slicked streets, venetian blind light patterns, cynical atmosphere",
  "drama":         "drama genre, emotionally resonant framing, motivated camera, nuanced performance-focused composition",
  "epic":          "epic genre, heroic scope, wide establishing vistas, massive production value, mythological grandeur",
  "suspense":      "suspense genre, tightly held tension, claustrophobic framing, unseen threat energy, Hitchcock grammar",
  "western":       "western genre, wide open landscapes, golden dust, squinting close-ups, Leone-inspired operatic scale",
  "documentary":   "documentary genre, handheld naturalism, observational distance, talking-head composition, verite authenticity",
  "music-video":   "music video genre, rhythmically cut motion, stylized visual excess, performance energy, treatment-driven aesthetic",
  "sci-fi":        "sci-fi genre, futuristic set design, cool color temperature, practical effects texture, speculative world-building",
  "commercial":    "commercial genre, product-forward clean photography, bright aspirational lighting, branded polish",
};

// ─── COLOR GRADES ─────────────────────────────────────────────────────────────

/** Prompt clause per color grade */
export const COLOR_GRADES: Record<string, string> = {
  "warm":           "warm color grade, amber and orange tones, cozy golden hue, elevated skin warmth",
  "teal-and-orange":"teal-and-orange Hollywood color grade, complementary skin vs shadow palette, blockbuster studio look",
  "muted-film":     "muted film color grade, desaturated analog palette, lifted blacks, organic restrained color",
  "high-contrast":  "high-contrast color grade, deep blacks, bright highlights, punchy dramatic contrast, no midtone comfort",
  "film-noir":      "film noir color grade, stark black-and-white or near-monochrome, extreme contrast, classic shadow play",
  "golden-hour":    "golden hour color grade, warm orange-gold sunlight, magic hour haze, romantic glowing atmosphere",
  "blockbuster":    "blockbuster color grade, saturated primaries, Hollywood cinema polish, maximum production value look",
  "overcast-indie": "overcast indie color grade, flat diffused light, desaturated muted palette, indie film intimacy",
  "documentary":    "documentary color grade, naturalistic neutral color science, authentic unstylized look, journalistic honesty",
};

// ─── SPEED RAMPS ─────────────────────────────────────────────────────────────

/** Prompt clause per speed ramp style */
export const SPEED_RAMPS: Record<string, string> = {
  "linear":      "linear constant playback speed, no time manipulation",
  "auto":        "automatic AI-driven speed ramp selection based on content motion",
  "flash-in":    "flash in speed ramp, extremely fast speed at clip start then normalizing to real-time",
  "flash-out":   "flash out speed ramp, normal speed then extreme speed burst at clip end",
  "slow-motion": "slow motion speed ramp, high-frame-rate slow-motion capture, ultra-detailed motion",
  "speed-up":    "speed up time ramp, action accelerating to hyperspeed, energy escalation",
  "impact":      "impact speed ramp, freeze or near-freeze at moment of physical impact for emphasis",
  "ramp-up":     "ramp up speed, starting slow then accelerating, energy building anticipation",
  "custom":      "custom speed ramp curve, bespoke time-remapping for specific creative need",
};

// ─── TREND PACKS ─────────────────────────────────────────────────────────────

export interface TrendPack {
  key: string;
  label: string;
  /** Prompt clause capturing the trend aesthetic */
  prompt: string;
}

export const TREND_PACKS: TrendPack[] = [
  // Instadump / named UGC trend packs
  { key: "mukbang",         label: "Mukbang",           prompt: "mukbang content aesthetic, large food portions close-up, ASMR eating sounds implied, warm flattering lighting, YouTube and TikTok food content format" },
  { key: "skibidi",         label: "Skibidi",           prompt: "Skibidi meme culture aesthetic, surreal absurdist internet humor, gen-Z irony, chaotic energy, memetic character distortion" },
  { key: "on-fire",         label: "On Fire",           prompt: "on fire trending aesthetic, explosive hot viral energy, flame and heat visual metaphor, maximum trending moment" },
  { key: "cloud-surf",      label: "Cloud Surf",        prompt: "cloud surfing aesthetic, ethereal above-the-clouds perspective, dreamlike floating, celestial freedom" },
  { key: "idol",            label: "Idol",              prompt: "K-pop idol aesthetic, perfect stage lighting, synchronized performance energy, parasocial fan-meeting intimacy, glossy idol styling" },
  { key: "paparazzi",       label: "Paparazzi",         prompt: "paparazzi celebrity candid aesthetic, direct flash on location, tabloid energy, off-duty celebrity moment" },
  { key: "2000s-paparazzi", label: "2000s Paparazzi",   prompt: "early 2000s paparazzi photography, heavy direct flash, TMZ era celebrity snap, oversaturated tabloid aesthetic" },
  { key: "candid-paparazzi",label: "Candid Paparazzi",  prompt: "candid paparazzi street shot, telephoto lens unposed moment, celebrity off-guard natural capture" },
  { key: "red-carpet",      label: "Red Carpet",        prompt: "red carpet premiere event photography, studio editorial flash, couture fashion showcase, celebrity gala glamour" },
  { key: "male-archive",    label: "Male Archive",      prompt: "male archive fashion aesthetic, editorial menswear, archive vintage designer pieces, high-concept masculine styling" },
  { key: "cool-girl-dump",  label: "Cool Girl Dump",    prompt: "cool girl photo dump aesthetic, effortlessly casual cool, mix of candids and posed, indie girl aesthetic, low-effort high-impact" },
  { key: "ugc-style",       label: "UGC Style",         prompt: "authentic user-generated content style, raw unpolished handheld video feel, relatable casual creator energy" },
  { key: "mirror-selfie",   label: "Mirror Selfie",     prompt: "mirror selfie aesthetic, bathroom or full-length mirror, outfit check energy, candid social media vibe" },
  { key: "pool-jump",       label: "Pool Jump",         prompt: "dynamic pool jump action photography, mid-air summer energy, water splash anticipation, fun vacation content" },
  { key: "group-photo",     label: "Group Photo",       prompt: "group photo content format, multiple people, celebration or gathering energy, social documentation" },
  { key: "drift-racing",    label: "Drift Racing",      prompt: "drift racing aesthetic, tire smoke, sideways car control, motorsport adrenaline, Tokyo drift energy" },
  { key: "zombie-dance",    label: "Zombie Dance",      prompt: "zombie dance viral trend, horror-comedy movement, undead choreography, Halloween viral energy" },
  { key: "earth-zoom-out",  label: "Earth Zoom Out",    prompt: "earth zoom out viral effect, pulling back from ground level to planetary scale, cosmic perspective shift" },
  { key: "neon-city",       label: "Neon City",         prompt: "neon city night aesthetic, cyberpunk urban glow, rain-slicked streets, neon sign reflections, nocturnal energy" },
  { key: "summer-haze",     label: "Summer Haze",       prompt: "summer haze aesthetic, heat shimmer, golden lazy summer days, warm overexposed outdoor feel" },
  { key: "nightline",       label: "Nightline",         prompt: "nightline urban aesthetic, late-night city energy, TV news-adjacent late night vibe, urban night journalism" },
  { key: "baseball-game",   label: "Baseball Game",     prompt: "baseball game sports content, stadium atmosphere, athletic action, American sports culture" },
];

// ─── VIRAL PRESETS ────────────────────────────────────────────────────────────

export interface ViralPreset {
  key: string;
  label: string;
  /** Prompt clause for the VFX or cinematic effect */
  prompt: string;
}

export const VIRAL_PRESETS: ViralPreset[] = [
  { key: "drift-racing-vfx",      label: "Drift Racing VFX",        prompt: "cinematic drift racing sequence, low-angle car tire smoke, motorsport adrenaline, wide-angle track edit" },
  { key: "zombie-dance-vfx",      label: "Zombie Dance VFX",        prompt: "horror-comedy zombie choreography sequence, undead group movement, theatrical makeup, viral dance format" },
  { key: "earth-zoom-out-vfx",    label: "Earth Zoom Out VFX",      prompt: "cinematic earth zoom out VFX, pulling back from street level through atmosphere to space, cosmic scale reveal" },
  { key: "earth-zoom-in",         label: "Earth Zoom In",           prompt: "dramatic earth zoom in VFX, plunging from orbit through clouds down to street level, God-eye reveal" },
  { key: "disintegration",        label: "Disintegration",          prompt: "snap disintegration VFX, subject dissolving into dust particles, Marvel-style dramatic dissolve effect" },
  { key: "bullet-time-scene",     label: "Bullet Time Scene",       prompt: "Matrix bullet-time frozen scene, 360-degree rotating freeze, ultra-slow-motion projectile time-freeze" },
  { key: "bullet-time-white",     label: "Bullet Time White",       prompt: "bullet time effect with white flash overexposure, bright high-key freeze moment" },
  { key: "bullet-time-splash",    label: "Bullet Time Splash",      prompt: "bullet time effect with water or liquid splash freeze, frozen droplets suspended in space" },
  { key: "sword-and-sorcery",     label: "Sword and Sorcery",       prompt: "fantasy sword and sorcery action sequence, magical combat, spell effects, epic fantasy battle energy" },
  { key: "arena-zero",            label: "Arena Zero",              prompt: "arena combat zero-gravity battle aesthetic, sci-fi fighting game energy, dramatic arena sequence" },
  { key: "superfast-flight",      label: "Superfast Flight",        prompt: "superhero superfast flight effect, streaking motion blur, aerial acceleration beyond speed of sound" },
  { key: "face-punch",            label: "Face Punch",              prompt: "slow-motion impact face punch VFX, cheek ripple physics, dramatic contact effect" },
  { key: "still-world",           label: "Still World",             prompt: "world frozen still while subject moves freely, reverse time-stop effect, frozen crowd with moving protagonist" },
  { key: "animal-ride",           label: "Animal Ride",             prompt: "mythological or fantastical animal ride sequence, majestic creature mounting, epic journey beginning" },
  { key: "me-and-pet-transformation", label: "Me and Pet Transformation", prompt: "human and pet dual transformation sequence, synchronized costume or species swap reveal" },
  { key: "magic-spell",           label: "Magic Spell",             prompt: "magical spell casting VFX, glowing energy orbs, particle beam, fantastical power unleashed" },
  { key: "animal-chase",          label: "Animal Chase",            prompt: "wildlife or mythological animal chase sequence, predator-pursuit energy, wildlife action format" },
  { key: "wrestle",               label: "Wrestle",                 prompt: "WWE-style wrestling dramatic sequence, theatrical combat, ring atmosphere, sports entertainment energy" },
  { key: "casual-monster-slayer", label: "Casual Monster Slayer",   prompt: "absurdist casual monster slaying humor, nonchalant hero defeating giant creature, comedy-action mash-up" },
  { key: "building-explosion",    label: "Building Explosion",      prompt: "cinematic building explosion VFX, structural collapse fireball, blockbuster-scale destruction sequence" },
  { key: "cgi-breakdown",         label: "CGI Breakdown",           prompt: "behind-the-scenes CGI wireframe breakdown effect, revealing digital scaffolding beneath real-world imagery" },
  { key: "storm-giant",           label: "Storm Giant",             prompt: "mythological storm giant emerging from clouds, elemental weather deity scale, awe-inspiring natural force" },
  { key: "portal",                label: "Portal",                  prompt: "interdimensional portal VFX, circular energy vortex, sci-fi teleportation gateway opening" },
  { key: "glitch",                label: "Glitch",                  prompt: "digital glitch VFX effect, pixel corruption, chromatic aberration split, data-error visual distortion" },
  { key: "x-ray",                 label: "X-Ray",                   prompt: "X-ray reveal VFX, seeing through skin to bones and internals, medical scan aesthetic layered on live action" },
  { key: "wireframe",             label: "Wireframe",               prompt: "CGI wireframe overlay VFX, digital construction grid revealed over real subject, Matrix digital-reveal aesthetic" },
  { key: "golf-major",            label: "Golf Major",              prompt: "cinematic golf major sports broadcast format, manicured course, professional tournament atmosphere" },
  { key: "race-track",            label: "Race Track",              prompt: "motorsport race track cinematic sequence, speed, competition tension, Formula racing grandeur" },
  { key: "drown-in-music",        label: "Drown in Music",          prompt: "music immersion VFX, sound wave visualizer, submerged-in-sound audio-reactive visual metaphor" },
  { key: "free-fall",             label: "Free Fall",               prompt: "free fall skydiving or falling sensation, rush of air, terminal velocity perspective" },
  { key: "soul-fighter",          label: "Soul Fighter",            prompt: "fighting game soul energy aesthetic, spectral fighter aura, Street Fighter and Tekken game character energy" },
  { key: "tuscan-yoga",           label: "Tuscan Yoga",             prompt: "outdoor yoga in Tuscan countryside setting, golden hill landscape, wellness lifestyle content, serene morning" },
  { key: "apex-hunter",           label: "Apex Hunter",             prompt: "apex predator hunter aesthetic, wilderness tracking, survival intensity, peak hunter energy" },
  { key: "in-the-dark",           label: "In the Dark",             prompt: "dramatic low-key dark lighting aesthetic, near-silhouette subject, thriller night scene" },
  { key: "exit-the-dream",        label: "Exit the Dream",          prompt: "surreal dream exit sequence, reality-dissolving VFX, waking from dream transition effect" },
  { key: "ending-fairy",          label: "Ending Fairy",            prompt: "fairy tale happy ending aesthetic, magical sparkle VFX, storybook closure, enchanted finale" },
  { key: "dragon-fantasy",        label: "Dragon Fantasy",          prompt: "epic dragon fantasy scene, fire-breathing dragon in flight, medieval fantasy world-building" },
  { key: "night-vision",          label: "Night Vision",            prompt: "military night-vision green-phosphor aesthetic, thermal imaging overlay, tactical surveillance look" },
  { key: "office-cctv",           label: "Office CCTV",             prompt: "office CCTV security camera aesthetic, high-angle surveillance footage, workplace voyeurism energy" },
  { key: "race-winner",           label: "Race Winner",             prompt: "victory lap race winner celebration, crossing finish line, confetti and crowd, athletic triumph moment" },
  { key: "fan-meeting",           label: "Fan Meeting",             prompt: "K-pop fan meeting intimate event aesthetic, parasocial celebrity interaction, idol fan exchange" },
  { key: "red-thread",            label: "Red Thread",              prompt: "red thread of fate aesthetic, East Asian fate-connection symbolism, narrative destiny visual" },
  { key: "kung-fu-hit",           label: "Kung Fu Hit",             prompt: "martial arts kung fu impact sequence, stylized fight choreography, Shaw Brothers-inspired action" },
  { key: "orbital-presence",      label: "Orbital Presence",        prompt: "orbital space station presence, ISS-level gravity-free floating, NASA broadcast aesthetic, cosmic authority" },
  { key: "blue-depth",            label: "Blue Depth",              prompt: "deep ocean blue depth aesthetic, underwater abyss, bioluminescent creatures, crushing pressure beauty" },
  { key: "android-assemble",      label: "Android Assemble",        prompt: "android robot assembly VFX, mechanical body construction animation, sci-fi cyborg build sequence" },
  { key: "football-invader",      label: "Football Invader",        prompt: "alien or space invader appears at football game, crowd chaos, stadium interruption spectacle" },
  { key: "final-serve",           label: "Final Serve",             prompt: "tennis championship match point final serve, grand slam pressure moment, slow-motion athletic peak" },
  { key: "3d-render",             label: "3D Render",               prompt: "photorealistic 3D CGI render aesthetic, product visualization quality, cinematic render grade" },
  { key: "summer-haze-vfx",       label: "Summer Haze VFX",         prompt: "summer haze cinematic effect, heat shimmer distortion, golden lazy summer days, warm overexposed outdoor feel" },
  { key: "neon-city-vfx",         label: "Neon City VFX",           prompt: "neon city cinematic sequence, cyberpunk urban glow, rain-slicked streets, neon sign reflections" },
];

// ─── AD FORMATS ───────────────────────────────────────────────────────────────

export interface AdFormat {
  key: string;
  label: string;
  /** Prompt clause for the ad format */
  prompt: string;
}

export const AD_FORMATS: AdFormat[] = [
  { key: "ugc",                  label: "UGC",                   prompt: "authentic user-generated content ad format, raw handheld smartphone video, relatable creator energy, no polished production" },
  { key: "tutorial",             label: "Tutorial",              prompt: "step-by-step tutorial ad format, educational how-to structure, clear demonstration pacing, informative visual hierarchy" },
  { key: "product-review",       label: "Product Review",        prompt: "honest product review ad format, hands-on testing footage, candid opinion delivery, trust-building direct-to-camera" },
  { key: "unboxing",             label: "Unboxing",              prompt: "unboxing ad format, fresh packaging reveal, anticipation build, product first impression delivery" },
  { key: "virtual-try-on",       label: "Virtual Try-On",        prompt: "virtual try-on ad format, AR clothing or product overlay on user, interactive try-before-you-buy experience" },
  { key: "tv-spot",              label: "TV Spot",               prompt: "broadcast television spot format, polished 15-30s story arc, cinematic production value, brand anthem energy" },
  { key: "hyper-motion",         label: "Hyper Motion",          prompt: "hyper motion ad format, ultra-fast kinetic editing, energy drink commercial pacing, maximum visual stimulation" },
  { key: "pro-virtual-try-on",   label: "Pro Virtual Try-On",    prompt: "professional virtual try-on ad, high-fidelity garment fitting AR, luxury retail digital experience" },
  { key: "wild-card",            label: "Wild Card",             prompt: "wild card ad format, unexpected creative concept, deliberately surprising or subversive brand communication" },
];

// ─── HOOK TYPES ───────────────────────────────────────────────────────────────

export interface HookType {
  key: string;
  label: string;
  /** Prompt clause describing the hook's opening structure */
  prompt: string;
}

export const HOOK_TYPES: HookType[] = [
  { key: "cold-open",        label: "Cold Open",        prompt: "cold open hook structure, drops viewer immediately into action without preamble, in-medias-res opening" },
  { key: "pov-setup",        label: "POV Setup",        prompt: "first-person POV hook setup, viewer placed in protagonist's perspective immediately, immersive you-are-here opening" },
  { key: "pattern-interrupt",label: "Pattern Interrupt",prompt: "pattern interrupt hook, deliberately unexpected visual or audio break that stops thumb-scroll, cognitive dissonance opener" },
  { key: "story-hook",       label: "Story Hook",       prompt: "narrative story hook opening, opens mid-story or with compelling character moment, story-forward engagement" },
];

// ─── HELPERS ─────────────────────────────────────────────────────────────────

/**
 * Generic case-insensitive key or label finder over an array catalog.
 * Returns undefined if not found.
 */
export function find<T extends { key: string; label: string }>(
  catalog: T[],
  query: string
): T | undefined {
  const q = query.toLowerCase().trim();
  return catalog.find(
    (item) => item.key.toLowerCase() === q || item.label.toLowerCase() === q
  );
}

/** Case-insensitive lookup in a Record<string, string> catalog. Returns the value or undefined. */
export function findRecord(
  catalog: Record<string, string>,
  query: string
): string | undefined {
  const q = query.toLowerCase().trim();
  const match = Object.entries(catalog).find(
    ([key]) => key.toLowerCase() === q
  );
  return match?.[1];
}

// ─── MENU FUNCTIONS ──────────────────────────────────────────────────────────

function buildMenu<T extends { key: string; label: string }>(
  catalog: T[],
  header?: string
): string {
  const lines = catalog.map((item) => `${item.key} — ${item.label}`);
  return header ? `${header}\n${lines.join("\n")}` : lines.join("\n");
}

/** Compact IMAGE_STYLES menu for Opus prompts: "key — label" per line */
export function stylesMenu(): string {
  return buildMenu(IMAGE_STYLES, "IMAGE STYLES:");
}

/** Compact CAMERA_MOTIONS menu for Opus prompts: "key — label" per line */
export function motionsMenu(): string {
  return buildMenu(CAMERA_MOTIONS, "CAMERA MOTIONS:");
}

/** Combined Cinema Studio menu: genres, color grades, speed ramps, camera bodies */
export function cinemaMenu(): string {
  const genreLines = Object.entries(GENRES)
    .map(([k]) => `${k} — ${k.replace(/-/g, " ")}`)
    .join("\n");
  const gradeLines = Object.entries(COLOR_GRADES)
    .map(([k]) => `${k} — ${k.replace(/-/g, " ")}`)
    .join("\n");
  const rampLines = Object.entries(SPEED_RAMPS)
    .map(([k]) => `${k} — ${k.replace(/-/g, " ")}`)
    .join("\n");
  const bodyLines = Object.entries(CAMERA_BODIES)
    .map(([k]) => `${k} — ${k.replace(/-/g, " ")}`)
    .join("\n");
  return [
    "GENRES:\n" + genreLines,
    "COLOR GRADES:\n" + gradeLines,
    "SPEED RAMPS:\n" + rampLines,
    "CAMERA BODIES:\n" + bodyLines,
  ].join("\n\n");
}

/** Combined TREND_PACKS + VIRAL_PRESETS menu for Opus prompts */
export function trendsMenu(): string {
  return `${buildMenu(TREND_PACKS, "TREND PACKS:")}\n\n${buildMenu(VIRAL_PRESETS, "VIRAL PRESETS:")}`;
}

/** Combined AD_FORMATS + HOOK_TYPES menu for Opus prompts */
export function adFormatsMenu(): string {
  return `${buildMenu(AD_FORMATS, "AD FORMATS:")}\n\n${buildMenu(HOOK_TYPES, "HOOK TYPES:")}`;
}

// ─── APPLY HELPERS ────────────────────────────────────────────────────────────

/**
 * Append the IMAGE_STYLES prompt clause for the given key to a base image prompt.
 * Logs a warning and returns the original prompt unchanged if the key is not found.
 */
export function applyStyle(prompt: string, key: string): string {
  const style = find(IMAGE_STYLES, key);
  if (!style) {
    console.error(`[catalog] Estilo no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${style.prompt}`;
}

/**
 * Append the CAMERA_MOTIONS prompt clause for the given key to a base prompt.
 * Logs a warning and returns the original prompt unchanged if the key is not found.
 * Accepts both new-style keys and legacy key variants (spaces/underscores normalized).
 */
export function applyMotion(prompt: string, key?: string): string {
  if (!key) return prompt;
  const normalised = key.trim().toLowerCase().replace(/[\s_]+/g, "-");
  const motion = CAMERA_MOTIONS.find(
    (m) => m.key === normalised || m.label.toLowerCase() === normalised
  );
  if (!motion) {
    // fall back to legacy internal catalog for backward compat
    const legacyPhrase = _legacyMotionCatalog[normalised];
    if (legacyPhrase) {
      const base = prompt.trimEnd().replace(/[.,;!?]+$/, "");
      return `${base}. Camera motion: ${legacyPhrase}.`;
    }
    console.error(`[catalog] Movimiento no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  const base = prompt.trimEnd().replace(/[.,;!?]+$/, "");
  return `${base}. Camera motion: ${motion.prompt}.`;
}

/**
 * Stack up to 3 camera motions into a compound prompt.
 * Subsequent motions are appended as additional clauses.
 */
export function stackMotions(
  basePrompt: string,
  keys: [string] | [string, string] | [string, string, string]
): string {
  let result = basePrompt;
  for (const k of keys) {
    result = applyMotion(result, k);
  }
  return result;
}

/** Append a COLOR_GRADES clause to a prompt. */
export function applyColorGrade(prompt: string, key: string): string {
  const clause = findRecord(COLOR_GRADES, key);
  if (!clause) {
    console.error(`[catalog] Color grade no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${clause}`;
}

/** Append a GENRES clause to a prompt. */
export function applyGenre(prompt: string, key: string): string {
  const clause = findRecord(GENRES, key);
  if (!clause) {
    console.error(`[catalog] Género no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${clause}`;
}

/** Append a CAMERA_BODIES clause to a prompt. */
export function applyCameraBody(prompt: string, key: string): string {
  const clause = findRecord(CAMERA_BODIES, key);
  if (!clause) {
    console.error(`[catalog] Cámara no encontrada: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${clause}`;
}

/** Append a SPEED_RAMPS clause to a prompt. */
export function applySpeedRamp(prompt: string, key: string): string {
  const clause = findRecord(SPEED_RAMPS, key);
  if (!clause) {
    console.error(`[catalog] Speed ramp no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${clause}`;
}

/** Append a LENSES clause to a prompt. */
export function applyLens(prompt: string, key: string): string {
  const clause = findRecord(LENSES, key);
  if (!clause) {
    console.error(`[catalog] Lente no encontrado: "${key}" — usando prompt sin modificar`);
    return prompt;
  }
  return `${prompt.trimEnd()}, ${clause}`;
}

/**
 * Build a full Cinema Studio prompt from structured per-shot params.
 * All params are optional; only provided ones are appended.
 */
export function buildCinemaPrompt(params: {
  base: string;
  cameraBody?: string;
  lens?: string;
  aperture?: string;
  genre?: string;
  colorGrade?: string;
  speedRamp?: string;
  motions?: [string] | [string, string] | [string, string, string];
}): string {
  let result = params.base;
  if (params.cameraBody) result = applyCameraBody(result, params.cameraBody);
  if (params.lens) result = applyLens(result, params.lens);
  if (params.aperture) {
    const clause = findRecord(APERTURES, params.aperture);
    if (clause) result = `${result.trimEnd()}, ${clause}`;
  }
  if (params.genre) result = applyGenre(result, params.genre);
  if (params.colorGrade) result = applyColorGrade(result, params.colorGrade);
  if (params.speedRamp) result = applySpeedRamp(result, params.speedRamp);
  if (params.motions) result = stackMotions(result, params.motions);
  return result;
}

// ─── LEGACY BACKWARD-COMPAT ───────────────────────────────────────────────────

/**
 * Legacy internal motion catalog (used by applyMotion for backward compat).
 * Original entries from the previous catalog.ts revision.
 * @internal
 */
const _legacyMotionCatalog: Record<string, string> = {
  "dolly-in":            "smooth, deliberate dolly-in gliding toward the subject, building intimacy as the background compresses",
  "dolly-out":           "graceful dolly-out pulling back to expand the world around the subject",
  "super-dolly-in":      "powerful super dolly-in, dramatically accelerating toward the subject",
  "super-dolly-out":     "sweeping super dolly-out retreating to reveal the full environment",
  "double-dolly":        "simultaneous dolly-in with a counter-zoom, the subject stays fixed while the world morphs",
  "dolly-zoom-in":       "vertigo dolly-zoom in — camera pushes forward while focal length widens, background stretches",
  "dolly-zoom-out":      "vertigo dolly-zoom out — camera retreats while focal length tightens, compressing the world",
  "crash-zoom-in":       "rapid crash zoom punching hard into the subject, kinetic and aggressive",
  "crash-zoom-out":      "explosive crash zoom pulling out, creating instant perspective shock",
  "rapid-zoom-in":       "fast snap zoom in on the subject, energetic and decisive",
  "rapid-zoom-out":      "fast snap zoom out from the subject, releasing tension abruptly",
  "yoyo-zoom":           "yoyo zoom cycling in and out in rhythm, dynamic and pulsing",
  "focus-change":        "a rack-focus pull shifting depth-of-field from foreground to the hero subject mid-shot",
  "crane-up":            "majestic crane-up rising from ground level to reveal the full scene above",
  "crane-down":          "deliberate crane-down descending toward the subject from height",
  "crane-over-head":     "crane arcing over the head of the subject, sweeping over the scene",
  "jib-up":              "jib rising smoothly, lifting the camera perspective upward",
  "jib-down":            "jib descending smoothly, lowering into the action",
  "pan-left":            "smooth horizontal pan to the left, tracking the environment",
  "pan-right":           "smooth horizontal pan to the right, leading the eye through the scene",
  "tilt-up":             "deliberate tilt upward, revealing height and scale",
  "tilt-down":           "measured tilt downward, anchoring the gaze to the subject",
  "truck-left":          "lateral truck to the left — camera body moves sideways, revealing the scene",
  "truck-right":         "lateral truck to the right — camera body moves sideways, unveiling space",
  "whip-pan":            "lightning-fast whip-pan snap to the side, pure kinetic momentum",
  "360-orbit":           "360-degree orbit around the subject, parallax revealing every angle",
  "3d-rotation":         "full 3D rotation around the subject, spinning in three-dimensional space",
  "arc-left":            "graceful arc panning left around the subject at medium radius",
  "arc-right":           "graceful arc panning right around the subject at medium radius",
  "fpv-drone":           "FPV drone diving and weaving through the scene at speed, immersive and visceral",
  "flying":              "aerial flying shot gliding over the landscape, majestic and expansive",
  "flying-cam-transition":"flying cam transition launching from one location and flying to another",
  "overhead":            "top-down overhead shot looking directly down on the subject",
  "handheld":            "handheld follow shot with natural shoulder movement and micro-corrections, documentary-real",
  "snorricam":           "snorricam body-mount shot, camera locked to the subject while the world rotates around them",
  "hero-cam":            "hero camera mounted low and close, framing the subject against an epic sky",
  "head-tracking":       "head-tracking lock on the subject's face, following every movement precisely",
  "object-pov":          "object POV shot from the perspective of an item, looking outward",
  "eyes-in":             "extreme close-up push into the eyes, intimate and psychologically direct",
  "mouth-in":            "close-up push toward the mouth, intense and visceral",
  "through-object-in":   "camera moves through a foreground object into the scene beyond",
  "through-object-out":  "camera moves through a foreground object, exiting the scene",
  "hyperlapse":          "hyperlapse tracking shot at accelerated time, environment flowing and time racing",
  "timelapse-glam":      "glamour timelapse with elegant slow motion moments freeze-framed in time",
  "timelapse-human":     "human timelapse showing the passage of time across a person's activity",
  "timelapse-landscape": "landscape timelapse with clouds racing and light transforming the scene",
  "low-shutter":         "low shutter speed motion blur, trailing light and soft ghosting",
  "bullet-time":         "360-degree bullet-time effect — time freezes while the camera orbits the subject",
  "slow-motion":         "cinematic slow motion at high frame rate, revealing detail and drama in every movement",
  "speed-up":            "time-compressed speed-up, environment and action racing past energetically",
  "impact":              "speed ramp that accelerates into a dramatic freeze on the peak impact frame",
  "ramp-up":             "speed ramp gradually accelerating from slow to fast, building relentless momentum",
  "push-to-glass":       "camera pushes into a glass or reflective surface, transitioning through the reflection",
  "anamorphic-flares":   "anamorphic cinematic lens flares sweeping across the frame as light sources move",
  "static":              "perfectly static locked-off shot, camera completely still, subject commanding the frame",
  "glam":                "glamour shot with soft focus halo, flattering light, and a slow revealing push-in",
  "incline":             "camera tilted on a dutch incline angle, adding tension and disorientation",
  "dutch-angle":         "dutch-angle tilt creating unease and visual tension in the frame",
  "lazy-susan":          "lazy susan rotation, subject on a turntable spinning smoothly into view",
  "levitation":          "levitation effect as the subject appears to float upward, weightless and ethereal",
  "fisheye":             "fisheye ultra-wide lens distortion, bending the world around the subject",
  "wiggle":              "subtle organic camera wiggle, alive and breathing with handheld energy",
  "robo-arm":            "precision robotic arm move, perfectly smooth mechanical arc at high speed",
  "car-grip":            "car-grip mount camera rolling with the vehicle, road rushing beneath",
  "car-chasing":         "car-chase pursuit shot following the vehicle at speed with kinetic urgency",
  "buckle-up":           "interior car shot as if bracing for acceleration, immersive and immediate",
  "road-rush":           "low road-level rush shot, asphalt blurring beneath at speed",
  "bts":                 "behind-the-scenes feel, slightly rough, pulling back curtain on the action",
  "action-run":          "fast run-alongside shot keeping pace with the subject in motion",
  "wan-animate":         "WAN 2.2 motion transfer — subtle natural movement breathing life into the still",
  "face-swap":           "seamless full-body character replacement preserving the original pose and performance",
  "reference-to-video":  "persona-consistent video generation matching the identity of the reference character",
};

/** Full list of available motion keys (for UI dropdowns and Opus vocab). Includes both new catalog and legacy keys. */
export function motionCatalogKeys(): string[] {
  const newKeys = CAMERA_MOTIONS.map((m) => m.key);
  const legacyKeys = Object.keys(_legacyMotionCatalog);
  return Array.from(new Set([...newKeys, ...legacyKeys]));
}

export default applyMotion;
