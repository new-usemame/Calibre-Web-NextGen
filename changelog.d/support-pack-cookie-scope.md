### Fixed

- **The support pack's download-started cookie is only sent when a download really starts, and stays within the app's own path.** It used to be set on any request that carried a token, including a `HEAD` request (which downloads nothing), and always for the whole site. It is now set only on a `GET` that returns the attachment, and is scoped to the app's mount path, so two instances on one host under different paths no longer share it (#2728).
