#!/usr/bin/env python3
"""
MySQL Replication Manager
=========================
CLI tool to set up MySQL replication by cloning data from a master (source)
server to a replica using the MySQL Clone Plugin, then configuring and
starting replication.

Usage:
    python mysql_replication.py --master <host:port> --replica <host:port>
"""

import os
import sys
import time
import threading

import click
import mysql.connector
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.live import Live
from rich import box

# ── Initialise ──────────────────────────────────────────────────────────────
load_dotenv()
console = Console()

# ── Credential helpers ──────────────────────────────────────────────────────

def _env(key: str) -> str:
    """Return an environment variable or abort with a clear message."""
    value = os.getenv(key)
    if not value:
        console.print(f"[bold red]✗[/] Environment variable [yellow]{key}[/] is not set. "
                       "Check your .env file.")
        sys.exit(1)
    return value


def get_root_creds() -> tuple[str, str]:
    return _env("MYSQL_ROOT_USER"), _env("MYSQL_ROOT_PASSWORD")


def get_clone_creds() -> tuple[str, str]:
    return _env("MYSQL_CLONE_USER"), _env("MYSQL_CLONE_PASSWORD")


def get_repl_creds() -> tuple[str, str]:
    return _env("MYSQL_REPL_USER"), _env("MYSQL_REPL_PASSWORD")


def parse_host_port(value: str, default_port: int = 3306) -> tuple[str, int]:
    """Parse a 'host:port' string. Port defaults to 3306 if omitted."""
    if ":" in value:
        host, port_str = value.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            console.print(f"[bold red]✗[/] Invalid port in [yellow]{value}[/]. "
                           "Expected host:port format.")
            sys.exit(1)
    return value, default_port


# ── Connection helper ───────────────────────────────────────────────────────

def connect(host: str, port: int, user: str, password: str,
            database: str | None = None) -> mysql.connector.MySQLConnection:
    """Open a connection to a MySQL server and return it."""
    try:
        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            port=port,
            database=database,
            connection_timeout=10,
        )
        return conn
    except mysql.connector.Error as err:
        console.print(f"[bold red]✗[/] Failed to connect to [cyan]{host}:{port}[/] "
                       f"as [yellow]{user}[/]: {err}")
        sys.exit(1)


# ── Pre-flight — Ensure required users exist ───────────────────────────────

def _uninstall_validate_password(conn: mysql.connector.MySQLConnection, label: str):
    """Remove the validate_password component if it is loaded."""
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM mysql.component "
        "WHERE component_urn LIKE '%%validate_password%%'"
    )
    if cur.fetchone()["cnt"] > 0:
        console.print(f"[dim]  Removing validate_password component on {label}…[/]")
        try:
            cur.execute("UNINSTALL COMPONENT 'file://component_validate_password'")
            conn.commit()
            console.print(f"[bold green]  ✔[/] validate_password removed on {label}.")
        except mysql.connector.Error as err:
            console.print(f"[yellow]  ⚠  Could not remove validate_password on {label}: {err}[/]")
    else:
        console.print(f"[dim]  validate_password not installed on {label} — OK[/]")
    cur.close()


def _user_exists(cur, user: str) -> bool:
    """Check whether a MySQL user account exists (any host)."""
    cur.execute("SELECT COUNT(*) AS cnt FROM mysql.user WHERE user = %s", (user,))
    return cur.fetchone()["cnt"] > 0


def ensure_users(master_conn: mysql.connector.MySQLConnection, master_label: str):
    """
    Ensure clone and replication users exist on the master.

    * Removes component_validate_password if installed.
    * Creates clone_user with BACKUP_ADMIN if missing.
    * Creates repl_user with REPLICATION SLAVE, REPLICATION CLIENT if missing.
    """
    console.print()
    console.print(Panel(
        "[bold]Pre-flight — Validate Password & User Provisioning[/]",
        border_style="cyan",
        expand=False,
    ))

    # ── Remove validate_password ────────────────────────────────────────
    _uninstall_validate_password(master_conn, master_label)

    clone_user, clone_password = get_clone_creds()
    repl_user, repl_password = get_repl_creds()
    cur = master_conn.cursor(dictionary=True)

    # ── Clone user ──────────────────────────────────────────────────────
    if _user_exists(cur, clone_user):
        console.print(f"[dim]  Clone user [yellow]{clone_user}[/] already exists — OK[/]")
    else:
        console.print(f"[dim]  Creating clone user [yellow]{clone_user}[/]…[/]")
        cur.execute(f"CREATE USER %s@'%%' IDENTIFIED BY %s", (clone_user, clone_password))
        cur.execute(f"GRANT BACKUP_ADMIN ON *.* TO %s@'%%'", (clone_user,))
        master_conn.commit()
        console.print(f"[bold green]  ✔[/] Clone user [yellow]{clone_user}[/] created with BACKUP_ADMIN.")

    # ── Replication user ────────────────────────────────────────────────
    if _user_exists(cur, repl_user):
        console.print(f"[dim]  Replication user [yellow]{repl_user}[/] already exists — OK[/]")
    else:
        console.print(f"[dim]  Creating replication user [yellow]{repl_user}[/]…[/]")
        cur.execute(f"CREATE USER %s@'%%' IDENTIFIED BY %s", (repl_user, repl_password))
        cur.execute(f"GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO %s@'%%'", (repl_user,))
        master_conn.commit()
        console.print(f"[bold green]  ✔[/] Replication user [yellow]{repl_user}[/] created "
                       "with REPLICATION SLAVE, REPLICATION CLIENT.")

    cur.close()
    console.print()


# ── Step 1 — Verify servers ────────────────────────────────────────────────

def _server_info(conn: mysql.connector.MySQLConnection) -> dict:
    """Gather useful metadata from a MySQL server."""
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT @@hostname AS hostname, @@server_id AS server_id, "
                "@@read_only AS read_only, @@super_read_only AS super_read_only, "
                "@@server_uuid AS server_uuid, @@version AS version, "
                "@@port AS port, @@gtid_mode AS gtid_mode")
    info = cur.fetchone()

    # Active non-system connections (exclude our own and system threads)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.processlist "
        "WHERE user NOT IN ('system user', 'event_scheduler','pmm','orc_client_user', %s) "
        "AND command != 'Daemon' "
        "AND id != CONNECTION_ID()",
        (conn.user,),
    )
    info["active_connections"] = cur.fetchone()["cnt"]

    # Running queries (non-Sleep, non-system)
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.processlist "
        "WHERE user NOT IN ('system user', 'event_scheduler', %s) "
        "AND command NOT IN ('Daemon', 'Sleep', 'Binlog Dump', 'Binlog Dump GTID') "
        "AND id != CONNECTION_ID()",
        (conn.user,),
    )
    info["running_queries"] = cur.fetchone()["cnt"]

    cur.close()
    return info


def verify_servers(master_conn, replica_conn, master_host, replica_host):
    """Display server topology and ensure the replica is safe to wipe."""

    master_info = _server_info(master_conn)
    replica_info = _server_info(replica_conn)

    # ── Topology table ──────────────────────────────────────────────────
    table = Table(
        title="Server Topology",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold magenta",
        header_style="bold cyan",
    )
    table.add_column("Property", style="dim")
    table.add_column(f"🟢  MASTER  ({master_host})", style="green")
    table.add_column(f"🔵  REPLICA  ({replica_host})", style="blue")

    rows = [
        ("Hostname",          str(master_info["hostname"]),       str(replica_info["hostname"])),
        ("Server UUID",       master_info["server_uuid"],         replica_info["server_uuid"]),
        ("Server ID",         str(master_info["server_id"]),      str(replica_info["server_id"])),
        ("Version",           master_info["version"],             replica_info["version"]),
        ("Port",              str(master_info["port"]),            str(replica_info["port"])),
        ("GTID Mode",         str(master_info["gtid_mode"]),       str(replica_info["gtid_mode"])),
        ("read_only",         str(master_info["read_only"]),      str(replica_info["read_only"])),
        ("super_read_only",   str(master_info["super_read_only"]),str(replica_info["super_read_only"])),
        ("Active connections",str(master_info["active_connections"]),str(replica_info["active_connections"])),
        ("Running queries",   str(master_info["running_queries"]),str(replica_info["running_queries"])),
    ]
    for label, m, r in rows:
        table.add_row(label, m, r)

    console.print()
    console.print(table)

    # ── Server-ID collision check ────────────────────────────────────────
    if master_info["server_id"] == replica_info["server_id"]:
        console.print()
        console.print(Panel(
            f"  ✗  Both servers have [bold]server_id = {master_info['server_id']}[/]\n"
            f"     Each server in a replication topology must have a unique server-id.\n"
            f"     Update [cyan]my.cnf[/] on one of the servers and restart MySQL.",
            title="[bold red]Server-ID Check — FAILED[/]",
            border_style="red",
            expand=False,
        ))
        sys.exit(1)

    # ── GTID mode check ─────────────────────────────────────────────────
    gtid_problems = []
    if master_info["gtid_mode"] != "ON":
        gtid_problems.append(f"Master ({master_host}): gtid_mode = [yellow]{master_info['gtid_mode']}[/]")
    if replica_info["gtid_mode"] != "ON":
        gtid_problems.append(f"Replica ({replica_host}): gtid_mode = [yellow]{replica_info['gtid_mode']}[/]")

    if gtid_problems:
        console.print()
        console.print(Panel(
            "\n".join(f"  ✗  {p}" for p in gtid_problems) + "\n\n"
            "     This tool requires GTID-based replication.\n"
            "     Set [cyan]gtid_mode=ON[/] and [cyan]enforce_gtid_consistency=ON[/] in my.cnf.",
            title="[bold red]GTID Check — FAILED[/]",
            border_style="red",
            expand=False,
        ))
        sys.exit(1)

    # ── Safety checks on the replica ────────────────────────────────────
    console.print()
    problems = []

    if replica_info["active_connections"] > 0:
        problems.append(
            f"[yellow]{replica_info['active_connections']}[/] active non-system "
            f"connection(s) on the replica"
        )
    if replica_info["running_queries"] > 0:
        problems.append(
            f"[yellow]{replica_info['running_queries']}[/] query(ies) currently "
            f"running on the replica"
        )

    if problems:
        console.print(Panel(
            "\n".join(f"  ⚠  {p}" for p in problems),
            title="[bold red]Replica Safety Check — FAILED[/]",
            border_style="red",
            expand=False,
        ))
        console.print("[bold red]✗[/] The target replica has active connections or "
                       "running queries. Resolve them before proceeding.")
        sys.exit(1)
    else:
        console.print(Panel(
            "  ✔  No active user connections on the replica\n"
            "  ✔  No running queries on the replica",
            title="[bold green]Replica Safety Check — PASSED[/]",
            border_style="green",
            expand=False,
        ))

    # ── Warning banner ──────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold yellow]⚠  WARNING[/]\n\n"
        f"This will [bold red]DESTROY ALL DATA[/] on [cyan]{replica_host}[/] "
        f"and replace it with a clone of [cyan]{master_host}[/].\n"
        f"Replication will then be configured from master → replica.",
        title="[bold yellow]Destructive Operation[/]",
        border_style="yellow",
        expand=False,
    ))

    if not Confirm.ask("\n[bold]Do you want to proceed?[/]", default=False):
        console.print("[dim]Aborted by user.[/]")
        sys.exit(0)


# ── Step 2 — Reset replica & clone ──────────────────────────────────────────

def reset_replica(replica_conn):
    """Stop and reset any existing replication on the replica."""
    cur = replica_conn.cursor()
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Stopping replication (if running)…")
        try:
            cur.execute("STOP REPLICA")
        except mysql.connector.Error:
            pass  # May not be running
        progress.update(task, description="Resetting replica configuration…")
        cur.execute("RESET REPLICA ALL")
        progress.update(task, completed=True)

    cur.close()
    console.print("[bold green]✔[/] Replica reset complete.")


def clone_from_master(replica_conn, master_host: str, master_port: int):
    """
    Use the MySQL Clone Plugin to clone data from the master to the replica.

    CLONE INSTANCE is a blocking DDL that replaces all data on the recipient,
    then restarts the MySQL instance automatically. We therefore need to
    reconnect after the clone finishes.
    """
    clone_user, clone_password = get_clone_creds()
    port = master_port

    cur = replica_conn.cursor(dictionary=True)

    # Ensure the clone plugin is installed on the replica
    cur.execute("SELECT COUNT(*) AS cnt FROM information_schema.plugins "
                "WHERE plugin_name = 'clone' AND plugin_status = 'ACTIVE'")
    if cur.fetchone()["cnt"] == 0:
        console.print("[dim]Installing clone plugin on replica…[/]")
        try:
            cur.execute("INSTALL PLUGIN clone SONAME 'mysql_clone.so'")
            replica_conn.commit()
            console.print("[bold green]✔[/] Clone plugin installed on replica.")
        except mysql.connector.Error as err:
            console.print(f"[bold red]✗[/] Failed to install clone plugin: {err}")
            sys.exit(1)
    else:
        console.print("[dim]Clone plugin already installed on replica — OK[/]")

    # Set the donor (master) credentials on the replica so the clone plugin
    # can authenticate against the master.
    console.print(f"[dim]Setting donor credentials for clone plugin…[/]")
    cur.execute("SET GLOBAL clone_valid_donor_list = %s", (f"{master_host}:{port}",))

    console.print(f"[bold cyan]⏳ Cloning data from [green]{master_host}:{port}[/] "
                   "— this may take a while …[/]\n")

    # Run the blocking CLONE INSTANCE in a background thread so we can
    # poll performance_schema.clone_progress from the main thread.
    clone_result: dict = {"error": None, "done": False}

    def _run_clone():
        try:
            clone_cur = replica_conn.cursor()
            clone_cur.execute(
                "CLONE INSTANCE FROM %s@%s:%s IDENTIFIED BY %s",
                (clone_user, master_host, port, clone_password),
            )
            clone_cur.close()
        except mysql.connector.Error as err:
            clone_result["error"] = err
        finally:
            clone_result["done"] = True

    clone_thread = threading.Thread(target=_run_clone, daemon=True)
    clone_thread.start()

    # Open a second connection to poll clone progress (the first is busy
    # executing CLONE INSTANCE).
    root_user, root_password = get_root_creds()
    try:
        monitor_conn = mysql.connector.connect(
            host=replica_conn.server_host,
            port=replica_conn.server_port,
            user=root_user,
            password=root_password,
            connection_timeout=10,
        )
    except mysql.connector.Error:
        # If we can't open a monitor connection, fall back to waiting blindly
        monitor_conn = None

    def _build_progress_table():
        """Query clone_progress and return a Rich Table."""
        tbl = Table(
            title="Clone Progress",
            box=box.SIMPLE_HEAVY,
            title_style="bold cyan",
            header_style="bold",
        )
        tbl.add_column("Stage", style="dim")
        tbl.add_column("State")
        tbl.add_column("Estimated", justify="right")
        tbl.add_column("Transferred", justify="right")
        tbl.add_column("Done %", justify="right")

        try:
            mon_cur = monitor_conn.cursor(dictionary=True)
            mon_cur.execute(
                "SELECT stage, state, "
                "       CAST(estimate AS UNSIGNED) AS estimate, "
                "       CAST(data AS UNSIGNED) AS data "
                "FROM performance_schema.clone_progress"
            )
            rows = mon_cur.fetchall()
            mon_cur.close()
        except mysql.connector.Error:
            return tbl  # return empty table on transient errors

        for r in rows:
            est = r["estimate"] or 0
            dat = r["data"] or 0
            pct = f"{dat / est * 100:.1f}%" if est > 0 else "—"

            est_mb = f"{est / 1048576:.1f} MB" if est > 0 else "—"
            dat_mb = f"{dat / 1048576:.1f} MB" if dat > 0 else "—"

            state_style = "green" if r["state"] == "Completed" else "yellow"
            tbl.add_row(
                r["stage"],
                f"[{state_style}]{r['state']}[/]",
                est_mb,
                dat_mb,
                pct,
            )
        return tbl

    # Poll progress until the clone thread finishes or the connection drops
    if monitor_conn:
        try:
            with Live(console=console, refresh_per_second=1) as live:
                while not clone_result["done"]:
                    live.update(_build_progress_table())
                    time.sleep(2)
                # One final refresh
                live.update(_build_progress_table())
        except Exception:
            pass  # connection lost during server restart — expected
        finally:
            try:
                monitor_conn.close()
            except Exception:
                pass
    else:
        # No monitor connection — just wait
        clone_thread.join()

    # Wait for the thread to finish
    clone_thread.join(timeout=10)

    # Check for errors from the clone thread
    if clone_result["error"]:
        err = clone_result["error"]
        # Error 3707: the server restarts after a successful clone.
        # Error 2013: Lost connection (server restart).
        if err.errno in (3707, 2013):
            console.print("[dim]Server is restarting after clone …[/]")
        else:
            console.print(f"[bold red]✗[/] Clone failed: {err}")
            sys.exit(1)

    cur.close()
    console.print("[bold green]✔[/] Clone completed successfully.")


def wait_for_server(host: str, port: int, admin_user: str, admin_password: str,
                    retries: int = 30, delay: int = 5):
    """Wait for a MySQL server to become available after a restart."""
    root_user, root_password = admin_user, admin_password

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Waiting for {host} to come back online…")
        for attempt in range(1, retries + 1):
            try:
                conn = mysql.connector.connect(
                    host=host, user=root_user, password=root_password,
                    port=port, connection_timeout=5,
                )
                conn.ping(reconnect=False)
                progress.update(task, completed=True,
                                description=f"{host} is online (attempt {attempt})")
                return conn
            except mysql.connector.Error:
                time.sleep(delay)

    console.print(f"[bold red]✗[/] Timed out waiting for [cyan]{host}[/] "
                   f"after {retries * delay}s.")
    sys.exit(1)


# ── Step 3 — Configure & start replication ──────────────────────────────────

def get_master_gtid_status(master_conn) -> str:
    """Retrieve the current GTID executed set from the master."""
    cur = master_conn.cursor(dictionary=True)
    cur.execute("SELECT @@gtid_mode AS gtid_mode, @@global.gtid_executed AS gtid_executed")
    row = cur.fetchone()
    cur.close()

    if row["gtid_mode"] != "ON":
        console.print("[bold red]✗[/] GTID mode is not ON on the master. "
                       "This tool requires GTID-based replication.")
        sys.exit(1)

    return row["gtid_executed"]


def configure_replication(replica_conn, master_host: str, master_port: int):
    """Set up the replication channel on the replica pointing to the master."""
    repl_user, repl_password = get_repl_creds()
    port = master_port

    cur = replica_conn.cursor()

    console.print(f"\n[dim]Configuring replication channel → {master_host}:{port}[/]")

    cur.execute(
        "CHANGE REPLICATION SOURCE TO "
        "SOURCE_HOST = %s, "
        "SOURCE_PORT = %s, "
        "SOURCE_USER = %s, "
        "SOURCE_PASSWORD = %s, "
        "SOURCE_AUTO_POSITION = 1, "
        "GET_SOURCE_PUBLIC_KEY = 1",
        (master_host, port, repl_user, repl_password),
    )
    cur.close()
    console.print("[bold green]✔[/] Replication channel configured.")


def start_replication(replica_conn):
    """Start the replica threads and verify they are running."""
    cur = replica_conn.cursor(dictionary=True)

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Starting replication…")
        cur.execute("START REPLICA")
        time.sleep(2)  # Give the threads a moment to initialise

        cur.execute("SHOW REPLICA STATUS")
        status = cur.fetchone()
        progress.update(task, completed=True)

    cur.close()

    if status is None:
        console.print("[bold red]✗[/] SHOW REPLICA STATUS returned no rows.")
        sys.exit(1)

    io_running = status.get("Replica_IO_Running", status.get("Slave_IO_Running", ""))
    sql_running = status.get("Replica_SQL_Running", status.get("Slave_SQL_Running", ""))
    io_err = status.get("Last_IO_Error", "")
    sql_err = status.get("Last_SQL_Error", "")
    seconds_behind = status.get("Seconds_Behind_Source",
                                 status.get("Seconds_Behind_Master", "N/A"))

    # ── Result table ────────────────────────────────────────────────────
    table = Table(
        title="Replication Status",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold magenta",
        header_style="bold cyan",
    )
    table.add_column("Metric", style="dim")
    table.add_column("Value")

    io_style = "green" if io_running == "Yes" else "red"
    sql_style = "green" if sql_running == "Yes" else "red"

    table.add_row("IO Thread",       f"[{io_style}]{io_running}[/]")
    table.add_row("SQL Thread",      f"[{sql_style}]{sql_running}[/]")
    table.add_row("Seconds Behind",  str(seconds_behind))
    table.add_row("Source Host",     status.get("Source_Host",
                                                 status.get("Master_Host", "?")))
    table.add_row("Source Port",     str(status.get("Source_Port",
                                                     status.get("Master_Port", "?"))))
    table.add_row("Auto Position",   str(status.get("Auto_Position", "?")))
    table.add_row("Retrieved GTID",  status.get("Retrieved_Gtid_Set", "")[:80] or "—")
    table.add_row("Executed GTID",   status.get("Executed_Gtid_Set", "")[:80] or "—")

    if io_err:
        table.add_row("IO Error", f"[red]{io_err}[/]")
    if sql_err:
        table.add_row("SQL Error", f"[red]{sql_err}[/]")

    console.print()
    console.print(table)

    if io_running == "Yes" and sql_running == "Yes":
        console.print("\n[bold green]✔ Replication is running successfully![/]")
    else:
        console.print("\n[bold red]✗ Replication threads are NOT healthy. "
                       "Review the errors above.[/]")
        sys.exit(1)


# ── CLI ─────────────────────────────────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--master", "-m", required=True,
              help="Master (source) server in host:port format (port defaults to 3306).")
@click.option("--replica", "-r", required=True,
              help="Target replica server in host:port format (port defaults to 3306).")
@click.option("--admin-user", "-u", default=None,
              help="Privileged admin username (overrides MYSQL_ROOT_USER in .env).")
@click.option("--admin-password", "-p", default=None,
              help="Privileged admin password (overrides MYSQL_ROOT_PASSWORD in .env).")
def main(master: str, replica: str, admin_user: str | None, admin_password: str | None):
    """
    MySQL Replication Manager — set up a replica from a running master.

    \b
    Servers are specified as host:port (port defaults to 3306 if omitted).

    \b
    Workflow:
      1. Verify server topology & ensure the replica is idle
      2. RESET REPLICA ALL → CLONE from master
      3. CHANGE REPLICATION SOURCE → START REPLICA
    """

    # ── Parse host:port ─────────────────────────────────────────────────
    master_host, master_port = parse_host_port(master)
    replica_host, replica_port = parse_host_port(replica)

    master_label = f"{master_host}:{master_port}"
    replica_label = f"{replica_host}:{replica_port}"

    # ── Banner ──────────────────────────────────────────────────────────
    console.print(Panel(
        "[bold white]MySQL Replication Manager[/]\n"
        "[dim]Clone & replicate in one shot[/]",
        border_style="bright_magenta",
        expand=False,
    ))

    # CLI flags override .env credentials
    if admin_user and admin_password:
        root_user, root_password = admin_user, admin_password
    elif admin_user or admin_password:
        console.print("[bold red]✗[/] Both --admin-user and --admin-password must be "
                       "provided together.")
        sys.exit(1)
    else:
        root_user, root_password = get_root_creds()

    # ── Connect to both servers ─────────────────────────────────────────
    console.print(f"\n[dim]Connecting to master [cyan]{master_label}[/] …[/]")
    master_conn = connect(master_host, master_port, root_user, root_password)

    console.print(f"[dim]Connecting to replica [cyan]{replica_label}[/] …[/]")
    replica_conn = connect(replica_host, replica_port, root_user, root_password)

    # ── Pre-flight — Ensure users exist on master ───────────────────────
    ensure_users(master_conn, master_label)

    # ── Step 1 — Verify ────────────────────────────────────────────────
    verify_servers(master_conn, replica_conn, master_label, replica_label)

    # ── Step 2 — Reset & Clone ──────────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold]Phase 2 — Reset Replica & Clone Data[/]",
        border_style="cyan",
        expand=False,
    ))

    reset_replica(replica_conn)

    clone_from_master(replica_conn, master_host, master_port)

    # After clone the replica restarts — reconnect
    console.print()
    replica_conn = wait_for_server(replica_host, replica_port, root_user, root_password)

    # ── Step 3 — Configure & Start ──────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold]Phase 3 — Configure & Start Replication[/]",
        border_style="cyan",
        expand=False,
    ))

    configure_replication(replica_conn, master_host, master_port)
    start_replication(replica_conn)

    # ── Cleanup ─────────────────────────────────────────────────────────
    master_conn.close()
    replica_conn.close()

    console.print(Panel(
        "[bold green]✔  All done![/]\n"
        f"[dim]{replica_label}[/] is now replicating from [dim]{master_label}[/].",
        border_style="green",
        expand=False,
    ))


if __name__ == "__main__":
    main()
