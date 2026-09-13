# The cover designer

A book with no cover gets a generated one, and the "Design a cover" panel in the cover picker is
where the reader decides what it looks like. This note records what the generator can actually do,
measured against the Calibre that ships in this project's container image, so the next person to
touch it does not have to rediscover it.

Everything below was read out of Calibre 9.11 itself (`calibre.ebooks.covers`) inside that image.
Where this project deliberately differs from Calibre, it says so.

## Two renderers, one design

The preferred renderer is Calibre's own cover engine — the one behind "Generate cover" in
Calibre's metadata editor — driven through `calibre-debug -e scripts/calibre_generate_cover.py`,
because the application's interpreter has neither `calibre` nor Qt. Where Calibre is absent (unit
tests, a stripped image) a Pillow renderer honours the identical design object. The two are
deliberately not pixel-identical and nothing compares them; the API reports which one drew the
bytes.

A *design* is the whole vocabulary: arrangement, four colours, a typeface and size per text slot,
an alignment per slot, a short text template per slot, and the output size. Every field is
optional and the server fills and echoes back the rest.

## Arrangements

`all_styles()` returns exactly five: `Banner`, `Blocks`, `Half and Half`, `Ornamental`,
`The Cross`. All five are offered, under short ids (`banner`, `blocks`, `half`, `ornamental`,
`cross`). There is no sixth arrangement hiding anywhere; adding one means adding a style class to
Calibre.

## Colours

Calibre's generator takes four colours — `color1`, `color2`, `contrast_color1`, `contrast_color2`
— and each arrangement decides what to do with them. The designer names them for what the reader
sees instead: `background`, `band`, `title`, `author`. The mapping is per arrangement, and the
catalogue ships the sentence describing it beside each one, because the same slot genuinely does
different work in different arrangements:

| Arrangement | `band` | `author` |
| --- | --- | --- |
| Blocks | the block behind the authors | authors, on the band |
| Banner | the ribbon behind the title | authors, on the page |
| Ornamental | the frame | authors, and the ornaments |
| The Cross | the vertical bar and the title panel | authors, on the page |
| Half and half | the upper half | unused by this arrangement |

Two of those are worth knowing about. *Ornamental* paints its decoration in the author colour, so
that colour covers the whole page rather than one block. *Half and half* draws the authors in the
title colour and never uses the author slot at all. Both are Calibre's behaviour, not this
project's, and both are visible in the panel.

Calibre ships four colour themes, and all four are offered under their own names: Earth
(`e8d9ac` / `c7b07b` / `564628` / `382d1a`), Grass (`d8edb5` / `abc8a4` / `375d3b` / `183128`),
Water (`d3dcf2` / `829fe4` / `00448d` / `00305a`) and Silver (`e6f1f5` / `aab3b6` / `6e7476` /
`3b3e40`). The rest of the palettes are this project's. A reader can also set all four colours by
hand, in which case the design carries `scheme: null` and its own `#rrggbb` values.

The stock Calibre cover people recognise — vertical bar on the left, rounded panel behind the
title, blue — is **The Cross** with the **Water** theme.

## Sizes and fonts

Calibre's own defaults are a 1200x1600 cover (3:4) with title 120, subtitle 80 and footer 80, and
no font family set for any of the three. This project defaults to **1200x1800** (2:3) instead,
because every other cover in the application is 2:3 and a generated cover that did not match would
be obvious in a grid. Font sizes are design units — pixels on a 1200px-wide cover — and scale
with the output, so a preview is the same cover with fewer pixels rather than a different one.

The font catalogue is built by walking the machine's font directories plus Calibre's own bundled
`resources/fonts`, reading the family name out of each file's name table (the same string Qt
reports to Calibre, so a font offered here is one both renderers can use), and capping the walk so
a mounted share full of fonts cannot stall the first request. Three generic aliases —
`serif`, `sans`, `mono` — are always present whatever is installed, so a design travels between
machines.

A font has to be able to spell before it is offered. Font directories hold icon sheets and dingbat
faces beside the typefaces, and a title set in one comes out as a row of pictures or of empty
boxes. Two things the file says about itself decide it: OpenType family class 12 is "Symbolic",
and a font that maps only a handful of characters is an icon sheet rather than an alphabet.
Anything unreadable or merely unusual stays in the list — the rule errs towards offering a font,
never towards hiding one. On the container image three of the eighteen installed
families are held back — `D050000L` and `Standard Symbols PS`, which both declare class 12, and
`calibre Symbols`, which maps five characters — leaving fifteen typefaces beside the three
aliases.

Each entry also carries a serif/sans/mono hint, used for the CSS stack the panel falls back to if
a sample image does not load and for the face Pillow substitutes when it cannot open the family
itself; the Calibre renderer always asks for the family by name and never consults it. The hint is
read from the family name, because the files mostly do not say: of the fifteen typefaces on the
container image three declare an OpenType family class and six a PANOSE family type, and the URW
clones of the standard PostScript faces — `C059`, `P052`, `Z003` — declare neither, so a serif
face whose name does not contain a serif word is described as sans.

## Text, and why no Calibre template ever runs

Calibre's defaults are templates in Calibre's own template language: `<b>{title}` for the title, a
GPM one-liner for the subtitle, and a `program:` block for the footer. That language has
`program:` and `python:` modes, which is to say it is code, and a reader-supplied template string
is untrusted input.

So none of it runs. The designer's templates are a closed set of `{placeholders}` — title, series,
series index, authors, publisher, year, tags, language, rating — expanded by this project against
the book's own metadata, with the book's text escaped, and handed to Calibre as literal text. A
template that looks like Calibre code is drawn as the characters it contains. Calibre's own
`parse_text_formatting` then honours only `b`/`strong`/`i`/`em` and a literal `<br>`, which is the
only markup that reaches a cover.

Two smaller rules make templates behave the way a reader expects. A slot whose fields are all
empty prints nothing rather than a line of stray punctuation, and a bracketed run that ends up
empty is dropped with its brackets — `{publisher} ({year})` reads "Hodder" for a book with no year
and "Hodder (2015)" for one with a year.

## The rest of the boundary

A font is chosen by id from that closed catalogue and never by path, so no request can name a file.
Colours must match `#rrggbb`. Sizes are clamped. An unknown arrangement, scheme or font is a 400
with a sentence this project wrote; the renderer's own stderr carries server paths and is never
echoed to a reader. Designs a reader saves go through exactly the same validation as a preview, so
a preset cannot smuggle a design past it to be rendered later.

Arrangement thumbnails and lettering samples are real renders, cached on disk under the existing
cover preview cache (so they share its size budget and its sweeper) with the renderer that drew
them as part of the cache identity — a picture drawn by the Pillow fallback is not served once
Calibre is installed.

## Waiting for a render without stopping the server

The panel asks for every thumbnail at once, so the same uncached image is missed several times in
the same millisecond and only one of those misses should reach a renderer. The obvious way to
arrange that — a per-key lock held across the render — is wrong here, and wrong in a way that stops
the whole server rather than just the designer.

CWNG serves every request from a gevent greenlet on a single OS thread and deliberately does not
call `monkey.patch_all()`, while a catalogue render is dispatched to the gevent threadpool and
yields its greenlet while a worker thread runs it. A second request that then blocks on a native
`threading.Lock` stops the one thread the hub runs on: the first request's render finishes, no hub
is left to deliver the result, the first greenlet never resumes to release the lock, and the
process never answers anything again. Measured twice — a cold container wedged on the second
concurrent thumbnail, with `py-spy` showing its single thread parked in `Lock.acquire` inside
`cover_designer_cache.cached` and every pool worker idle with its render already done, and the same
stack out of `faulthandler` on a two-greenlet host reproduction.

`cover_designer_cache.single_flight_lock` is therefore a `gevent.lock.Semaphore` wherever gevent is
importable, so waiting parks the greenlet and leaves the hub free to deliver the very render the
waiter is waiting for, and a `threading.Semaphore` where it is not. The wait is also bounded: a
request that has waited `SINGLE_FLIGHT_WAIT_SECONDS` renders its own copy, so a renderer that never
returns costs one slow request instead of every later request for that image.

The cover-tile cache next door keeps its native lock, and is right to: it pads its image inline on
the request greenlet and never yields while holding it. The distinction that matters is not which
cache it is, but whether the work inside the lock can yield.
