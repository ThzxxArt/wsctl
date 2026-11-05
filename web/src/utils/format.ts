/** Locale-aware formatting for values that cross the server boundary. */

export function formatDateTime(epochSeconds: number | null | undefined, locale: string): string {
  if (!epochSeconds) return "-";
  try {
    return new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(epochSeconds * 1000));
  } catch {
    return new Date(epochSeconds * 1000).toLocaleString();
  }
}
