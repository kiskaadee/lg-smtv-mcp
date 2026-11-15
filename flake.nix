{
  description = "LG Smart TV (webOS) MCP server and testing environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      ps = pkgs.python3Packages;

      runtimeDeps = [
        ps.websockets
        ps.mcp
        ps.asyncssh
        ps.uvicorn
      ];

      testDeps = [
        ps.httpx
        ps.pytest
        ps.pytest-asyncio
      ];

      pythonEnv = pkgs.python3.withPackages (_: runtimeDeps ++ testDeps);
    in
    {
      packages.${system}.default = ps.buildPythonApplication {
        pname = "lg-smtv-mcp";
        version = "0.1.0";
        src = ./.;
        pyproject = true;
        build-system = [ ps.setuptools ];
        dependencies = runtimeDeps;
        nativeCheckInputs = [
          ps.pytestCheckHook
          pkgs.ruff
          pkgs.pyright
        ] ++ testDeps;
        preCheck = ''
          ruff check .
          ruff format --check .
          pyright
        '';
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pythonEnv
          pkgs.pyright
          pkgs.ruff
        ];
        shellHook = ''
          export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
          if [ -d .git ] && [ -d .githooks ]; then
            git config core.hooksPath .githooks
          fi
        '';
      };
    };
}
