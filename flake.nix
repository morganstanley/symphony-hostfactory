{
  inputs = {
    nixpkgs = {
      type = "indirect";
      id = "nixpkgs";
    };
  };
  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      flakePkgs = pkgs.callPackages ./. { };
      defaultPackage = flakePkgs.devshell;
    in
    {
      defaultPackage.${system} = defaultPackage;
      packages.x86_64-linux = flakePkgs;
    };
}