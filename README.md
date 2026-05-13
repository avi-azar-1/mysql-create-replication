# MySQL Replication Manager

A CLI tool that automates MySQL replication setup by cloning data from a master
(source) server to a replica using the **MySQL Clone Plugin**, then configuring
and starting GTID-based replication — all in one command.

## Prerequisites

| Requirement | Details |
|---|---|
| **MySQL 8.0+** | Both master and replica |
| **Clone Plugin** | Installed on master (`INSTALL PLUGIN clone SONAME 'mysql_clone.so';`) — auto-installed on replica if missing |
| **GTID mode** | Enabled on both servers (`gtid_mode=ON`, `enforce_gtid_consistency=ON`) |
| **my.cnf** | Already configured for replication (`server-id`, `log_bin`, etc.) |
| **Network** | Replica can reach master on the MySQL port |

> **Note:** The tool will automatically:
> - Remove `component_validate_password` if installed (avoids password policy errors)
> - Install the clone plugin on the replica if not already present
> - Create the clone and replication users on the master if they don't exist

## Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd mysql-create-replication

# 2. Create a virtual environment (recommended)
python -m venv .venv && .venv\Scripts\activate   # Windows
# python3 -m venv .venv && source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
copy .env.example .env
# Edit .env with your actual credentials
```

## Usage

```bash
python mysql_replication.py --master <host:port> --replica <host:port> [OPTIONS]
```

Servers are specified in `host:port` format. Port defaults to **3306** if omitted.

### Options

| Flag | Short | Default | Description |
|---|---|---|---|
| `--master` | `-m` | *(required)* | Master (source) server as `host:port` |
| `--replica` | `-r` | *(required)* | Target replica server as `host:port` |
| `--admin-user` | `-u` | from `.env` | Privileged admin username (overrides `MYSQL_ROOT_USER`) |
| `--admin-password` | `-p` | from `.env` | Privileged admin password (overrides `MYSQL_ROOT_PASSWORD`) |
| `--delay` | `-d` | `0` | Replication delay in seconds (`SOURCE_DELAY`) |
| `--help` | `-h` | | Show help message |

> `--admin-user` and `--admin-password` must be provided together.
> If neither is specified, the tool falls back to `.env` values.

### Examples

```bash
# Standard replication, admin creds from .env
python mysql_replication.py -m db-master-01 -r db-replica-02

# Custom ports
python mysql_replication.py -m db-master-01:3307 -r db-replica-02:3308

# Override admin credentials via CLI
python mysql_replication.py -m db-master-01 -r db-replica-02 -u dba_admin -p secret123

# Create a delayed replica (1 hour lag)
python mysql_replication.py -m db-master-01 -r db-replica-02 -d 3600

# Combined: custom port, custom admin, delayed
python mysql_replication.py -m db-master-01:3307 -r db-replica-02:3308 -u dba_admin -p secret123 -d 1800
```

## What It Does

```
Pre-flight — User Provisioning
  ├─ Remove component_validate_password (if installed)
  ├─ Ensure clone user exists on master (create + BACKUP_ADMIN if missing)
  └─ Ensure replication user exists on master (create + REPLICATION SLAVE,
     REPLICATION CLIENT if missing)

Phase 1 — Verify
  ├─ Display server topology (hostname, version, server-id, UUID, GTID mode, …)
  ├─ Check server-id is unique between master and replica (fail if equal)
  ├─ Check GTID mode is ON on both servers (fail if not)
  ├─ Check the replica for active connections & running queries
  └─ Prompt for confirmation (destructive operation warning)

Phase 2 — Reset & Clone
  ├─ STOP REPLICA  /  RESET REPLICA ALL
  ├─ Install clone plugin on replica (if not already installed)
  ├─ CLONE INSTANCE FROM master
  │    └─ Live progress table (per-stage: state, estimated MB, transferred MB, %)
  └─ Wait for replica to restart after clone

Phase 3 — Configure & Start Replication
  ├─ CHANGE REPLICATION SOURCE TO … SOURCE_AUTO_POSITION = 1
  │    └─ SOURCE_DELAY = N  (if --delay is specified)
  ├─ START REPLICA
  └─ Display replication health (IO/SQL threads, lag, SQL delay, GTID sets)
```

## Configuration

Credentials are read from a `.env` file in the project root.
The admin user can optionally be overridden via CLI flags.

| Variable | Purpose |
|---|---|
| `MYSQL_ROOT_USER` | Admin user for connecting to both servers (overridden by `--admin-user`) |
| `MYSQL_ROOT_PASSWORD` | Admin password (overridden by `--admin-password`) |
| `MYSQL_CLONE_USER` | Clone user — created automatically on master if missing |
| `MYSQL_CLONE_PASSWORD` | Clone user password |
| `MYSQL_REPL_USER` | Replication user — created automatically on master if missing |
| `MYSQL_REPL_PASSWORD` | Replication user password |

## License

MIT
