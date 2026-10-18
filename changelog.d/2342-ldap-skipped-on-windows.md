### Fixed

- **A checkout installs on Windows without a C compiler.** `pip install .` failed on Windows because python-ldap has no Windows wheels and has to be built from source. python-ldap and Flask-SimpleLDAP are now skipped on Windows, where the app already runs with LDAP login turned off; Linux, macOS and the Docker image install them as before, so LDAP login keeps working there. Reported by @Rol3333 in #2342, with the platform-marker approach; tests from @theluckystrike's #2583.
