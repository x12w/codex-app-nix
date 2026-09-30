{
  description = "OpenAI Codex desktop app for NixOS";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

      chatgpt = pkgs.callPackage ./package.nix { };

      # The Codex agent and ripgrep that OpenAI ships inside the Electron
      # bundle, exposed on their own so they can be used without the GUI.
      codex-cli = pkgs.runCommand "codex-cli-${chatgpt.version}" { } ''
        mkdir -p "$out/bin"
        ln -s ${chatgpt}/lib/chatgpt/resources/codex "$out/bin/codex"
        ln -s ${chatgpt}/lib/chatgpt/resources/rg "$out/bin/rg"
      '';

      codexApp = {
        type = "app";
        program = "${chatgpt}/bin/chatgpt";
        meta.description = "Launch the OpenAI Codex desktop app";
      };
    in
    {
      packages.${system} = {
        inherit chatgpt codex-cli;
        codex = codex-cli;
        default = chatgpt;
      };

      apps.${system} = {
        chatgpt = codexApp;
        default = codexApp;
      };

      overlays.default = final: _prev: {
        chatgpt = final.callPackage ./package.nix { };
      };

      formatter.${system} = pkgs.nixfmt;
    };
}
