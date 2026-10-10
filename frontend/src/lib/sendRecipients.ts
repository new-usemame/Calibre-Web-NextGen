/* The send panel's recipient field is the one record of who receives a book
 * (#2296). An admin's "other users' eReaders" checkboxes edit that field rather
 * than keeping a second list, so what the field shows is exactly what is sent. */

function addresses(field: string): string[] {
  return field.split(',').map((part) => part.trim()).filter(Boolean);
}

const key = (address: string) => address.toLowerCase();

/** True when every one of `emails` is already in the recipient field. */
export function hasRecipients(field: string, emails: string[]): boolean {
  const present = new Set(addresses(field).map(key));
  return emails.length > 0 && emails.every((email) => present.has(key(email)));
}

/** The field with `emails` added (once each) or removed, keeping the rest in order. */
export function toggleRecipients(field: string, emails: string[], on: boolean): string {
  const current = addresses(field);
  const toggled = new Set(emails.map(key));
  const kept = current.filter((address) => !toggled.has(key(address)));
  return (on ? [...kept, ...emails] : kept).join(', ');
}
