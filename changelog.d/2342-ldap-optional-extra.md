### Changed

- **LDAP login is now an optional extra.** `python-ldap` and `Flask-SimpleLDAP` moved out of the core dependencies into the `ldap` extra, so a plain `pip install .` no longer needs a C compiler to build them on Windows. Install `.[ldap]` to enable LDAP login. The Docker image still installs the `ldap` extra, so LDAP login keeps working in the container. Reported in #2342.
