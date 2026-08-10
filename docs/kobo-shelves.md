# Choosing which books go to your Kobo

Setting up Kobo sync connects the device to your library. It doesn't decide *what*
lands on it. By default your Kobo syncs your whole library; to send it particular
shelves instead — and have them appear as **collections** on the device — you need
a couple of settings that live in different places.

Which ones depends on whether the shelf is an ordinary shelf or a smart shelf, and
the two behave differently enough that it's worth knowing which you have.

## Smart shelves and Magic Shelves are the same thing

The new interface calls them **Smart shelves**. The classic interface calls them
**Magic Shelves ✨**, and so does the admin setting below. The API calls them
`magicshelf`.

Same feature, three names. If you are looking at a *Smart* shelf and hunting for a
setting that says *Magic*, you are in the right place.

## The settings

**1. Kobo sync itself — required for everything.**

Admin → **Edit Basic Configuration** → **Enable Kobo sync**. This is the one you
turned on when you set the device up.

**2. "Sync Magic Shelves to Kobo" — required for smart shelves, and it ships OFF.**

Ordinary shelves do not need this one. Smart shelves do, because they are computed
from a rule rather than a fixed list of books. **It is disabled by default**, which
is the single most common reason smart shelves don't reach a device.

It lives on the NextGen settings page, at **`/cwa-settings`**. In the classic admin
panel the button is labelled **NextGen Settings**; in the new UI the admin page lists
it as **CWA settings (ingest/convert)**. Its own note says the rest: *"You must also
enable Kobo Sync in Basic Configuration."*

**3. Sync only selected shelves — what stops your whole library going across.**

A per-user setting, on your own account page or Admin → Users → your user. The label
depends on where you find it, which does not help:

| Where | Label |
|---|---|
| New UI, account page | Sync only selected shelves to Kobo |
| Classic, user edit page | Sync only books in selected shelves with Kobo |
| Classic, admin user table | Sync selected Shelves with Kobo |

All three are the same setting. On the classic pages it is hidden entirely until
Kobo sync (setting 1) is on; on the new UI account page it is always shown, and
simply has no effect until then.

## What each setting actually changes

This is the part that isn't obvious, and the two shelf kinds differ:

**Ordinary shelves** are governed by setting 3. With it **off**, your device receives
your whole library, and *every* shelf you own goes across as a collection — marking
one makes no difference. With it **on**, only the shelves you marked are sent, and
books that aren't on one leave the device. They stay in your library here.

**Smart shelves** are governed by setting 2, not setting 3. A marked smart shelf is
sent whenever setting 2 is on, whatever setting 3 says — and an *unmarked* smart
shelf is actively removed from the device rather than merely left alone.

So: if you marked one ordinary shelf and got all of them, setting 3 is why. If a
smart shelf never appears at all, setting 2 is why.

## Marking a shelf

Open the shelf and use the **Enable Kobo sync** button on the shelf page. It reads
**Kobo sync on** once set.

- **Ordinary shelves**: the button has been there since the new UI arrived.
- **Smart shelves**: the same button, added in **v4.1.19**. On an older build the
  control exists only in the classic interface — you would find it when editing the
  shelf in classic, and not find it at all in the new UI. If that is what you are
  seeing, `docker pull ghcr.io/new-usemame/calibre-web-nextgen:latest` and restart.

Then sync the Kobo.

## If the button isn't there

**On a smart shelf**, in order:

1. **Setting 2** — it ships off, and when it is off the button is *hidden* rather
   than greyed out, so an unset admin setting looks exactly like a missing feature.
   This is nearly always the answer.
2. **Is it your shelf?** Smart-shelf controls appear only for the owner. A smart
   shelf can be public and others can view it, but no permission restores the button
   for someone who doesn't own it.
3. **Setting 1.**

**On an ordinary shelf**, in order:

1. **Can you edit it?** The controls follow edit rights: your own shelves always, a
   public shelf you didn't create only with the **Edit public shelves** permission,
   and someone else's private shelf never.
2. **Setting 1.**

The classic interface handles the smart-shelf case differently — on the shelf's edit
page it shows the checkbox greyed out with a note naming the setting to turn on. The
new UI hides it instead. That difference is a known rough edge, not something you
configured wrong.

## If the shelf syncs but the books don't appear

Confirm setting 3 is on for **the account whose token the device uses**. It is
per-user, so an admin turning it on for themselves changes nothing for anyone else.

To check the device is actually reaching your server, watch the log while it syncs:

```bash
docker logs -f calibre-web-nextgen 2>&1 | grep -i "Kobo library sync request"
```

A line per sync means the device found you. (Use your own container name if you
changed it — `calibre-web-nextgen` is what the shipped compose file sets. Note that
HTTP access logging is off by default and writes to a file rather than the container
log, so grepping for the request *path* shows nothing even when sync is working.)

If the device is talking to the server and the collection still doesn't appear, that
is worth reporting — open an issue at
<https://github.com/new-usemame/Calibre-Web-NextGen/issues> or ask on
[Discord](https://discord.gg/B8NXZmcp32), and say which of the three settings are on.
