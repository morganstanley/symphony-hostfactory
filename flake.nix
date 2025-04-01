{
  inputs = {
    nixpkgs.url = "git+http://nix@stashblue.ms.com:11990/atlassian-stash/scm/nix_nixpkgs/nixpkgs.git?ref=ms/nixos-24.11";
    # nixpkgs = {
    #   type = "indirect";
    #   id = "nixpkgs";
    # };
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
