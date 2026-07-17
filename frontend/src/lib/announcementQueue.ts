export interface ChannelledAnnouncement {
  priority: number;
  channel?: string;
}

/** Keep channel-less announcements unchanged, but allow only the last-declared
 * entry in each named channel to participate before applying queue priority. */
export function prioritizeAnnouncements<T extends ChannelledAnnouncement>(
  announcements: readonly T[],
): T[] {
  const newestIndexByChannel = new Map<string, number>();

  announcements.forEach((announcement, index) => {
    if (announcement.channel) newestIndexByChannel.set(announcement.channel, index);
  });

  return announcements
    .filter((announcement, index) => (
      !announcement.channel || newestIndexByChannel.get(announcement.channel) === index
    ))
    .sort((left, right) => right.priority - left.priority);
}
