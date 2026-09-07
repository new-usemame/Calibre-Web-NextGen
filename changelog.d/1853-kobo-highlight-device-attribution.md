### Fixed

- **Highlights made on a Kobo are attributed to that Kobo again, instead of "Unknown device".** When the e-reader uploaded a highlight over its login session without repeating its hardware identifier, the server could not tell which device the highlight came from and stored it unattributed — so the Highlights page labelled it "Unknown device" and it never appeared under the reader that made it. The device's identity is now retained for the length of its login session, and is only ever resolved to an active Kobo belonging to the signed-in user.
