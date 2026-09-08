### Fixed

- Setting up My Library for every account now keeps its original Undo snapshot before changing any account. Interrupted setup can resume safely after a restart, failed accounts can be retried without reseeding completed selections, and Undo also restores a partially completed setup. Concurrent administrator requests cannot replace or undo a running setup.
