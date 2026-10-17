# NixOS module for Calibre-Web-NextGen.
#
# Runs the web app as a systemd service. Startup does what the container's
# cwa-init and cwa-auto-library oneshots do: seed app.db and the library on
# first run and record where the library is. The ingest watcher and the other
# s6 long-running services are not run yet (see #2094).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.calibre-web-nextgen;
  inherit (lib)
    escapeShellArg
    escapeShellArgs
    mkEnableOption
    mkIf
    mkOption
    mkPackageOption
    types
    ;

  app = "${cfg.package}/${cfg.package.appRoot}";
  appDb = "${cfg.configDir}/app.db";
in
{
  options.services.calibre-web-nextgen = {
    enable = mkEnableOption "Calibre-Web-NextGen";

    package = mkPackageOption pkgs "calibre-web-nextgen" { };

    user = mkOption {
      type = types.str;
      default = "calibre-web";
      description = "User account under which the service runs.";
    };

    group = mkOption {
      type = types.str;
      default = "calibre-web";
      description = "Group under which the service runs.";
    };

    listenAddress = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Address the web interface listens on.";
    };

    port = mkOption {
      type = types.port;
      default = 8083;
      description = "TCP port the web interface listens on.";
    };

    configDir = mkOption {
      type = types.path;
      default = "/var/lib/calibre-web-nextgen";
      description = "Directory holding app.db, cwa.db, logs and the other runtime state.";
    };

    libraryDir = mkOption {
      type = types.path;
      description = "Calibre library directory. It must exist; an empty library is created in it on first start.";
    };

    ingestDir = mkOption {
      type = types.path;
      description = "Folder new books are dropped into for import. It must exist.";
    };

    openFirewall = mkOption {
      type = types.bool;
      default = false;
      description = "Open the web interface port in the firewall.";
    };

    extraArgs = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Extra arguments for `cps`; see `cps --help`.";
    };

    environment = mkOption {
      type = types.attrsOf types.str;
      default = { };
      example = {
        TRUSTED_PROXY_COUNT = "1";
        CWA_PROCESSED_BOOKS_DIR = "/srv/books/processed";
      };
      description = "Extra environment variables for the service.";
    };
  };

  config = mkIf cfg.enable {
    # The library and ingest folders are often shared, so only the state dir is created here.
    systemd.tmpfiles.settings.calibre-web-nextgen."${cfg.configDir}".d = {
      inherit (cfg) user group;
      mode = "0750";
    };

    systemd.services.calibre-web-nextgen = {
      description = "Calibre-Web-NextGen";
      documentation = [ "https://github.com/new-usemame/Calibre-Web-NextGen/wiki" ];
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      environment = {
        HOME = cfg.configDir;
        CALIBRE_DBPATH = cfg.configDir;
        CWA_CALIBRE_LIBRARY_DIR = cfg.libraryDir;
        CWA_INGEST_FOLDER = cfg.ingestDir;
        CWA_TMP_CONVERSION_DIR = "${cfg.configDir}/.cwa_conversion_tmp";
        CACHE_DIR = "${cfg.configDir}/cache";
        # A plugin-free Calibre config, as the container's services use.
        CALIBRE_CONFIG_DIRECTORY = "${cfg.configDir}/.config/calibre-runtime";
        CWA_PORT_OVERRIDE = toString cfg.port;
      }
      // cfg.environment;

      path = [ cfg.package ];

      preStart = ''
        mkdir -p "$CALIBRE_CONFIG_DIRECTORY" "$CACHE_DIR" "$CWA_TMP_CONVERSION_DIR"

        # Seeds app.db and the library on first run and records the library location.
        cwa-python ${app}/scripts/auto_library.py

        # Google Drive setup expects this file to exist.
        if [ ! -f ${escapeShellArg cfg.configDir}/client_secrets.json ]; then
          echo '{}' > ${escapeShellArg cfg.configDir}/client_secrets.json
        fi

        # Point an unset kepubify path at the packaged binary.
        cwa-python - ${escapeShellArg appDb} ${pkgs.kepubify}/bin/kepubify <<'EOF'
        import sqlite3, sys
        with sqlite3.connect(sys.argv[1]) as db:
            db.execute(
                "UPDATE settings SET config_kepubifypath = ? "
                "WHERE config_kepubifypath IS NULL OR config_kepubifypath = '''",
                (sys.argv[2],),
            )
        EOF
      '';

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.configDir;
        ExecStart = escapeShellArgs (
          [
            "${cfg.package}/bin/cps"
            "-p"
            appDb
            "-i"
            cfg.listenAddress
          ]
          ++ cfg.extraArgs
        );
        Restart = "on-failure";
        RestartSec = "10s";

        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        LockPersonality = true;
        ReadWritePaths = [
          cfg.configDir
          cfg.libraryDir
          cfg.ingestDir
        ];
      };
    };

    users.users = mkIf (cfg.user == "calibre-web") {
      calibre-web = {
        isSystemUser = true;
        inherit (cfg) group;
        home = cfg.configDir;
        description = "Calibre-Web-NextGen service account";
      };
    };

    users.groups = mkIf (cfg.group == "calibre-web") { calibre-web = { }; };

    networking.firewall.allowedTCPPorts = mkIf cfg.openFirewall [ cfg.port ];
  };
}
