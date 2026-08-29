- **Book cards no longer show Read, edit and remove buttons at rest on touch
  devices.** They previously stayed pinned visible on any coarse-pointer or
  hover-less device, so an iPad library showed three controls on every cover at
  once. They are now revealed the same way as on a mouse — by hover, focus or
  focus-within — and on a touch device a hidden control is not tappable either, so a tap on a cover corner can no longer fire remove-from-shelf blind. They remain in layout and in
  the accessibility tree with their 44px touch targets intact, so keyboard and
  screen-reader users still reach them. Every action stays available on the
  book's own page, and bulk removal stays in Select mode.
