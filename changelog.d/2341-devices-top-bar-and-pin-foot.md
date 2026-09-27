### Fixed

- **The top bar no longer scrolls away on the Devices pages.** On
  Account → Devices and browsers (and each device's page) the top bar scrolled
  off with the page while the sidebar stayed put, leaving an empty band above
  it. The bar now stays at the top like everywhere else, and keyboard focus
  still stops below it on long device lists. (#2341)
- **"Pin sidebar" moved to the foot of the sidebar.** In the collapsed desktop
  sidebar it left an empty slot above Library; it now sits at the bottom and
  stays in reach while a long sidebar scrolls. (#2341)
- **Paging a device's library keeps your place.** Next and Previous in a
  device's library list used to drop keyboard focus and leave the page
  scrolled to wherever the pager had been. Now you land on the new page's
  status line, at the top of the device card. (#2341)
