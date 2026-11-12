### Fixed

- **A save from a KOSync app such as JustRead now moves your Kobo to the same place, not only to the same percentage.** Apps that don't send CWNG's device header only moved the Kobo's percentage, so the book showed the new percentage and opened where the Kobo last was. When the app's save is accepted and its place converts exactly into the KEPUB your Kobo holds, the Kobo now gets that place. These saves still only move forward, as before (#2783, thanks @lukerajah).
