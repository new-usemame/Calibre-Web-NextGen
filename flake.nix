{
  description = "Calibre-Web-NextGen: automated eBook management built on Calibre-Web";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        calibre-web-nextgen = pkgs.callPackage ./nix/package.nix { };
        default = calibre-web-nextgen;
      });

      overlays.default = final: _prev: {
        calibre-web-nextgen = final.callPackage ./nix/package.nix { };
      };

      nixosModules = rec {
        calibre-web-nextgen =
          { lib, pkgs, ... }:
          {
            imports = [ ./nix/module.nix ];
            services.calibre-web-nextgen.package = lib.mkDefault (pkgs.callPackage ./nix/package.nix { });
          };
        default = calibre-web-nextgen;
      };

      checks = forAllSystems (pkgs: {
        vm = pkgs.callPackage ./nix/test.nix { module = self.nixosModules.default; };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
