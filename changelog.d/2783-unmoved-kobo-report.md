### Fixed

- **A Kobo that wakes up with a book open no longer undoes a newer place from another reader.** On waking, a Kobo reports the place it closed on with the current time, before it syncs. That fresh time used to beat a place you had just saved in the web reader or KOReader, so the Kobo's old place replaced it everywhere. With Share my place on, a Kobo report that repeats the place that Kobo last sent now leaves the newer place alone, and the Kobo's next sync offers it that place. Reading on the Kobo, forward or back, still wins as before (#2783, thanks @lukerajah).
