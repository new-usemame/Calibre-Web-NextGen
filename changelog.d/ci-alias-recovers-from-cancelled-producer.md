### Fixed

- **Back-to-back main updates no longer leave the release train blocked by a
  cancelled image build.** When an unchanged commit cannot alias its required
  ancestor image because that producer was cancelled or failed, CI now builds
  the exact commit and publishes its immutable image tag automatically.
