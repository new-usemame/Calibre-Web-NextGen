### Changed

- **A failed KOReader plugin publish is now announced instead of sitting unseen.** When publishing the plugin to its own repository fails after a release, a message goes to the project's release channel with the tag and a link to the run, so devices are not left on an old plugin for several releases. Nothing changes when the publish succeeds or the plugin did not change (#1688).
