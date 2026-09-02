### Fixed

- **Back-to-back main updates no longer leave the release train blocked by a
  cancelled image build.** When an unchanged commit cannot alias its required
  ancestor image because that producer was cancelled or failed, CI now builds
  the exact commit and publishes its immutable image tag automatically. Because
  that is a real build rather than a tag copy, it also advances `:dev`, so a
  dev-channel deployment restarts on a commit that would previously have been
  skipped. The image content is unchanged — such a commit touches nothing the
  image is built from.
