# Personal book ratings

Every signed-in reader can save a private book score from half a star to five stars in Classic or New UI. Choose **Unrated** and save to clear it. You do not need permission to edit book metadata. A score belongs to the signed-in account and the visible book; it does not change the Calibre library score or another reader’s rating.

**Your rating** is the default on the book page and for signed-in readers’ Top Rated list, rating categories, advanced rating ranges, and highest/lowest rating sorts. Top Rated keeps its existing five-star threshold. Both sort directions keep unrated books at the end. A book with a Calibre score and no personal score remains unrated for you.

The book page also shows a clearly labelled **Library rating** when one exists. Turn **Show library rating** off in your account to hide that score. This does not delete or alter the Calibre metadata. Book metadata editing, saved smart-shelf rating rules, metadata exports, and existing external Calibre-rating URLs keep their library-score meaning; the smart-shelf rule is labelled **Library rating**. Guest browsing keeps the library score because there is no signed-in account to own a private rating.

## Optional household average

Sharing starts off. Turn **Share my ratings in the household average** on in your account to include your positive ratings in the average visible to other signed-in readers. The interface shows the average without individual names or a list of scores. With one contributor, the average equals that contributor’s score, so opt in only if you are comfortable sharing that information. Turning sharing off removes your scores from subsequent averages. Clearing a score removes it from the average too. Anonymous visitors do not receive household averages.

## API compatibility

`GET /api/v1/books/{id}/rating` returns your `personal_rating` and an optional `household_rating`, in integer half-star units from 1 to 10 (averages may be fractional). An absent score is `null`. `PUT` accepts `{ "rating": 0..10 }`; zero or `DELETE` clears your score. Every request uses the current account, normal book visibility and CSRF protection on writes. Client-supplied user IDs cannot choose a different owner.

Book list/detail responses add `personal_rating` and `household_rating`; their existing `rating` field continues to mean the Calibre library score. `/api/v1/personal-ratings` and `personal_rating=0..10` provide private rating categories and filters, with zero meaning unrated. The existing `/api/v1/ratings` category IDs and `rating=` filter keep their Calibre meaning for existing integrations. Private projections are marked `private, no-store`.

Scores live in app.db. When duplicate books are consolidated, each account’s most recent rating or clear is preserved; a clear is retained as a timestamped choice so an older score cannot reappear. Removing a book or account purges its rating records.

Consent updates in the New UI stay bound to the initiating account and session generation. A delayed response or queued toggle from an earlier account cannot replace the current identity or preferences. The preferences API accepts an optional `expected_user_id`; a mismatch rejects the entire update before any preference is changed. Existing clients may omit it.
