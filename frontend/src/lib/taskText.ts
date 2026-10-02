// The worker queue (/api/v1/info tasks, shared with the classic /tasks table)
// sends taskMessage and user as HTML: a link to the book in some messages,
// markupsafe-escaped user text everywhere else. React renders strings as text,
// so show what the classic table shows: drop the tags, decode the entities.
const ENTITIES: Record<string, string> = {
  '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#34;': '"', '&#39;': "'", '&amp;': '&',
};

export function taskText(html: string | null | undefined): string {
  if (!html) return '';
  return html
    .replace(/<[^>]*>/g, '')
    .replace(/&(?:lt|gt|quot|amp|#34|#39);/g, (entity) => ENTITIES[entity]);
}
