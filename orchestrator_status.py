#!/usr/bin/env python3
"""
Orchestrator Status
===================
CLI tool to display the node topology of a MySQL cluster managed by
Orchestrator.

Usage:
    python orchestrator_status.py --host <orchestrator_host> --cluster <alias>
"""

import sys

import click
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


def discover_new_node(orchestrator_host: str, db_host: str, db_port: int | str):
    """
    Triggers Orchestrator to discover a new MySQL instance.
    """
    url = f"http://{orchestrator_host}:3000/api/discover/{db_host}/{db_port}"
    
    console.print(f"[dim]Requesting discovery for [cyan]{db_host}:{db_port}[/] on Orchestrator…[/]")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.ConnectionError:
        console.print(f"[bold red]✗[/] Could not connect to Orchestrator at "
                       f"[cyan]{orchestrator_host}:3000[/].")
        sys.exit(1)
    except requests.exceptions.HTTPError as err:
        console.print(f"[bold red]✗[/] Orchestrator returned an error during discovery: {err}")
        sys.exit(1)
    except requests.exceptions.RequestException as err:
        console.print(f"[bold red]✗[/] Discovery request failed: {err}")
        sys.exit(1)

    instance_key = data.get("Key", {})
    hostname = instance_key.get("Hostname", "Unknown")
    console.print(f"[bold green]✔[/] Success! Orchestrator is now tracking: [cyan]{hostname}[/]")

# ── Data fetching ────────────────────────────────────────────────────────────

def get_orchestrator_nodes(orchestrator_host: str, cluster_alias: str) -> list[dict]:
    """
    Fetches all nodes in a cluster from the Orchestrator API and returns a
    structured list ready for display.
    """
    url = f"http://{orchestrator_host}:3000/api/cluster-nodes/{cluster_alias}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        nodes = response.json()
    except requests.exceptions.ConnectionError:
        console.print(f"[bold red]✗[/] Could not connect to Orchestrator at "
                       f"[cyan]{orchestrator_host}:3000[/].")
        sys.exit(1)
    except requests.exceptions.HTTPError as err:
        console.print(f"[bold red]✗[/] Orchestrator returned an error: {err}")
        sys.exit(1)
    except requests.exceptions.RequestException as err:
        console.print(f"[bold red]✗[/] Request failed: {err}")
        sys.exit(1)

    table_data = []

    for node in nodes:
        # Instance (Host:Port)
        instance = f"{node['Key']['Hostname']}:{node['Key']['Port']}"

        # Master (Host:Port) — Primary will have an empty MasterKey Hostname
        master_data = node.get("MasterKey", {})
        master_host = master_data.get("Hostname", "")
        master_port = master_data.get("Port", "")
        master = f"{master_host}:{master_port}" if master_host else "— (Primary)"

        # Role
        if not master_host:
            role = "Primary"
        elif node.get("Replicas"):
            role = "Intermediate Replica"
        else:
            role = "Replica"

        # Health / lag / mode
        status = "Healthy" if node.get("IsLastCheckValid") else "Check Failed"
        lag_val = node.get("SecondsBehindMaster", {})
        lag = f"{lag_val.get('Int64', 0)}s" if isinstance(lag_val, dict) else "N/A"
        read_only = "RO" if node.get("ReadOnly") else "RW"

        table_data.append({
            "instance": instance,
            "role": role,
            "master": master,
            "lag": lag,
            "status": status,
            "mode": read_only,
        })

    return table_data


# ── Display ──────────────────────────────────────────────────────────────────

def _role_style(role: str) -> str:
    return {
        "Primary":             "bold green",
        "Intermediate Replica": "bold yellow",
        "Replica":             "cyan",
    }.get(role, "")


def _status_style(status: str) -> str:
    return "green" if status == "Healthy" else "bold red"


def print_topology(nodes: list[dict], cluster_alias: str, orchestrator_host: str):
    """Render the cluster topology as a Rich table."""
    table = Table(
        title=f"Cluster Topology — {cluster_alias}",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold magenta",
        header_style="bold cyan",
    )
    table.add_column("Instance",       style="dim")
    table.add_column("Role",           justify="center")
    table.add_column("Master",         style="dim")
    table.add_column("Lag",            justify="right")
    table.add_column("Status",         justify="center")
    table.add_column("Mode",           justify="center")

    for n in nodes:
        rs = _role_style(n["role"])
        ss = _status_style(n["status"])
        mode_style = "red" if n["mode"] == "RW" else "green"

        table.add_row(
            n["instance"],
            f"[{rs}]{n['role']}[/]" if rs else n["role"],
            n["master"],
            n["lag"],
            f"[{ss}]{n['status']}[/]",
            f"[{mode_style}]{n['mode']}[/]",
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[dim]Source: [cyan]http://{orchestrator_host}:3000[/] — "
        f"{len(nodes)} node(s) in cluster [yellow]{cluster_alias}[/][/]"
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--host",    "-H", required=True,
              help="Orchestrator host (hostname or IP).")
@click.option("--cluster", "-c", required=True,
              help="Cluster alias as configured in Orchestrator.")
def main(host: str, cluster: str):
    """
    Orchestrator Status — display the node topology of a MySQL cluster.
    """
    console.print(Panel(
        "[bold white]Orchestrator Status[/]\n"
        "[dim]MySQL cluster topology viewer[/]",
        border_style="bright_magenta",
        expand=False,
    ))

    console.print(f"\n[dim]Fetching nodes for cluster [yellow]{cluster}[/] "
                   f"from [cyan]{host}:3000[/] …[/]")

    nodes = get_orchestrator_nodes(host, cluster)

    if not nodes:
        console.print(Panel(
            f"  No nodes returned for cluster [yellow]{cluster}[/].\n"
            "  Check the cluster alias and that Orchestrator can reach the instances.",
            title="[bold yellow]No Data[/]",
            border_style="yellow",
            expand=False,
        ))
        sys.exit(1)

    print_topology(nodes, cluster, host)


if __name__ == "__main__":
    main()