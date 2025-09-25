{
  callPackages,
  python312,
  nix-gitignore,
}:
let
  py = python312;
  makevenv = ./makevenv.sh;

  hostfactory = py.pkgs.buildPythonPackage rec {
    pname = "hostfactory";
    version = "1.0";
    pyproject = true;

    shellHook = ''
    source ${makevenv}
    '';

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
      httpx
    ];

    nativeCheckInputs = with py.pkgs; [
      pytestCheckHook
      pytest-mock
      ruff
      mypy
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
      pydantic-settings
      tenacity
      prometheus_client
      sqlalchemy
      psycopg2
      alembic
      fastapi
      uvicorn
    ];

    installCheckPhase = ''
      echo Running pytest
      ${py.interpreter} -m pytest
      echo Running ruff check
      ruff check
      echo Running ruff format --check
      ruff format --check
      echo $unning mypy
      mypy $src/src
      echo Done
    '';
  };
in
{ inherit hostfactory; }
