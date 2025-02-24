{
  awscli2,
  eksctl,
  coreutils,
  mkShell,
  execline,
  findutils,
  gnugrep,
  gnused,
  jq,
  kubectl,
  kubernetes-helm,
  openshift,
  s6,
  python311,
}:
let
  python = with python311.pkgs; [
    # Project runtime dependencies.
    click
    kubernetes
    boto3
    jinja2
    typing-extensions
    inotify

    # TODO: add lint and test dependencies.

    # Standard python stuff.
    pep517
    pip
    pytest
    pytestCheckHook
    pytest-mock
    setuptools
    wheel
  ];
in
let
  devshell = mkShell {
    packages = [
      awscli2
      coreutils
      eksctl
      execline
      findutils
      gnugrep
      gnused
      jq
      kubectl
      kubernetes-helm
      openshift
      s6
      python
    ];
    shellHook = ''
      # Heurisitic to cd to the root of the project.
      #
      #
      src="$(dirname "$(dirname "$out")")"
      cd "$src"

      venv="$src"/venv
      if test -w default.nix; then

        # Create a virtual environment and install the project
        # in editable mode.
        python -mvenv "$venv"
        # shellcheck disable=SC1091
        source "$venv"/bin/activate
        cd "$venv"/lib64/python3.11/site-packages

        IFS=':' read -ra array <<< "$PYTHONPATH"

        for element in "''${array[@]}"; do
          find $element -maxdepth 1 -mindepth 1 -name '*.dist-info' | \
          while read -r i; do
            fname="$(cut -d'-' -f1 <<< $(basename "$i"))".pth
            if ! diff -q "$fname" - <<< "$element" > /dev/null 2>&1; then
              echo $element > "$fname"
            fi
          done
        done
        unset PYTHONPATH
        cd "$src"
        "$venv"/bin/pip install -e '.[dev]'
      else
        # Assume existing virtual environment.
        # TODO: consider installing into different venv for each user.
        unset PYTHONPATH
        # shellcheck disable=SC1091
        source "$venv"/bin/activate
      fi

      # Debug output to confirm where local tools are coming from.
      which hostfactory
      export PS1="+ $PS1"
    '';
  };
in
{
  inherit devshell;
}
