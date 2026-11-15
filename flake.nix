{
  description = "LG Smart TV (webOS) MCP server and testing environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      pythonEnv = pkgs.python3.withPackages (ps: [
        ps.websockets
        ps.mcp
        ps.asyncssh
        ps.uvicorn
        ps.httpx
        ps.pytest
        ps.pytest-asyncio
      ]);
    in
    {
      packages.${system}.default = pkgs.python3Packages.buildPythonApplication {
        pname = "lg-smtv-mcp";
        version = "0.1.0";
        src = ./.;
        pyproject = true;
        build-system = [ pkgs.python3Packages.setuptools ];
        dependencies = with pkgs.python3Packages; [
          websockets
          mcp
          asyncssh
          uvicorn
        ];
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pythonEnv
          pkgs.pyright
          pkgs.ruff
        ];
        shellHook = ''
          export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
        '';
      };
    };
}
