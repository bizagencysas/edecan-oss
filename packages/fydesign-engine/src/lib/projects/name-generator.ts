// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Name Generator — Extract meaningful project name from user prompt            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

const STOP_WORDS = new Set([
  'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
  'of', 'with', 'from', 'by', 'as', 'is', 'it', 'its', 'be', 'has', 'have',
  'create', 'make', 'build', 'design', 'generate', 'give', 'show', 'need',
  'want', 'please', 'can', 'you', 'me', 'my', 'i', 'we', 'our',
]);

const MODE_SUFFIX: Record<string, string> = {
  mockup: 'Mockups',
  carousel: 'Carousel',
  ad: 'Ads',
  post: 'Social',
  landing: 'Landing',
  email: 'Email',
  general: 'Design',
};

export function generateProjectName(prompt: string, mode: string): string {
  // Extract meaningful words from the prompt, skipping stop words
  const words = prompt
    .replace(/[^\w\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 1 && !STOP_WORDS.has(w.toLowerCase()));

  // Take first 3-4 keywords
  const keywords = words.slice(0, 4);

  // If we found brand/product names, use them
  if (keywords.length >= 2) {
    const name = keywords
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
    const suffix = MODE_SUFFIX[mode] || 'Design';
    // Only append suffix if name doesn't already contain it
    return name.includes(suffix) ? name : `${name} ${suffix}`;
  }

  // Fallback: mode + timestamp
  const suffix = MODE_SUFFIX[mode] || 'Design';
  return `${suffix} ${new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
}
