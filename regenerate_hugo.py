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
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import yaml

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


def relay(config: dict, relay_host: str, args: argparse.Namespace) -> None:
    print(f"Not on {relay_host} -- running there over ssh instead...", file=sys.stderr)

    # Forward this invocation's actual argv verbatim rather than manually
    # re-listing each flag from the parsed Namespace -- the latter is a
    # real bug class (forget to forward one new flag here and it's
    # silently dropped on the relay hop, not an error; this exact pattern
    # in deploy_ghpages.py's relay() caused a real, unintended production
    # deploy during testing). --sync-back is local-machine-only (it runs
    # here, after the ssh call returns) so it's excluded, not forwarded.
    drop = {"--no-relay", "--sync-back"}
    remote_args = [a for a in sys.argv[1:] if a not in drop] + ["--no-relay"]
    remote_cmd = (
        "cd ~/source/repos/OceanICU/oceanicu_3d && "
        "./regenerate_hugo.py " + shlex.join(remote_args)
    )
    subprocess.run(["ssh", relay_host, remote_cmd], check=True)

    if args.sync_back:
        sync_back(config, relay_host)

    print()
    print("Done. To publish (a real, public, hard-to-reverse push to")
    print("gh-pages -- not run automatically here), from anywhere:")
    print("  cd ~/source/repos/ocean-post && ./deploy_ghpages.py --apply")


def sync_back(config: dict, relay_host: str) -> None:
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
    print()
    print("Synced. To preview locally:")
    print(f"  cd {SCRIPT_DIR / 'hugo'} && hugo server")
    print("Then open http://localhost:1313/oceanicu_3d/ in a browser.")


def generate_locally(config: dict, hostname: str, args: argparse.Namespace) -> None:
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
    if args.area:
        cmd += ["--area", args.area]
    if args.serve:
        cmd.append("--serve")

    subprocess.run(cmd, check=True)

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
        help='Run "hugo server" after generating (implies --build)',
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

    if hostname != relay_host and not args.no_relay:
        relay(config, relay_host, args)
    else:
        generate_locally(config, hostname, args)


if __name__ == "__main__":
    main()
