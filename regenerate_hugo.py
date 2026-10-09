#!/usr/bin/env python3
"""Regenerate Hugo content from validation results and area metadata.

Usage (--apply is required to actually do anything; without it, this help
is shown and nothing runs):
  ./regenerate_hugo.py --apply                # all areas
  ./regenerate_hugo.py --apply --area NS      # single area
  ./regenerate_hugo.py --apply --serve        # regenerate then start dev server
  ./regenerate_hugo.py --apply --sync-back    # also sync content/static to this
                                       # machine's own local paths, for a
                                       # low-latency `hugo server` preview
                                       # here instead of on the relay

Generation always runs on the relay host (see regen_hosts.yaml) -- running
this from any other machine listed there auto-relays over ssh, so you
never have to remember to do that by hand. Pass --no-relay to force a
genuinely local run instead (e.g. for testing non-DB-dependent page types
on a machine other than the relay -- the status page/production filter
won't reflect real data in that mode, since the registry only lives on
the relay).

Nothing is synced back by default -- the relay is the primary path end to
end (generate here, then deploy_ghpages.py also runs here directly, see
its own --help). --sync-back is for the specific case of wanting a local,
low-latency `hugo server` preview on a machine other than the relay.
"""
import argparse
import socket
import subprocess
import sys
from pathlib import Path

import yaml

from ocean_data import host_config as oc_host_config  # type: ignore[import-not-found]

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "regen_hosts.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def host_config(config: dict, hostname: str) -> dict:
    host_cfg = config["hosts"].get(hostname)
    if host_cfg is None:
        sys.exit(
            f"ERROR: no entry for host {hostname!r} in {CONFIG_PATH} -- "
            f"add one to run this here."
        )
    return host_cfg


def relay(config: dict, oc_cfg: dict, relay_host: str, args: argparse.Namespace) -> None:
    # --sync-back and --serve are both local-machine-only concerns (they
    # run here, after the ssh call returns -- see below) so they're
    # dropped, not forwarded to the relay. --serve in particular must
    # never reach the relay: cli.reporting's own --serve runs `hugo
    # server` on whatever machine it's given to, and bb-server1 is in
    # Hetzner/Germany -- a live dev server there would have bad latency
    # for anyone actually browsing it.
    relayed = oc_host_config.relay_if_needed(
        oc_cfg, sys.argv[1:],
        repo_key='OceanICU/oceanicu_3d', entry_point='./regenerate_hugo.py',
        drop_flags=frozenset({"--sync-back", "--serve"}),
    )
    if not relayed:
        # main() already decided (via regen_hosts.yaml's relay_host) that
        # relaying was needed before calling this -- if oc_host_config's
        # own check (via ~/.config/oceanicu/hosts.yaml) disagrees, the two
        # config sources have drifted out of sync. Fail loudly rather than
        # silently falling through to "Done" below as if a relay happened.
        sys.exit(
            "ERROR: expected to relay to "
            f"{oc_cfg.get('relay_host')!r} but relay_if_needed() declined -- "
            "check ~/.config/oceanicu/hosts.yaml (relay_host must match "
            f"regen_hosts.yaml's {relay_host!r}, and hosts.{relay_host} "
            "must have conda_env + repos.'OceanICU/oceanicu_3d' set)."
        )

    # --serve implies --sync-back (no point serving without local content)
    if args.sync_back or args.serve:
        sync_back(config, relay_host, serve=args.serve)
        return

    print()
    print("Done. To publish (a real, public, hard-to-reverse push to")
    print("gh-pages -- not run automatically here), from anywhere:")
    print("  cd ~/source/repos/ocean-post && ./deploy_ghpages.py --apply")


def sync_back(config: dict, relay_host: str, serve: bool = False) -> None:
    """rsync content/static from the relay down to this machine's own
    local paths, so a local `hugo server` has something current to read."""
    local_cfg = host_config(config, socket.gethostname())
    local_out = local_cfg["hugo_out"]
    relay_out = host_config(config, relay_host)["hugo_out"]

    print(f"Syncing content/static back to {local_out} for local preview...", file=sys.stderr)
    for sub in ("content", "static"):
        subprocess.run(
            ["rsync", "-a", "--delete", f"{relay_host}:{relay_out}/{sub}/", f"{local_out}/{sub}/"],
            check=True,
        )

    hugo_dir = SCRIPT_DIR / "hugo"
    if serve:
        print()
        print(f"Synced. Starting hugo server here (not on {relay_host}) …")
        print("Open http://localhost:1313/oceanicu_3d/ in a browser. Ctrl-C to stop.")
        subprocess.run(["hugo", "server"], cwd=hugo_dir, check=True)
    else:
        print()
        print("Synced. To preview locally:")
        print(f"  cd {hugo_dir} && hugo server")
        print("Then open http://localhost:1313/oceanicu_3d/ in a browser.")


def generate_locally(config: dict, hostname: str, relay_host: str, args: argparse.Namespace) -> None:
    if args.serve and hostname == relay_host:
        sys.exit(
            f"ERROR: refusing --serve on {relay_host} -- it's in Hetzner/Germany, "
            f"so a live dev server there has bad latency for anyone actually "
            f"browsing it. Run this from another machine with --sync-back "
            f"(or --serve, which implies it) instead."
        )

    host_cfg = host_config(config, hostname)

    # Invoke the conda env's python3 directly rather than "conda run":
    # conda's shell function isn't set up in a plain non-interactive ssh
    # command, so "conda" itself often isn't even on PATH there.
    env_python = Path.home() / "miniconda3" / "envs" / host_cfg["conda_env"] / "bin" / "python3"
    if not env_python.exists():
        sys.exit(f"ERROR: no python3 found for conda env {host_cfg['conda_env']!r} at {env_python}")

    cmd = [
        str(env_python), "-m", "cli.reporting",
        "--analyses-dir", host_cfg["analyses_dir"],
        "--recursive",
        "--hugo", host_cfg["hugo_out"],
    ]
    if host_cfg.get("db"):
        cmd += ["--db", host_cfg["db"]]
    # --area (this invocation's own one-off override) always wins over the
    # persistent enabled_areas allowlist below -- e.g. `--area AMM7` to
    # regenerate just that one area's own pages without touching the
    # allowlist or any other area's already-published pages.
    if args.area:
        cmd += ["--area", args.area]
    else:
        for area in config.get("enabled_areas") or []:
            cmd += ["--area", area]
    # --serve is handled separately below (a plain local hugo server on
    # this machine), never forwarded to cli.reporting -- see the refusal
    # check above for why.

    subprocess.run(cmd, check=True)

    if args.serve:
        hugo_dir = SCRIPT_DIR / "hugo"
        print()
        print(f"Starting hugo server here ({hostname}) …")
        print("Open http://localhost:1313/oceanicu_3d/ in a browser. Ctrl-C to stop.")
        subprocess.run(["hugo", "server"], cwd=hugo_dir, check=True)
        return

    hugo_dir = SCRIPT_DIR / "hugo"
    print()
    print(f"Content written to {host_cfg['hugo_out']}/content/")
    print(f"To serve locally:  cd {hugo_dir} && hugo server")
    print(f"To build static:   cd {hugo_dir} && hugo")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--area", help="Regenerate a single area only")
    parser.add_argument(
        "--serve", action="store_true",
        help="Run a local `hugo server` preview after generating -- never "
             "on the relay itself (Hetzner/Germany latency), so this "
             "implies --sync-back when relaying and is refused outright "
             "if this machine IS the relay host.",
    )
    parser.add_argument(
        "--sync-back", action="store_true",
        help="After relay generation, rsync content/static back to this "
             "machine's own local paths for a hugo server preview",
    )
    parser.add_argument(
        "--no-relay", action="store_true",
        help="Force a genuinely local run, even if not on the relay host",
    )
    parser.add_argument(
        "--relay-host",
        help="Override the relay hostname from regen_hosts.yaml",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually run generation. Without this, just shows this help "
             "and does nothing -- a deliberate confirmation gate, not a "
             "dry-run preview.",
    )
    args = parser.parse_args()

    if not args.apply:
        parser.print_help()
        return

    config = load_config()
    relay_host = args.relay_host or config["relay_host"]
    hostname = socket.gethostname()

    # ~/.config/oceanicu/hosts.yaml -- the generic relay config
    # (ocean_data.host_config), separate from regen_hosts.yaml's own
    # enabled_areas/hugo_out/analyses_dir/db (unrelated to relaying,
    # stays exactly as-is). --relay-host applies to both, so a one-off
    # override can't make the two sources disagree about which host is
    # the relay.
    oc_cfg = oc_host_config.load_host_config('oceanicu')
    if args.relay_host:
        oc_cfg = {**oc_cfg, 'relay_host': args.relay_host}

    if hostname != relay_host and not args.no_relay:
        relay(config, oc_cfg, relay_host, args)
    else:
        generate_locally(config, hostname, relay_host, args)


if __name__ == "__main__":
    main()
