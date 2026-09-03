### Fixed

- **Image-neutral maintenance commits no longer trigger needless dev container
  rebuilds.** CI now derives image relevance from the Docker build-context
  policy while retaining explicit checks for out-of-context build inputs.
