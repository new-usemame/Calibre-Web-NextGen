# Stats and Activity language

Classic **Stats & Activity**, including the page opened from the New UI’s
Statistics link, follows the language selected in your account. Activity,
Library, API and System tabs use that language for headings, controls, chart
labels, empty states, demo controls and history column headings. Dates follow
the same locale. Russian, French and Dutch translations are included.

Changing the language changes presentation. Event grouping, totals, selected
users and date filters keep their meaning. “All Time” continues to include all
recorded activity. Demo mode uses the same translated labels as real data.

Book titles, usernames, filenames, paths, format names such as EPUB and PDF,
and endpoint paths remain as recorded. CSV exports retain their existing
column names, raw categories and date values so existing spreadsheets and
scripts can continue to use them.

Translators can find the messages in `messages.pot` and their locale’s
`cps/translations/<locale>/LC_MESSAGES/messages.po`. Existing contributed
translations are preserved. Other languages use English for new messages
until translations are supplied.

Conversion flow charts use distinct input and output stages while keeping the visible format labels and conversion counts. This supports reciprocal and same-format conversions without cycles in the Sankey renderer. Search demo totals, successful counts and the gauge now derive from one coherent sample; actual server metrics are unchanged. Existing empty French and Dutch page-title translations are filled alongside the new messages.
