# Your library on a Kindle (or any KOReader e-reader)

With KOReader and the CWNG plugin, the e-reader opens on your CWNG library: the covers
of your books, ready to tap. A book downloads when you open it, and your place in it,
whether you've finished it, and your highlights go back to CWNG by themselves.

This page assumes KOReader is already installed on the e-reader. CWNG doesn't change
anything on the e-reader's own software.

## What you'll see

- **The library home**, with five tabs: **Reading**, **Recent**, **Shelves**,
  **Authors** and **Series**.
- **Search** (the magnifying glass, top right): title, author or series. Accents don't
  matter; "bronte" finds Brontë.
- **Tap a cover** to read. A book that isn't on the e-reader yet downloads first (a few
  seconds on Wi-Fi).
- **Hold a cover** to mark the book Reading, On hold or Finished, or to see its details.
- **The menu** (☰, top left): **Sync now**, **Show only downloaded books**, **Browse
  files**, and **KOReader menu** for Wi-Fi, settings and everything else KOReader does.

## Connect the e-reader (once)

You need CWNG with KOReader sync switched on (your administrator does this), and the
e-reader on a Wi-Fi network that can reach your CWNG address.

In CWNG, open **E-readers**, then **Pair a Kobo or KOReader**. There are two ways.

### Ready-made plugin (USB): nothing to type on the e-reader

1. Click **Download ready-made plugin**. Check the address under *Your e-reader will
   connect to*: the e-reader must be able to reach it (`localhost` won't work). Unzip the
   download.
2. On a Kindle, exit KOReader before you plug it in: ☰ ▸ **KOReader menu** ▸ ☰ ▸
   **Exit** ▸ **Exit**. A Kindle and its USB connection don't mix while KOReader is open.
3. Connect the e-reader with its USB cable. Windows shows it in File Explorer. On a Mac,
   Kindles from 2024 on appear only in an MTP app, such as OpenMTP or Amazon's Kindle USB
   File Manager (it comes with Send to Kindle for Mac); older Kindles and Kobos appear as
   a drive.
4. Copy the `cwngsync.koplugin` folder into `koreader/plugins` on the e-reader (on a Kobo,
   `.adds/koreader/plugins`), replacing any older copy.
5. Eject the e-reader and start KOReader with Wi-Fi on. It says *Connected to* your CWNG
   address, and your library appears in a moment.
6. Delete the zip from your computer. It holds a password for your account.

### Pair with a code: for an e-reader that already has the plugin

1. On the e-reader, open **Tools ▸ CWNG library ▸ Connect this device** and choose
   **Connect with a code (easiest)**. A new install asks this by itself. (Tools is the
   wrench in KOReader's menu; CWNG library is near the end of its list.)
2. Type the address you open CWNG at in a browser, such as `books.example.com` or
   `192.168.1.20:8083`, and tap **Continue**.
3. The e-reader shows a code like `K7M4-QX2P`, and a QR code.
4. In CWNG choose **Pair with a code**. Type the code, or scan the QR code with your
   phone. Check that the device name is yours, then **Approve**.

If you'd rather type, the e-reader also offers **Sign in with username and password**.

Each e-reader gets its own app password, which you can revoke on your account page.

## Which books appear

The same choice your Kobo uses. By default that's your whole library. To carry only some
shelves, open each shelf and choose **Enable e-reader sync**, then on your account page
turn on **Sync only selected shelves to e-readers**.

A book is only a cover until you open it, so a big library takes little space. Books you
send to the e-reader appear too, even if they aren't on those shelves.

## Reading on more than one device

- **Close the book, or let the e-reader sleep.** Your place, finished or not, and your
  highlights are sent to CWNG. There's nothing to press.
- **Open a book** and it picks up where you left off on another device or in the web
  reader.
- **Highlights and notes made on another KOReader e-reader** appear in the book when you
  open it. Delete one here and it stays deleted.
- **No Wi-Fi?** Nothing is lost. It's sent the next time the e-reader is online. The
  plugin never turns Wi-Fi on by itself for this.
- **Opening a book and closing it without turning a page sends nothing**, so glancing at
  a book never moves your place on your other devices.

## Sending a book from CWNG

On the book's page in CWNG, open the menu, choose **Send to device**, pick the e-reader,
and press **Send to device**. The book arrives the next time the e-reader wakes up, or
straight away if you tap ☰ ▸ **Sync now**. The home says *New on this device* and shows
the book first under **Recent**.

## Tips for big libraries

- **Authors** are listed by surname. Tap the page number at the bottom ("Page 1 of 26")
  and use **Go to letter** to jump.
- **Show only downloaded books** (☰) shows only what you can read without Wi-Fi.
- Prefer KOReader's own file browser? Use ☰ ▸ **Browse files**; the ⌂ button brings the
  library back. To always start in the file browser, turn off **Tools ▸ CWNG library ▸
  Advanced ▸ Start on the library home**.

## If something goes wrong

- **"Could not download"**: check that Wi-Fi is on, then tap the book again.
- **A new book or shelf hasn't appeared**: tap ☰ ▸ **Sync now**.
- **Connected to the wrong account**: choose **Tools ▸ CWNG library ▸ Disconnect this
  device**, then connect again.
