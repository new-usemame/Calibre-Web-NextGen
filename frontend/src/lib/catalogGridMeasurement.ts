export interface CatalogGridMeasurement {
  gridTemplateColumns: string;
  gridWidth: number;
  minColumnWidth?: number;
  columnGap: number;
}

const RESOLVED_LENGTH = /^(?:0|[1-9]\d*)(?:\.\d+)?px$/;

/**
 * Return the resolved catalog track count only when the grid has a usable
 * content box. A hidden/settling grid can report one genuine 140px track while
 * its own width is zero or near zero; accepting that track is what can strand
 * virtualization at one card per row.
 */
export function measureCatalogColumnCount({
  gridTemplateColumns,
  gridWidth,
  minColumnWidth,
  columnGap,
}: CatalogGridMeasurement): number | null {
  if (!Number.isFinite(gridWidth)
    || gridWidth <= 0) return null;

  const tracks = gridTemplateColumns.trim();
  if (!tracks || tracks === 'none') return null;

  const resolvedTracks = tracks.split(/\s+/);
  if (!resolvedTracks.every((track) => RESOLVED_LENGTH.test(track))) return null;

  // Mobile replaces auto-fill with fixed repeat(n) tracks and pins the shared
  // minimum to zero. An absent CSS minimum likewise leaves no auto-fill
  // geometry to validate, so preserve the resolved-track behavior in both
  // cases. Other invalid minima cannot describe either layout safely.
  if (minColumnWidth === undefined || Number.isNaN(minColumnWidth) || minColumnWidth === 0) {
    return resolvedTracks.length;
  }
  if (!Number.isFinite(minColumnWidth)
    || minColumnWidth < 0
    || !Number.isFinite(columnGap)
    || columnGap < 0
    || gridWidth < minColumnWidth) return null;

  // auto-fill can fit floor((W + gap) / (min + gap)) tracks. Keep the exact
  // integer floor: subtracting a width tolerance would lower it immediately
  // above every track threshold and admit the undercount this gate screens out.
  const requiredTrackCount = Math.max(1, Math.floor(
    (gridWidth + columnGap) / (minColumnWidth + columnGap),
  ));
  return resolvedTracks.length < requiredTrackCount ? null : resolvedTracks.length;
}
