### Fixed

- **OPDS reader apps get small covers they can keep.** Every OPDS cover address, including the thumbnail one a reader app uses for its browse grid, sent the full-size cover, and none of them could be cached, so each visit to a shelf downloaded every full cover again. The thumbnail addresses (`/opds/thumb_240_240`, `/opds/cover_240_240`, `/opds/cover_90_90`) now send the generated thumbnail as JPEG, and the cover and thumbnail links in the feed carry a version, so an app can keep a cover until the book's cover changes. `/opds/cover` still sends the original. (#2845)
