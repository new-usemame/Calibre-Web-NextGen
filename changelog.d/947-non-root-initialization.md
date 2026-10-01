### Fixed

- **Non-root container startup:** Fresh containers started with an explicit non-root UID now complete setup for the default library mount. Runtime directories use the running user, and the image provides its intended ImageMagick policy before startup.
