{
  callPackages,
  python312,
  nix-gitignore,
}:
let
  shells = callPackages ./shells.nix { };

  py = python312;

  hostfactory = py.pkgs.buildPythonPackage rec {
    pname = "hostfactory";
    version = "1.0";
    pyproject = true;

    # TODO: move source up one level, otherwise app is rebuilt every time
    #       there is a change in root level expressions.
    src =
      nix-gitignore.gitignoreSource
        [
          "helm"
          "deployments"
          "bin"
          "docs"
          "tools"
          ./.gitignore
        ]
        ./.;

    nativeBuildInputs = with py.pkgs; [
      setuptools
      wheel
      pep517
      pip
      hatchling
      pytest-cov
      mypy
    ];

    nativeCheckInputs = with py.pkgs; [
      pytestCheckHook
      pytest-mock
    ];

    propagatedBuildInputs = with py.pkgs; [
      click
      kubernetes
      boto3
      jinja2
      typing-extensions
      inotify
      wrapt
      rich
      pydantic
      tenacity
    ];
  };
in
{ inherit hostfactory; } // shells
