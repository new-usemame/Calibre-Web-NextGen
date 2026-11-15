# Cover resolution sources

The cover picker can ask Amazon's image CDN for a high-resolution image using identifiers already stored on the book, even when the Amazon metadata provider is disabled. The `CWA_COVER_BOOST_AMAZON_CDN=0` kill switch disables this path for both the picker candidate and metadata-result upgrades.

For each normalized Amazon key, the server makes at most two bounded HEAD checks using the existing per-request timeout: it checks Amazon's `MAIN._SCRM_` image first, the same image variant on the existing host used by Calibre's Kindle High-res Covers plugin. If Amazon does not return a sufficiently large JPEG for that variant, the server checks the previous bounded `SL2000` URL as a fallback. Unknown keys and Amazon's tiny placeholder GIF are not offered. ISBN-derived keys keep their existing priority over stored ASIN identifiers, so edition matching does not change.

The server only probes a bounded number of metadata results per request and uses the configured cover-boost timeout. Choosing a result downloads its exact candidate URL through the existing validated cover-save path; JPEG bytes are retained, while supported non-JPEG formats are normalized to JPEG. Amazon-provider enablement and other metadata-provider settings continue to control provider searches independently of this direct image lookup.
