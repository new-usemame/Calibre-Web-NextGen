# Boots the module, logs in as the default admin and checks the library was set up.
{ testers, module }:

testers.runNixOSTest {
  name = "calibre-web-nextgen";

  nodes.machine = {
    imports = [ module ];
    virtualisation.memorySize = 2048;
    services.calibre-web-nextgen = {
      enable = true;
      libraryDir = "/srv/library";
      ingestDir = "/srv/ingest";
    };
    systemd.tmpfiles.rules = [
      "d /srv/library 0750 calibre-web calibre-web -"
      "d /srv/ingest 0750 calibre-web calibre-web -"
    ];
  };

  testScript = ''
    import re

    def login():
        page = machine.succeed("curl -sf -c /tmp/jar http://127.0.0.1:8083/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        assert match, "login page has no CSRF token"
        token = match.group(1)
        machine.succeed(
            "curl -sf -b /tmp/jar -c /tmp/jar -o /dev/null "
            f"--data-urlencode csrf_token={token} "
            "-d username=admin -d password=admin123 http://127.0.0.1:8083/login"
        )

    machine.wait_for_unit("calibre-web-nextgen.service")
    machine.wait_for_open_port(8083)
    machine.succeed("test -f /srv/library/metadata.db")

    login()
    # A configured library serves the book list instead of redirecting to setup.
    machine.succeed("curl -sf -b /tmp/jar -o /dev/null -w '%{http_code}' http://127.0.0.1:8083/ | grep -qx 200")
    machine.succeed("curl -sf -o /dev/null http://127.0.0.1:8083/app/")

    machine.succeed("systemctl restart calibre-web-nextgen.service")
    machine.wait_for_open_port(8083)
    login()
    machine.succeed("curl -sf -b /tmp/jar -o /dev/null -w '%{http_code}' http://127.0.0.1:8083/ | grep -qx 200")
  '';
}
