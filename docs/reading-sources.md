# Reading sources and Storyteller

The EPUB reader can show the last position reported by each registered device,
an account-level saved position whose source was not recorded, and an optional
Storyteller position. That unattributed position can be updated by more than
one sync client. Positions stay separate: CWNG does not replace several
device locations with a single percentage.

Choosing a source previews it in the web reader. It does not edit the source
device. Choosing **Read from here**, or turning a page from the preview, saves a
new Browser position while leaving the inspected device and shared device
carriers unchanged.

## Connect Storyteller

Storyteller connections are assigned to individual CWNG user IDs. There is no
server-wide token fallback because that could expose one household member's
reading activity to another account.

Mount a JSON file containing the assignments and set
`CWNG_STORYTELLER_CONNECTIONS_FILE` to its path inside the CWNG container:

```json
{
  "107": {
    "url": "https://storyteller.example.test",
    "token": "replace-with-that-users-token"
  }
}
```

The server administrator controls this file; readers cannot supply or change a
URL through the API. Keep the file readable only by the container account and
out of source control. `CWNG_STORYTELLER_CONNECTIONS_JSON` accepts the same JSON
for secret-capable deployment systems, although a mounted secret file is less
likely to appear in environment diagnostics.

CWNG calls Storyteller with the assigned token and never includes the token or
URL in its API response. Redirects are disabled. Before CWNG offers an exact
Storyteller chapter anchor, it compares Storyteller's ebook SHA-256 with the
EPUB opened by CWNG and verifies that the reported fragment is a real element
ID in that archive. Readium navigators can report private synthetic fragments
that another renderer cannot resolve. For an identical archive with one of
those fragments, CWNG uses the reported chapter href and chapter-local
progression to choose an approximate epub.js location. A missing or different
hash leaves only an approximate whole-book percentage target.
