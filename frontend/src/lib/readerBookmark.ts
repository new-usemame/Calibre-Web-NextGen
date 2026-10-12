/** One wire payload for debounced saves and the teardown keepalive. */
export function readerBookmarkBody(
  bookmark: string,
  percentage: number | null | undefined,
  shareWithDevices: boolean,
): {
  format: 'epub'; bookmark: string; percentage?: number; share_with_devices: boolean;
} {
  return {
    format: 'epub',
    bookmark,
    ...(percentage != null ? { percentage } : {}),
    share_with_devices: shareWithDevices,
  };
}
