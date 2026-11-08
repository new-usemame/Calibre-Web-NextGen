### Fixed

- **With Proxy unknown requests to Kobo Store on, CWNG now fetches every Kobo Store call itself instead of redirecting the Kobo to Kobo's server.** A Kobo on firmware 6 stopped at the first redirected call, so its library sync never started and the Store and Libby pages stayed empty. The store's answer now comes back through CWNG, and a store that cannot be reached is answered with a gateway error instead of a server error. (#2797, #2807, reported by @Duckbilled and @IceSentry)
