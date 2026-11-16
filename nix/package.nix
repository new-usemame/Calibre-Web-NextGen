# Calibre-Web-NextGen as a Nix package.
#
# The app runs from its own source tree, exactly as in the Docker image: the
# tree is installed read-only under share/, and `cps` starts cps.py with a
# Python environment holding the dependencies. Writable state goes wherever
# CALIBRE_DBPATH and the CWA_* path variables point (see README, "Runtime path
# overrides for packagers").
{
  lib,
  python313,
  buildNpmPackage,
  importNpmLock,
  fetchPypi,
  makeWrapper,
  gettext,
  stdenvNoCC,
  calibre,
  kepubify,
}:

let
  version = lib.strings.trim (builtins.readFile ../VERSION);

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../cps
      ../scripts
      ../cps.py
      ../dirs.json
      ../VERSION
      ../LICENSE
    ];
  };

  python = python313.override {
    self = python;
    packageOverrides = final: prev: {
      # The app needs Flask-Limiter below 3.13 (it calls limiter.check(), gone in 4.x).
      flask-limiter = final.buildPythonPackage rec {
        pname = "flask_limiter";
        version = "3.12";
        format = "wheel";
        src = fetchPypi {
          inherit pname version format;
          dist = "py3";
          python = "py3";
          hash = "sha256-uUyelYTfmCCVQmhpR89kfx7eNe1+SrVkk0orue1GsUM=";
        };
        dependencies = with final; [
          flask
          limits
          ordered-set
          rich
          typing-extensions
        ];
        # Its rich<14 pin is older than nixpkgs; the APIs it uses are unchanged.
        dontCheckRuntimeDeps = true;
        pythonImportsCheck = [ "flask_limiter" ];
      };
    };
  };

  pythonEnv = python.withPackages (
    ps: with ps; [
      apscheduler
      babel
      beautifulsoup4
      bleach
      certifi
      chardet
      charset-normalizer
      comicapi
      cryptography
      curl-cffi
      faust-cchardet
      flask
      flask-babel
      flask-dance
      flask-httpauth
      flask-limiter
      flask-principal
      flask-simpleldap
      flask-wtf
      gevent
      google-api-python-client
      google-auth-oauthlib
      greenlet
      html2text
      html5lib
      httplib2
      idna
      jsonschema
      levenshtein
      lxml
      markdown2
      mutagen
      natsort
      netifaces-plus
      oauth2client
      packaging
      pillow
      polib
      py7zr
      pyasn1
      pyasn1-modules
      pycountry
      pypdf
      python-dateutil
      python-ldap
      python-magic
      pytz
      pyyaml
      qrcode
      rarfile
      regex
      requests
      rsa
      scholarly
      sqlalchemy
      sqlalchemy-utils
      tabulate
      tornado
      unidecode
      uritemplate
      urllib3
      wand
    ]
  );

  # The new UI. vite writes it to ../cps/static/app, next to the frontend dir.
  frontend = buildNpmPackage {
    pname = "calibre-web-nextgen-frontend";
    inherit version;
    src = lib.fileset.toSource {
      root = ../.;
      fileset = lib.fileset.unions [
        ../frontend
        ../cps/static/js/reading/selection-observer.js
        ../cps/static/js/reading/selection-observer.d.ts
      ];
    };
    npmRoot = "frontend";
    # Hashes come from package-lock.json, so a lockfile change needs no edit here.
    npmDeps = importNpmLock { npmRoot = ../frontend; };
    npmConfigHook = importNpmLock.npmConfigHook;
    preBuild = "cd frontend";
    installPhase = ''
      runHook preInstall
      cp -r ../cps/static/app $out
      runHook postInstall
    '';
  };
in
stdenvNoCC.mkDerivation {
  pname = "calibre-web-nextgen";
  inherit version src;

  nativeBuildInputs = [
    makeWrapper
    gettext
  ];

  buildPhase = ''
    runHook preBuild
    bash scripts/compile_translations.sh
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    app=$out/share/calibre-web-nextgen
    mkdir -p $app
    cp -r cps scripts cps.py dirs.json VERSION $app/
    cp -r ${frontend} $app/cps/static/app

    # Without CALIBRE_DBPATH, keep state in ~/.calibre-web-automated, not in the store.
    touch $app/cps/.HOMEDIR

    makeWrapper ${pythonEnv}/bin/python $out/bin/cps \
      --add-flags $app/cps.py \
      --prefix PATH : ${
        lib.makeBinPath [
          pythonEnv
          calibre
          kepubify
        ]
      } \
      --set-default CWA_INSTALLED_VERSION v${version}
    makeWrapper ${pythonEnv}/bin/python $out/bin/cwa-python \
      --prefix PATH : ${
        lib.makeBinPath [
          pythonEnv
          calibre
          kepubify
        ]
      } \
      --set-default CWA_INSTALLED_VERSION v${version}
    runHook postInstall
  '';

  passthru = {
    inherit frontend pythonEnv;
    appRoot = "share/calibre-web-nextgen";
  };

  meta = {
    description = "Community continuation of Calibre-Web-Automated";
    homepage = "https://github.com/new-usemame/Calibre-Web-NextGen";
    changelog = "https://github.com/new-usemame/Calibre-Web-NextGen/releases";
    license = lib.licenses.gpl3Plus;
    platforms = lib.platforms.linux;
    mainProgram = "cps";
  };
}
